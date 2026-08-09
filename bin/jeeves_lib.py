#!/usr/bin/env python3
"""Shared plumbing for the jeeves pipeline. Stdlib only."""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from unicodedata import normalize as unicodedata_normalize
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

CONFIG_DEFAULTS = {
    "model": "",
    # Free Zen routes go through recurring infra-strain spells where every
    # dispatch dies with no_output_timeout and 0 bytes written (documented in
    # choosing-a-model's working-report). One paid retry beats losing the day's
    # digest; the working report's own guidance is to stop retrying the free
    # route after 1-2 attempts and switch routes.
    "fallback_model": "minimax/MiniMax-M3",
    "trivial_min": 4,
    # Hours a transcript may sit untouched before jeeves stops waiting
    # for it to grow and extracts whatever it already holds.
    "carry_max_h": 24,
    "batch_max": 4,
    "batch_under": 10,
    "truncate": 800,
}


def now_et() -> datetime:
    return datetime.now(ET)


def today_et() -> str:
    return now_et().date().isoformat()


def state_dir() -> Path:
    d = Path(
        os.environ.get("JEEVES_STATE_DIR")
        or Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "jeeves"
    )
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def data_dir() -> Path:
    d = Path(
        os.environ.get("JEEVES_DATA_DIR")
        or Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "jeeves"
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def projects_root() -> Path:
    return Path(os.environ.get("JEEVES_PROJECTS_ROOT", "~/.claude/projects")).expanduser()


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    f = state_dir() / "config"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = [s.strip() for s in line.split("=", 1)]
            cfg[k] = int(v) if k in cfg and isinstance(cfg[k], int) else v
    return cfg


def log(msg: str) -> None:
    f = state_dir() / "collect.log"
    with f.open("a") as fh:
        fh.write(f"{now_et().isoformat(timespec='seconds')} {msg}\n")


def die(msg: str) -> NoReturn:
    """Fail fast and loud."""
    log(f"FATAL {msg}")
    print(f"jeeves: {msg}", file=sys.stderr)
    sys.exit(1)


SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def read_delta(path: Path, offset: int) -> tuple:
    size = path.stat().st_size
    status = "ok"
    if size < offset:
        offset = 0
        status = "rotated"
    with path.open("rb") as fh:
        fh.seek(offset)
        raw = fh.read()
    end = raw.rfind(b"\n")
    if end == -1:
        return [], offset, status  # no complete new line yet
    new_offset = offset + end + 1
    lines = raw[:end].decode("utf-8", "replace").split("\n")
    return lines, new_offset, status


def _entry_text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def denoise(lines, truncate: int = 800) -> list:
    """Structural field selection: keep user/assistant text, drop everything else.
    Unrecognized shapes are dropped, never passed through garbled. Strips NUL
    bytes -- a valid \\u0000 escape can decode into one, and an embedded NUL
    later crashes subprocess.run() when the rendered text becomes a taskferry
    dispatch argv string (ValueError: embedded null byte)."""
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(e, dict):
            continue
        if e.get("isSidechain") or "attachment" in e:
            continue
        msg = e.get("message")
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        text = SYSTEM_REMINDER_RE.sub("", _entry_text(msg)).replace("\x00", "").strip()
        if not text:
            continue
        out.append({"t": e.get("timestamp", "?"), "r": msg["role"], "x": text[:truncate]})
    return out


def render_slice(entries) -> str:
    return "\n".join(f"[{e['t']}] {e['r']}: {e['x']}" for e in entries)


PROVENANCE_RE = re.compile(r"\s*\(jeeves:[^)]*\)\s*$|\s*\(dismissed[^)]*\)\s*$")


def normalize(s: str) -> str:
    # Strip provenance repeatedly, not once: suffixes stack, as in
    # "... (jeeves: loose-end, x, 2026-08-09) (dismissed 2026-08-09)". Both
    # alternatives in PROVENANCE_RE are $-anchored and re.sub makes a single
    # left-to-right pass, so one sub() removed only the outermost group and
    # left the inner one attached -- which meant a dismissed ledger line never
    # compared equal to the same line quoted without its tags, and --dismiss
    # reported "no open ledger line matches" for a line plainly in the file.
    while True:
        stripped = PROVENANCE_RE.sub("", s)
        if stripped == s:
            break
        s = stripped
    s = s.lstrip("- [x] ").lstrip("- [ ] ").strip()
    s = re.sub(r"\s+", " ", s)
    return unicodedata_normalize("NFKC", s).casefold()


def line_hash(s: str) -> str:
    return hashlib.sha256(normalize(s).encode("utf-8")).hexdigest()


def parse_github_slug(url: str):
    """owner/name from a git origin URL, or None if it isn't GitHub.

    Requiring the host is what makes this safe to build an API path from. A
    tail-anchored `owner/repo` match alone accepts anything URL-shaped, so a
    filesystem origin such as `/tmp/x/origin.git` yields `x/origin` -- a
    plausible-looking slug naming a repo that does not exist, or worse, one
    that does and belongs to somebody else.

    `git remote get-url` output keeps its trailing newline, and the old
    `([^/.]+)` name class happily matched it -- producing a repo id ending in
    `\\n` that made every later API path fail with "invalid control character
    in URL". Strip first, and let the name carry dots so `foo.bar` survives.
    """
    # rstrip the slash before matching: a `.../owner/name/` origin otherwise
    # carries it into the name and 404s every path built from the id, which
    # is the same silent-drop this function exists to fix.
    m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", url.strip().rstrip("/"))
    return f"{m.group(1)}/{m.group(2)}" if m else None


class SeenStore:
    def __init__(self, path: Path):
        self.path = path
        self.rows = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.rows[r["hash"]] = r

    @classmethod
    def load(cls) -> "SeenStore":
        return cls(state_dir() / "seen.ndjson")

    def check(self, h: str):
        return self.rows.get(h)

    def upsert(self, h: str, line: str, status: str = "open") -> dict:
        now = now_et().isoformat(timespec="seconds")
        r = self.rows.get(h)
        if r is None:
            r = {"hash": h, "line": line, "first_seen": now, "last_seen": now,
                 "count": 1, "status": status}
        else:
            r["last_seen"] = now
            r["count"] += 1
            r["status"] = status
        self.rows[h] = r
        return r

    def set_status(self, h: str, status: str) -> None:
        if h in self.rows:
            self.rows[h]["status"] = status

    def by_status(self, status: str) -> list:
        return [r for r in self.rows.values() if r["status"] == status]

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(json.dumps(r) for r in self.rows.values()) + "\n")
        tmp.replace(self.path)


FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.S)


def parse_axi_message(result_text: str):
    """Extract message: "..." from taskferry result output, quote-aware."""
    m = re.search(r'^message: "', result_text, re.M)
    if not m:
        return None
    i = m.end()
    out = []
    while i < len(result_text):
        c = result_text[i]
        if c == "\\" and i + 1 < len(result_text):
            nxt = result_text[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        if c == '"':
            return "".join(out)
        out.append(c)
        i += 1
    return None  # unterminated


def parse_fenced_json(text: str):
    """Extract a JSON value from the model's reply.

    Primary path: a ```json ... ``` fence.
    Lenient fallbacks, in order:
      1. ``` fence with no language tag (``` ... ```) wrapping JSON.
      2. Unclosed ```json fence — take everything after the opening fence
         to end of message.
      3. A trailing `Status: DONE` (or similar) line often follows the
         payload; strip it before parsing.
      4. Raw JSON starting at the first `[` or `{` and ending at its
         matching closer.
    Each step tries json.loads on the candidate; returns the first that
    parses, or None if none do.
    """
    candidates = []

    # 1. closed ```json fence
    m = re.search(r"```json\s*\n(.*?)```", text, re.S)
    if m:
        candidates.append(m.group(1))

    # 2. closed fence with no language tag wrapping JSON-looking content
    if not candidates:
        m = re.search(r"```\s*\n(\[.*?\]|\{.*?\})\s*```", text, re.S)
        if m:
            candidates.append(m.group(1))

    # 3. unclosed ```json fence: from opener to end-of-message
    if not candidates:
        m = re.search(r"```json\s*\n(.*)", text, re.S)
        if m:
            candidates.append(m.group(1))

    for raw in candidates:
        # strip any trailing Status: ... line
        raw = re.sub(r"\n\s*Status:.*$", "", raw, flags=re.S).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue

    # 4. raw JSON from first [ or { to its matching closer (handles
    #    outputs with no fences at all, where Status: is tacked on after).
    for opener, closer in [("[", "]"), ("{", "}")]:
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    raw = text[start : i + 1]
                    raw = re.sub(r"\n\s*Status:.*$", "", raw, flags=re.S).strip()
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        break
    return None


def _tf(args, timeout=180, input=None):
    try:
        p = subprocess.run(
            ["taskferry", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input,
        )
        return p.returncode, p.stdout, p.stderr
    # ValueError covers subprocess.run's "embedded null byte" -- a prompt
    # argument can't reach the OS as an argv string if it still contains a
    # raw NUL despite denoise()'s stripping (e.g. a caller that bypasses
    # denoise). Treated the same as any other spawn-time failure: a failed
    # dispatch this call can report, never an exception that kills the
    # whole collect.py run over one bad slice.
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError) as e:
        return 1, "", str(e)


def task_log_message(tid: str):
    """Rebuild a task's raw model output from its taskferry NDJSON log.
    Fallback for when the daemon replaces a large result's message with a
    75-word Delta summary (summaryOf wrapper) — the full output stays on
    disk in the task's own log."""
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    f = base / "taskferry" / "logs" / f"{tid}.ndjson"
    if not f.exists():
        return None
    parts = []
    for line in f.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "text":
            parts.append(e.get("part", {}).get("text", ""))
    return "".join(parts) if parts else None


# Per-process tally of primary-route failures, keyed by model. Reset on any
# success, so a route that recovers mid-run gets used again.
_PRIMARY_FAILURES: dict = {}


def ferry(prompt: str, model: str, wait_s: int = 420, directory: str = "") -> dict:
    """Dispatch to `model`, falling back to the configured paid route once if
    the primary route fails outright (crash, timeout, empty output).

    A failed retry reports the *primary* model's error, since that's the route
    the operator pinned and the one whose health matters for diagnosis."""
    fb = load_config().get("fallback_model", "")

    # Circuit breaker: once the primary has failed twice in this run it is
    # down, not unlucky — every further attempt costs a full pre-output
    # timeout (~4.5 min) before falling back anyway. working-report.md's own
    # guidance is to stop after 1-2 retries and switch routes, so honour that
    # per-run instead of re-paying the timeout on every batch.
    if fb and fb != model and _PRIMARY_FAILURES.get(model, 0) >= 2:
        return _ferry_once(prompt, fb, wait_s, directory)

    res = _ferry_once(prompt, model, wait_s, directory)
    if res["ok"]:
        _PRIMARY_FAILURES.pop(model, None)
        return res
    _PRIMARY_FAILURES[model] = _PRIMARY_FAILURES.get(model, 0) + 1
    if not fb or fb == model:
        return res
    if _PRIMARY_FAILURES[model] == 2:
        log(f"ferry: {model} failed twice; routing the rest of this run to {fb}")
    log(f"ferry: {model} failed ({res['error']}); retrying on {fb}")
    alt = _ferry_once(prompt, fb, wait_s, directory)
    if alt["ok"]:
        log(f"ferry: fallback {fb} succeeded")
        return alt
    log(f"ferry: fallback {fb} also failed ({alt['error']})")
    # Report both routes. Returning only the primary's error made the caller's
    # summary line blame a timeout when the fallback had actually failed for a
    # different reason entirely — the same misattribution this status check
    # exists to prevent.
    return {**res, "error": f"{model}: {res['error']}; {fb}: {alt['error']}"}


def _ferry_once(prompt: str, model: str, wait_s: int = 420, directory: str = "") -> dict:
    def fail(err, tid=""):
        return {"ok": False, "message": "", "task_id": tid, "error": err}

    if not model:
        return fail("no model pinned in config")
    # `--prompt -` reads from stdin, bypassing argv length limits that
    # would otherwise crash with [Errno 7] Argument list too long when
    # the synthesis prompt is large.
    args = ["dispatch", "--prompt", "-", "--model", model]
    if directory:
        # --no-overlay: the ferry gets ungated read-write access to whatever
        # `directory` holds, with no accept/reject gate on what it writes
        # back. That's only acceptable because every caller in this codebase
        # passes collect.py's per-run staging subdir and nothing else — never
        # a real project directory. The summary files need to land directly
        # (that's the entire point: skip the accept/reject round-trip an
        # overlay would need to extract them), and the input side is a
        # scratch copy jeeves made for this dispatch, not a live source of
        # truth. The blast radius of the ungated rw is bounded by what's
        # already true of that directory on the collect.py side: contents are
        # redacted before they're staged (`redact()`), the subdir is
        # chmod 0700, and it's deleted the moment this dispatch returns
        # (success or failure) rather than left for the next run's TTL
        # prune. If a future caller ever wants to pass something other than
        # a disposable staging dir here, that's the point to revisit this,
        # not before.
        args += ["--directory", directory, "--no-overlay"]
    rc, out, err = _tf(args, input=prompt)
    m = re.search(r"oc_[a-z0-9_]+", out)
    if rc != 0 or not m:
        return fail(f"dispatch failed: {err or out}")
    tid = m.group(0)
    _tf(["wait", tid, "--timeout", f"{wait_s * 1000}"], timeout=wait_s + 60)
    rc, out, err = _tf(["result", tid])
    if rc != 0:
        return fail(f"result failed: {err or out}", tid)
    # A task that never produced output still returns rc 0 with message: "",
    # which parses to an empty string rather than None. Without an explicit
    # status check that reads as success, and the caller misreports a crashed
    # ferry as unparseable model output. Fail on the real reason instead.
    st = re.search(r"^status: (\w+)", out, re.M)
    status = st.group(1) if st else ""
    if status and status != "done":
        why = re.search(r"^failureDetail: (.+)$", out, re.M) or \
              re.search(r"^failureReason: (.+)$", out, re.M)
        return fail(f"task {status}: {why.group(1).strip() if why else 'no detail'}", tid)

    msg = parse_axi_message(out)
    if msg is None:
        return fail("could not read message from result output", tid)
    if not msg.strip():
        # `incomplete: true` is taskferry's flag for "the final message was
        # empty, or didn't match the task's finalMarker" — see
        # evaluateOutputCompleteness in taskferry's src/tasks.js. It says
        # nothing about token budgets. The usual cause is a worker that spent
        # its whole turn on tool calls and reasoning without ever emitting a
        # text block (`taskferry tail` shows textTotalChars: 0), which is a
        # different problem from a crash and from a genuinely empty answer.
        if re.search(r"^incomplete: true", out, re.M):
            return fail("task settled with no final message (taskferry: incomplete); "
                        "the worker likely never emitted a text block — check "
                        "`taskferry tail <id>` for textTotalChars", tid)
        return fail(f"task reported {status or 'unknown status'} but produced an empty message", tid)
    if msg.startswith("**Delta:**") or "summaryOf:" in out:
        # daemon replaced a large result with a summary wrapper — rebuild
        # the raw model output from the task's own log
        rebuilt = task_log_message(tid)
        if rebuilt:
            log(f"ferry {tid}: result was daemon-summarized; rebuilt from task log")
            msg = rebuilt
        else:
            return fail("result daemon-summarized and task log unreadable", tid)
    return {"ok": True, "message": msg, "task_id": tid, "error": ""}
