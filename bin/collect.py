#!/usr/bin/env python3
"""jeeves cron entrypoint: collect transcript deltas, extract via ferries,
synthesize the daily digest, apply todo mutations. Stdlib only."""
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import jeeves_lib as jl
import todos as td

PROMPTS = Path(__file__).parent.parent / "prompts"


def offsets_load() -> dict:
    f = jl.state_dir() / "offsets.tsv"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        if line.strip():
            p, off, size = line.split("\t")
            out[p] = {"offset": int(off), "size": int(size)}
    return out


def offsets_save(offs: dict) -> None:
    f = jl.state_dir() / "offsets.tsv"
    tmp = f.with_suffix(".tmp")
    tmp.write_text("".join(f"{p}\t{v['offset']}\t{v['size']}\n" for p, v in offs.items()))
    tmp.replace(f)


def discover_sessions():
    return sorted(jl.projects_root().glob("*/*.jsonl"))


def session_id_of(path: Path) -> str:
    return path.stem


def _is_dead(path: Path, cfg: dict) -> bool:
    """Has this transcript stopped growing? Held-back content is only worth
    waiting on while the session might still add to it."""
    try:
        age_h = (time.time() - path.stat().st_mtime) / 3600
    except OSError:
        return True
    return age_h >= cfg["carry_max_h"]


def group_slices(slices, batch_under, batch_max):
    solo, small = [], defaultdict(list)
    for s in slices:
        if len(s["entries"]) < batch_under:
            small[s["dir"]].append(s)
        else:
            solo.append([s])
    groups = list(solo)
    for dir_slices in small.values():
        for i in range(0, len(dir_slices), batch_max):
            groups.append(dir_slices[i:i + batch_max])
    return groups


def _gh_json(args, timeout=45):
    # Plain `gh` (not gh-axi) because the collector needs --jq/JSON-structured
    # output and gh-axi api renders YAML with no --jq flag — the sanctioned
    # fallback per CLAUDE.md's gh-axi-first rule.
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            return None
        return json.loads(p.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
        return None


GH_SIGNAL_REASONS = {"review-requested", "mention", "team-mention", "comment",
                     "assign", "author"}


# Shared with todos.py, which needs the same parse to build a `-R owner/repo`
# and an API path. Two copies of it had already drifted on which URL shapes
# they accept, so the one that decides evidence lives in jeeves_lib.
_parse_origin = jl.parse_github_slug


_ORIGINS: dict = {}


def _repo_origins() -> list:
    """(local dir, owner/name) for each project dir with a GitHub origin.

    Memoized per state dir: this shells out `git remote get-url` once per
    project dir, and both callers (`gh_review` and `_flag_unverified_refs`)
    want the same answer in the same run — 132 dirs meant 264 spawns a run
    to compute one list twice."""
    dirs_file = jl.state_dir() / "project-dirs.txt"
    if str(dirs_file) in _ORIGINS:
        return _ORIGINS[str(dirs_file)]
    if not dirs_file.exists():
        return []
    out, seen = [], set()
    for d in dirs_file.read_text().splitlines():
        if not d.strip():
            continue
        try:
            url = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"],
                                 capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        if url.returncode != 0:
            continue
        full = _parse_origin(url.stdout)
        if full and full not in seen:
            seen.add(full)
            out.append((d, full))
    _ORIGINS[str(dirs_file)] = out
    return out


def _github_repos() -> list:
    """Distinct GitHub owner/name pairs behind the project dirs, in order."""
    return [full for _, full in _repo_origins()][:12]


def gh_review() -> str:
    """Live GitHub obligations feed for the synthesis ferry: human-signal
    notifications, open PRs split mine/external, recent issues."""
    user = _gh_json(["api", "user"])
    if not isinstance(user, dict) or not user.get("login"):
        jl.log("gh review: auth failed or unavailable")
        return "(github unavailable: gh auth failed)"
    login = user["login"]
    out = [f"(authenticated as {login})"]

    notifs = _gh_json(["api", "notifications"])
    if isinstance(notifs, list):
        signal, noise = [], 0
        for n in notifs:
            if n.get("reason") in GH_SIGNAL_REASONS:
                num = str(n.get("subject", {}).get("url", "")).rsplit("/", 1)[-1]
                signal.append(f"- [{n['reason']}] {n['subject'].get('type')} "
                              f"{n['repository']['full_name']}#{num}: "
                              f"{n['subject'].get('title', '')[:80]}")
            else:
                noise += 1
        out.append("\nNEEDS A RESPONSE (unread notifications, human signals):")
        out.extend(signal[:25] or ["(none)"])
        if noise:
            out.append(f"(plus {noise} unread CI/state notifications suppressed)")
    else:
        out.append("\nNEEDS A RESPONSE:\n(notifications unavailable)")

    for full in _github_repos():
        pulls = _gh_json(["api", f"repos/{full}/pulls?state=open&per_page=30"])
        issues = None
        since = (jl.now_et() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
        issues = _gh_json(["api", f"repos/{full}/issues?state=open&since={since}&per_page=50"])
        pr_lines, iss_lines = [], []
        if isinstance(pulls, list):
            for pr in pulls[:15]:
                who = pr.get("user", {}).get("login", "?")
                tag = " (EXTERNAL)" if who != login else ""
                draft = " [draft]" if pr.get("draft") else ""
                pr_lines.append(f"- #{pr['number']} by {who}{tag}{draft} "
                                f"{str(pr.get('updated_at', ''))[:10]}: "
                                f"{pr.get('title', '')[:80]}")
        if isinstance(issues, list):
            real = [i for i in issues if "pull_request" not in i]
            for i in real[:12]:
                who = i.get("user", {}).get("login", "?")
                tag = " (EXTERNAL)" if who != login else ""
                iss_lines.append(f"- #{i['number']} by {who}{tag} "
                                 f"{str(i.get('created_at', ''))[:10]}, "
                                 f"{i.get('comments', 0)} comments: "
                                 f"{i.get('title', '')[:80]}")
        if pr_lines or iss_lines:
            out.append(f"\n{full}:")
            if pr_lines:
                out.append(" open PRs:")
                out.extend(pr_lines)
            if iss_lines:
                out.append(" recent issues (updated last 7 days):")
                out.extend(iss_lines)
    return "\n".join(out)


def tf_diff() -> str:
    """Diff taskferry list against last snapshot; return human notes."""
    try:
        p = subprocess.run(["taskferry", "list", "--all", "--limit", "40"],
                           capture_output=True, text=True, timeout=60)
        current = p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        jl.log(f"tf diff skipped: {e}")
        return "(taskferry list unavailable)"
    f = jl.state_dir() / "tf-state.json"
    prev = json.loads(f.read_text()) if f.exists() else {"lines": []}
    cur_lines = {line.strip() for line in current.splitlines() if "oc_" in line}
    prev_lines = set(prev.get("lines", []))
    new = sorted(cur_lines - prev_lines)
    f.write_text(json.dumps({"lines": sorted(cur_lines)}))
    return "\n".join(new) if new else "(no new task-queue activity)"


def git_state() -> str:
    """Cross-repo branch/commit state via orient's scan-active.sh (absorbed
    from the retired orient-global skill), persisted to state so invocation-
    time reads stay local-only. Honest about failure rather than skipping."""
    scan = Path.home() / ".claude/skills/orient/bin/scan-active.sh"
    since = os.environ.get("SINCE", "yesterday 00:00")
    out = "(git scan unavailable: scan-active.sh missing)"
    if scan.exists():
        try:
            p = subprocess.run(["bash", str(scan), since],
                               capture_output=True, text=True, timeout=180)
            out = (p.stdout.strip()
                   or f"(git scan exited {p.returncode} with no output)")
        except subprocess.TimeoutExpired:
            jl.log("git scan timed out")
            out = "(git scan unavailable: timed out)"
        except OSError as e:
            jl.log(f"git scan failed: {e}")
            out = f"(git scan unavailable: {e})"
    if not out.startswith("(git scan"):
        stamp = jl.now_et().isoformat(timespec="seconds")
        out = (f"(git scan taken {stamp} ET; commit ages below are "
               f"relative to this moment)\n{out}")
    (jl.state_dir() / "git-state.md").write_text(out + "\n")
    return out


def extract_prompt(group) -> str:
    template = (PROMPTS / "extract-rubric.md").read_text()
    blocks = []
    for s in group:
        # Denoised prose is far less likely to carry a raw secret than the
        # staged path's unfiltered tool output, but it's not impossible (a
        # user pasting a token into chat, an error message echoing one) —
        # this path gets none of the staged path's redaction otherwise.
        blocks.append(f"[SESSION {s['sid']} — project {Path(s['dir']).name}]\n"
                      f"{redact(s['rendered'])}")
    return template.replace("{slices_block}", "\n\n---\n\n".join(blocks))


def staging_root() -> Path:
    d = jl.state_dir() / "staging"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def prune_staging(keep_h: int = 6) -> int:
    """Staged slices are verbatim transcript copies. Never leave them lying
    around past the run that needed them."""
    cutoff = time.time() - keep_h * 3600
    n = 0
    for d in staging_root().iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            if d.exists():
                jl.log(f"staging prune left {d} behind (rmtree failed)")
            else:
                n += 1
    return n


# Staging widens what reaches the model from denoised prose to the whole
# delta, tool results included — and a tool result is where a secret actually
# shows up (an `env` dump, a curl with a bearer token, a cat of a .env). Prose
# rarely carries one; command output does. Redact before anything leaves the
# host, not after.
SECRET_RES = [
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,})"),
    re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})"),
    # `xox[a-z]` rather than a fixed letter class, so a Slack token family
    # added later — `xoxe-` (workspace token), the `xoxe.xoxp-` compound
    # refresh-token shape — is covered without another edit here.
    re.compile(r"\b(xox[a-z](?:\.[a-z]+)?-[A-Za-z0-9-]{10,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
    re.compile(r"\b(AIza[0-9A-Za-z_-]{30,})"),
    # No floor beyond a handful of chars: the keyword itself ("bearer"/
    # "basic") is specific enough that a short base64 credential right after
    # it (Basic auth of a short `user:pass`, e.g. `Basic dTpw`) is still
    # worth redacting more than the false-positive risk of an unrelated
    # short word following those two keywords is worth avoiding. The floor
    # counts the whole credential including its `=` padding, not the base64
    # characters alone — a padded short `user:pass` like `OnA=` or `YQ==`
    # is 4 chars only once the padding is counted.
    re.compile(r"(?i)\b((?:bearer|basic)\s+)"
               r"(?:[A-Za-z0-9._~+/-]{4,}={0,2}|[A-Za-z0-9._~+/-]{2}={2}"
               r"|[A-Za-z0-9._~+/-]{3}={1,2})"),
    # PEM/SSH private key blocks — the BEGIN/END markers alone are signal
    # enough to redact the whole thing, body included. Two separate patterns:
    # the `... PRIVATE KEY` family (RSA/EC/ECDSA/ED25519/OPENSSH/DSA/
    # ENCRYPTED/unlabeled) shares a `-----END <label> PRIVATE KEY-----`
    # marker; PGP's block has a different suffix (`PRIVATE KEY BLOCK`, no
    # bare "PRIVATE KEY" marker).
    re.compile(r"-----BEGIN ((?:RSA |EC |ECDSA |ED25519 |OPENSSH |DSA |ENCRYPTED |)"
               r"PRIVATE KEY)-----[\s\S]*?-----END \1-----"),
    re.compile(r"-----BEGIN (PGP PRIVATE KEY BLOCK)-----"
               r"[\s\S]*?-----END \1-----"),
    # Fallback for a key body that never reaches its own `-----END` marker —
    # the prose-fallback path's 800-char denoise truncation can cut a pasted
    # key mid-body. The two patterns above already consumed every BEGIN...END
    # pair by the time this runs, so any `-----BEGIN...PRIVATE KEY-----`
    # still standing here is exactly the truncated case. Redact the header
    # plus a bounded base64 body rather than to end-of-file: the body is
    # limited to base64 characters, whitespace (line-wrapped base64), and
    # escaped-newline backslashes (the staged JSONL form), capped at a
    # generous fixed length — one truncated key can no longer wipe every
    # subsequent staged line.
    re.compile(r"-----BEGIN (?:(?:RSA |EC |ECDSA |ED25519 |OPENSSH |DSA |ENCRYPTED |)"
               r"PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----"
               r"[A-Za-z0-9+/=\s\\]{0,8192}"),
    # `scheme://user:pass@host` connection strings (DATABASE_URL, a raw
    # psql/mysql DSN). Only the password half is masked; scheme/user stay for
    # context. The user segment is optional (`redis://:pass@host` is a valid,
    # common DSN shape with no username) but bans `/` — without that ban the
    # pattern also fired on plain-URL path shapes like `example.com/path:8080@`
    # and destroyed path/port content. The password group allows `/` (a
    # password itself can contain one) and `@` (greedy, so it backtracks to
    # the rightmost `@` before the lookahead succeeds) — a password containing
    # its own `@` used to truncate the match at the first one, leaking the
    # remainder. It is escape-aware: a `\` plus any char (a JSON-escaped quote
    # or backslash inside the password) counts as one password character, so an
    # escaped quote no longer makes the `(?=@)` lookahead fail and leak the
    # whole password.
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/@:\"']*:)((?:\\.|[^\s\"'\\])+)(?=@)"),
    # No `\b` in front: `_` is a word character, so a boundary would never
    # match the `API_KEY` inside `OPENAI_API_KEY`, which is exactly the shape
    # these turn up in. The key is `[\w.-]*<keyword>[\w.-]*` rather than the
    # bare keyword, so `AWS_SECRET_ACCESS_KEY`, `_authToken`, and
    # `PGPASSWORD`/`MYSQL_PWD` all match too, not just an exact
    # `secret`/`token`/`password`. `(?!(?-i:[a-z]))` right after the keyword
    # blocks the mirror-image false positive — a keyword that's really the
    # start of an unrelated lowercase word (`Author:`, `tokenizer=`) — while
    # still allowing a real compound continuation, including a camelCase one
    # (`_authToken`'s `T`, `_ACCESS_KEY`'s `_`, `secretKey`'s `K`) or the
    # keyword standing alone. The `(?-i:...)` scopes case-sensitivity to just
    # this lookahead despite the pattern's own `(?i)` flag — under plain
    # `(?i)`, `[a-z]` also matches uppercase, so a camelCase key's uppercase
    # continuation (`secretKey`'s `K`) would wrongly fail the lookahead and
    # kill the whole match right after the keyword, leaking the key=value
    # entirely. `pwd` alone (bare `PWD=`, the shell env var) still matches on
    # purpose: a missed real credential costs more than an over-redacted
    # directory path. Quotes allow an optional leading `\` — `stage_slice`
    # writes raw JSON-escaped bytes, so a quoted value on disk reads
    # `\"value\"`, not `"value"`. A quoted value is matched escape-aware and
    # stops at its closing quote when a JSON delimiter (`,`/`}`/`]`/
    # whitespace/end) follows: an embedded `\"` inside the secret is consumed
    # as an escaped pair, and an adjacent `"key":"value"` pair keeps its own
    # key instead of the value group running to whitespace and swallowing the
    # comma plus the next key, which leaked the second value. Unquoted values
    # still run to whitespace. No floor on the value length: a short real
    # secret is worth redacting more than a floor is worth avoiding one false
    # positive.
    re.compile(r"(?i)(?<![A-Za-z0-9])([\w.-]*(?:api[_-]?key|secret|token|password"
               r"|passwd|pwd|credential|auth)(?!(?-i:[a-z]))[\w.-]*\\?[\"']?\s*[:=]\s*\\?[\"']?)"
               r"((?:\\.|[^\\\"])*?(?=\\?[\"'](?=\s*[,}\]]|$))|\S+)"),
]


def redact(text: str) -> str:
    """Mask credential-shaped strings in staged transcript content."""
    for rx in SECRET_RES:
        text = rx.sub(lambda m: m.group(1) + "[REDACTED]"
                      if rx.groups > 1 else "[REDACTED]", text)
    return text


def stage_slice(staging: Path, s: dict) -> Path:
    """Write a session's raw delta lines for the ferry to read directly.

    Otherwise unfiltered on purpose: the whole point is that tool calls, tool
    results, and sidechain entries reach the model, and `denoise` drops all
    three."""
    f = staging / f"{s['slug']}--{s['sid']}.jsonl"
    f.write_text(redact("\n".join(s["raw"])) + "\n")
    f.chmod(0o600)
    return f


def extract_tools_prompt(group, staging: Path) -> str:
    template = (PROMPTS / "extract-tools.md").read_text()
    rows = []
    for s in group:
        f = f"{s['slug']}--{s['sid']}.jsonl"
        rows.append(f"- session `{s['sid']}` (project {Path(s['dir']).name}): "
                    f"`{f}` — write findings to `summary-{s['sid']}.json`")
    return template.replace("{files_block}", "\n".join(rows))


# The extraction schema from prompts/extract-tools.md. Used only for a
# minimal shape check on what the ferry wrote — not full validation.
_SUMMARY_FIELDS = {"session", "shipped", "oversaw", "loose_ends", "tangents",
                   "overlooked", "failures", "shape"}
_SUMMARY_LIST_FIELDS = _SUMMARY_FIELDS - {"session", "shape"}


def _looks_like_summary(payload: dict) -> bool:
    """Reject a JSON object that happens to parse but isn't shaped like the
    extraction schema — an empty `{}`, or an unrelated blob (a mutation dict,
    a stray tool-output object) shouldn't get written into the ledger as if
    it were a real session summary. Checked against `_SUMMARY_LIST_FIELDS`
    (not the full field set) so a stub carrying only `session`/`shape` — the
    two non-list fields — and nothing else doesn't pass: there'd be nothing
    left for the `isinstance(..., list)` check below to actually typecheck,
    the same failure mode an all-`session` intersection has."""
    if not _SUMMARY_LIST_FIELDS & payload.keys():
        return False
    return all(isinstance(payload[k], list)
               for k in _SUMMARY_LIST_FIELDS if k in payload)


def read_staged_summaries(staging: Path, group) -> dict:
    """Collect per-session summary files the ferry wrote. A file that never
    appeared is absent from the result, never a stub — the caller decides
    whether to hold the offset for a retry."""
    out = {}
    for s in group:
        f = staging / f"summary-{s['sid']}.json"
        if f.is_symlink():
            # The ferry has rw access to this directory (--no-overlay); a
            # symlink here could point the expected filename at a sensitive
            # local file instead of a summary it actually wrote.
            jl.log(f"staged summary for {s['sid']} is a symlink; refusing to read it")
            continue
        if not f.exists():
            continue
        try:
            payload = json.loads(f.read_text())
        except (json.JSONDecodeError, ValueError, OSError) as e:
            jl.log(f"staged summary unreadable for {s['sid']}: {e}")
            continue
        if isinstance(payload, list):  # a one-entry array is close enough
            list_len = len(payload)
            payload = payload[0] if list_len == 1 else None
            if payload is not None and not isinstance(payload, dict):
                jl.log(f"staged summary for {s['sid']} is a one-item list of "
                       f"a non-dict ({type(payload).__name__}); skipped")
                continue
            if payload is None:
                jl.log(f"staged summary for {s['sid']} is a list of length "
                       f"{list_len}, expected exactly one; skipped")
                continue
        if not isinstance(payload, dict):
            continue
        own_sid = payload.get("session")
        if own_sid is not None and own_sid != s["sid"]:
            # The filename says one sid, the payload's own `"session"` field
            # says another — a mislabeled or swapped-content file. Silently
            # relabeling it to the filename's sid (the old behavior) hides a
            # real mismatch instead of surfacing it.
            jl.log(f"staged summary filename says {s['sid']} but payload's "
                   f"own \"session\" field says {own_sid!r}; skipped")
        elif _looks_like_summary(payload):
            out[s["sid"]] = {**payload, "session": s["sid"]}
        else:
            jl.log(f"staged summary for {s['sid']} doesn't look like the "
                   f"extraction schema; skipped")
    return out


def write_summary(date, slug, sid, n, payload: dict) -> None:
    d = jl.state_dir() / "summaries" / date
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{slug}--{sid}--{n}.md"
    f.write_text(json.dumps(payload, indent=1))


def synthesis_prompt(date, github_block, git_state_block) -> str:
    template = (PROMPTS / "synthesis.md").read_text()
    summaries = []
    sd = jl.state_dir() / "summaries" / date
    if sd.is_dir():
        for f in sorted(sd.glob("*.md")):
            summaries.append(f"## {f.stem}\n```json\n{f.read_text()}\n```")
    ledger = "\n".join(td.parse_ledger(td.ledger_path().read_text())["open"]) or "(none)"
    return (template
            .replace("{date}", date)
            .replace("{summaries_block}", "\n\n".join(summaries) or "(none)")
            .replace("{open_ledger_block}", ledger)
            .replace("{tf_notes_block}", tf_diff())
            .replace("{github_block}", github_block)
            .replace("{git_state_block}", git_state_block))


REF_RE = re.compile(r"#(\d+)\b")
# The ferry attaches a repo to a ref two ways, and neither is a tidy bullet
# prefix: `taskferry#417` glued together, or the repo named once in the line
# ("taskferry PR #421 (3.2.0), #401, #407"). Both are handled; a line naming
# no repo, or more than one, is left alone.
ATTACHED_REF_RE = re.compile(r"([A-Za-z0-9._-]+)#(\d+)\b")


def _repo_index() -> dict:
    """Short name -> owner/name, for resolving a digest bullet's repo prefix.

    Bullets are written `- taskferry: ...`, so index both the GitHub name and
    the directory basename: the ferry uses whichever the user says out loud,
    and those differ (`token-burn` for
    `token-burn-dashboard-model-faceoff`)."""
    idx: dict[str, str] = {}
    for d, full in _repo_origins():
        for key in (full.split("/")[-1], Path(d).name):
            idx.setdefault(key.lower(), full)
    return idx


_CEILINGS: dict = {}


def _ref_ceiling(full: str):
    """Highest issue/PR number in a repo, or None if it can't be read.

    GitHub numbers issues and pull requests from one sequence, so a single
    ceiling bounds both. This catches a number invented out of range; it does
    NOT catch a real number attached to an invented claim (the 2026-07-30
    "PR #238 by anakette" failure was that second kind, and no existence check
    would have caught it — only verify_evidence's per-ref lookup does). Gaps
    below the ceiling from transferred or deleted issues stay unflagged on
    purpose: a false accusation on a real ref costs more than a missed one."""
    if full not in _CEILINGS:
        got = None
        # Retry once: caching the None from a transient `gh` failure would
        # silently abstain on that repo for the rest of the run.
        for _ in range(2):
            got = _gh_json(["api", f"repos/{full}/issues?state=all&per_page=1"])
            if got is not None:
                break
        _CEILINGS[full] = (got[0].get("number")
                           if isinstance(got, list) and got else None)
    return _CEILINGS[full]


def _add_is_disproved(mut: dict) -> bool:
    """Does an `add` mutation cite a ref its own repo cannot have?

    `check` ops get a real per-ref lookup from todos.verify_evidence, but
    `add` ops carry no evidence field to verify — they'd otherwise reach the
    ledger with no ref check at all, so a fabricated number becomes a real
    obligation. Same disproof standard as the digest: only an out-of-range
    ref in a resolvable repo counts."""
    repo = mut.get("repo")
    if mut.get("op") != "add" or not repo:
        return False
    full = next((f for d, f in _repo_origins() if d == str(repo)), None)
    if not full:
        return False
    ceiling = _ref_ceiling(full)
    if ceiling is None:
        return False
    return any(int(n) > ceiling for n in REF_RE.findall(mut.get("line") or ""))


def _flag_unverified_refs(text: str) -> tuple:
    """Mark a #NNN citation only when its repo is known AND the host proves
    the number cannot exist.

    The previous gate allowlisted refs appearing in the live GitHub feed
    (open PRs only) and a one-day git scan, so every *merged* PR — precisely
    the ones a Shipped bullet cites — got flagged. Twenty-odd false positives
    per digest, injected inside backticked evidence tags. Unknown now means
    unremarked, not guilty."""
    idx = _repo_index()
    flagged, out = [], []

    def mark(num):
        flagged.append(num)
        return f"#{num} [UNVERIFIED — no such issue or PR in this repo]"

    def over_ceiling(full, num):
        c = _ref_ceiling(full)
        return c is not None and int(num) > c

    for line in text.splitlines(keepends=True):
        # `\b` sits between a word char and a hyphen, so a `\bcatbow\b` key
        # matches inside `catbow-tools` and would judge that sibling's refs
        # against the wrong repo's numbering. Require a non-name character
        # on both sides instead.
        named = {idx[k] for k in idx
                 if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", line, re.I)}
        # Bare refs only resolve when the line points at exactly one repo.
        # Guessing between two would risk judging a ref against the wrong
        # numbering — the same false-accusation failure this rewrite exists
        # to end.
        sole = next(iter(named)) if len(named) == 1 else None

        def repl(mm):
            whole, name = mm.group(0), mm.group(1)
            num = mm.group(2) if name is not None else mm.group(3)
            if name is not None:  # `taskferry#417`
                full = idx.get(name.lower())
                return f"{name}{mark(num)}" if full and over_ceiling(full, num) else whole
            if sole and over_ceiling(sole, num):
                return mark(num)
            return whole

        out.append(re.sub(rf"{ATTACHED_REF_RE.pattern}|{REF_RE.pattern}", repl, line))
    return "".join(out), flagged


def collect_cwds(lines) -> set:
    cwds = set()
    for ln in lines:
        try:
            e = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(e, dict) and e.get("cwd"):
            cwds.add(e["cwd"])
    return cwds


def run_once() -> dict:
    cfg = jl.load_config()
    if not cfg["model"]:
        jl.die("no model pinned — run the jeeves setup (see SKILL.md) first")
    offs = offsets_load()
    date = jl.today_et()
    counts = {"sessions": 0, "extracted": 0, "failed": 0, "digest": 0, "skipped": 0}
    slices = []
    all_cwds = set()
    pending_offsets = dict(offs)

    sessions = discover_sessions()
    for path in sessions:
        key = str(path)
        off = offs.get(key, {}).get("offset", 0)
        # Same vanishing-transcript race as the offset write further down:
        # `discover_sessions()` globbed this path, but a worktree removal can
        # delete it before the read reaches it. Skip the session rather than
        # letting the whole run die on one dead path.
        # Catch only the vanished case. A permissions or I/O error on a
        # transcript that is still there is a real fault, and logging it as
        # "vanished" would hand back a confident wrong diagnosis.
        try:
            lines, new_off, status = jl.read_delta(path, off)
        except FileNotFoundError:
            jl.log(f"transcript vanished before read, skipping: {key}")
            continue
        if status == "rotated":
            jl.log(f"rotation detected, re-reading from 0: {key}")
        all_cwds |= collect_cwds(lines)
        entries = jl.denoise(lines, cfg["truncate"])
        if new_off == off and not entries:
            continue
        counts["sessions"] += 1
        # A sub-threshold slice used to advance the offset, which discarded
        # the content outright — a session dribbling out three prose entries
        # an hour was never summarized at all. Hold the offset instead so the
        # lines accumulate until they're worth a dispatch. Once the session
        # stops growing there is nothing left to wait for, so extract the
        # little there is rather than sitting on it forever.
        if len(entries) < cfg["trivial_min"] and not _is_dead(path, cfg):
            counts["skipped"] += 1
            continue
        slices.append({"path": key, "dir": str(path.parent), "slug": path.parent.name,
                       "sid": session_id_of(path), "new_off": new_off,
                       "entries": entries, "raw": lines,
                       "rendered": jl.render_slice(entries)})

    prune_staging()
    # Every real session the collector discovered this run, across all batches
    # — used below to tell a garbage session label (safe to re-key) from one
    # that names a real session (a claim about whose work this is, which must
    # not be re-keyed). Derive it from the discovered session universe, not
    # just the slices with new content this run: a real-but-quiet session (no
    # new bytes, so never sliced) is still a real name, and a fallback payload
    # naming it must not be re-keyed onto a slice that happens to be missing.
    known_sids = {session_id_of(p) for p in sessions}
    for gi, group in enumerate(group_slices(slices, cfg["batch_under"], cfg["batch_max"])):
        by_session, res = {}, None
        # Staged path: hand the ferry the raw transcript and let it read the
        # tool calls, tool results, and sidechain entries `denoise` throws
        # away — 98% of the bytes, and where every real evidence ref lives.
        staging = staging_root() / f"{date}--{jl.now_et().strftime('%H%M%S')}--{gi}"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            staging.chmod(0o700)  # staging_root() locks the parent; this run's
            # own subdir inherits the process umask (typically 0755) unless
            # told otherwise — verbatim transcript copies, not world-readable.
            for s in group:
                stage_slice(staging, s)
            res = jl.ferry(extract_tools_prompt(group, staging), cfg["model"],
                           directory=str(staging))
            if res["ok"]:
                by_session = read_staged_summaries(staging, group)
        except OSError as e:
            jl.log(f"staging failed ({e}); falling back to the rendered-prose path")
        finally:
            # The TTL prune at the top of the next run would eventually get
            # this, but there's no reason to leave a verbatim transcript copy
            # sitting on disk for up to `keep_h` hours once this run is done
            # reading it.
            shutil.rmtree(staging, ignore_errors=True)
            if staging.exists():
                jl.log(f"staging cleanup left {staging} behind (rmtree failed)")

        # Fallback: for whatever sessions the staged path didn't cover (all
        # of them, if staging failed outright or wrote no files; a subset,
        # if the ferry only got partway through the batch), retry on the
        # pre-staging dispatch, which passes a denoised prose blob and reads
        # the answer out of the final message. A session the staged path DID
        # cover is never re-dispatched — falling back per-session, not
        # all-or-nothing, so one uncovered session in an otherwise-successful
        # batch doesn't cost every session in it a retry.
        missing = [s for s in group if s["sid"] not in by_session]
        if missing:
            if res is not None and res["ok"] and by_session:
                jl.log(f"staged run covered {sorted(by_session)}, missing "
                       f"{[s['sid'] for s in missing]}; retrying those on the "
                       f"rendered-prose path")
            elif res is not None and res["ok"]:
                jl.log(f"staged run wrote no summary files (task {res['task_id']}); "
                       f"retrying on the rendered-prose path")
            elif res is not None and not res["ok"]:
                jl.log(f"staged dispatch failed ({res['error']}); retrying on "
                       f"the rendered-prose path")
            fb_res = jl.ferry(extract_prompt(missing), cfg["model"])
            if not fb_res["ok"]:
                jl.log(f"extraction failed ({fb_res['error']}); offsets held "
                       f"for retry: {[s['sid'] for s in missing]}")
            else:
                payload = jl.parse_fenced_json(fb_res["message"])
                if not isinstance(payload, list):
                    jl.log(f"extraction output unparseable (task {fb_res['task_id']}); "
                           f"offsets held; first 300 chars: {fb_res['message'][:300]!r}")
                else:
                    fallback = {}
                    for p in payload:
                        if not isinstance(p, dict):
                            continue
                        sid = p.get("session")
                        if not isinstance(sid, str):
                            # The fallback keys summaries by the real session
                            # sid (always a str). A non-str `session` here — a
                            # ferry returning a list/dict for the field — can't
                            # be a real sid, and using a list/dict as a dict key
                            # would raise TypeError; skip rather than crash the
                            # whole run.
                            jl.log(f"fallback entry has non-string session {sid!r}; skipped")
                            continue
                        if not _looks_like_summary(p):
                            # No shape gate existed here at all before — the
                            # staged path's own `_looks_like_summary` gate
                            # applies here too, not just there.
                            jl.log(f"fallback entry for {sid!r} "
                                   f"doesn't look like the extraction schema; skipped")
                            continue
                        fallback[sid] = p
                    # Lenient fallback: with exactly one missing slice and one
                    # returned entry, a mislabeled session key is still
                    # obviously the answer — there's only one session it could
                    # possibly be about, so a placeholder or hallucinated name
                    # is safe to re-key. The one exception is a name that
                    # belongs to another *real* session this run knows about:
                    # that's not a garbage label, it's a claim this content is
                    # some other session's, and re-keying it would attribute
                    # one session's work to another. `read_staged_summaries`
                    # refuses the analogous mismatch rather than relabeling it;
                    # this is the same rule, narrowed to the case where the
                    # conflicting name actually resolves to something.
                    only = (payload[0] if len(payload) == 1 and isinstance(payload[0], dict)
                            else None)
                    own_sid = only.get("session") if only is not None else None
                    # `fallback.get(own_sid) is only` reuses the loop's shape
                    # gate above: `fallback` holds only entries that passed
                    # `_looks_like_summary` with a string session, so if the
                    # sole entry is there under its own sid it's the validated
                    # value — no need (or room for drift) to re-check it.
                    valid_single = isinstance(own_sid, str) and fallback.get(own_sid) is only
                    conflicts = valid_single and bool(missing) \
                        and own_sid != missing[0]["sid"] and own_sid in known_sids
                    if valid_single and len(missing) == 1 \
                            and missing[0]["sid"] not in fallback \
                            and not conflicts:
                        # `valid_single` implies `only is not None` (a None
                        # can't be a dict value in `fallback`), but mypy can't
                        # track that through the boolean — assert it so `{**only,
                        # ...}` narrows `only` from `dict | None`.
                        assert only is not None
                        fallback[missing[0]["sid"]] = {**only, "session": missing[0]["sid"]}
                    elif conflicts:
                        jl.log(f"fallback entry for {missing[0]['sid']} claims to be "
                               f"another session in this batch ({own_sid!r}); not re-keyed")
                    # Scope the merge to the sids this fallback dispatch was
                    # actually asked about — an extra or mislabeled sid in
                    # the response must not silently overwrite an
                    # already-staged (tool-evidence-backed) summary for a
                    # session outside `missing`.
                    missing_sids = {s["sid"] for s in missing}
                    by_session.update({sid: v for sid, v in fallback.items()
                                       if sid in missing_sids})
        for s in group:
            item = by_session.get(s["sid"])
            if item is None:
                # The ferry read the batch but said nothing about this
                # session. Advancing anyway wrote a "no block" stub over
                # content nobody ever looked at; hold for a retry instead,
                # unless the session is done growing and never will produce
                # more than what a stub records.
                if not _is_dead(Path(s["path"]), cfg):
                    counts["failed"] += 1
                    jl.log(f"no block returned for {s['sid']}; offset held for retry")
                    continue
                item = {"session": s["sid"], "note": "no block for session"}
            n = len(list((jl.state_dir() / "summaries" / date).glob(f"*--{s['sid']}--*.md"))) + 1
            write_summary(date, s["slug"], s["sid"], n, item)
            # The transcript can disappear between discovery and here — the
            # project dir is named after a git worktree, so removing that
            # worktree deletes it, and a ferry dispatch sits inside the
            # window. Sizing it unguarded raised FileNotFoundError out of
            # run_once, which skipped `offsets_save` below and threw away the
            # whole batch's extraction, including every session that was
            # perfectly fine. Drop the row instead: the summary above is
            # already written, and an offset into a file nobody can stat is
            # not worth carrying forward. Matches `_is_dead`'s reading of a
            # missing transcript as one that has stopped growing.
            try:
                size = Path(s["path"]).stat().st_size
            except FileNotFoundError:
                jl.log(f"transcript vanished mid-run, dropping its offset: {s['path']}")
                counts["extracted"] += 1
                continue
            pending_offsets[s["path"]] = {"offset": s["new_off"], "size": size}
            counts["extracted"] += 1

    offsets_save(pending_offsets)

    dirs_file = jl.state_dir() / "project-dirs.txt"
    prev = set(dirs_file.read_text().splitlines()) if dirs_file.exists() else set()
    union = sorted(prev | all_cwds)
    dirs_file.write_text("\n".join(union) + ("\n" if union else ""))
    td.ingest_repo_todos(union)

    if counts["extracted"]:
        td.reconcile()
        github_block = gh_review()
        git_state_block = git_state()
        res = jl.ferry(synthesis_prompt(date, github_block, git_state_block),
                        cfg["model"], wait_s=600)
        if not res["ok"]:
            jl.log(f"synthesis failed: {res['error']} — digest not refreshed")
        else:
            m = re.search(r"```markdown\s*\n(.*?)```", res["message"], re.S)
            digest_md = m.group(1) if m else None
            if digest_md is None:
                # Lenient fallback: on long outputs the model sometimes
                # drops the ```markdown fence but still emits the digest —
                # take everything from the digest heading to the next fence.
                m2 = re.search(r"(# jeeves digest\b.*?)(?=\n```|\Z)",
                               res["message"], re.S)
                if m2:
                    digest_md = m2.group(1).strip() + "\n"
                    jl.log("digest recovered without markdown fence")
            muts = jl.parse_fenced_json(res["message"])
            if digest_md is None or not isinstance(muts, list):
                jl.log("synthesis output malformed; digest not refreshed")
            else:
                digest_md, flagged = _flag_unverified_refs(digest_md)
                if flagged:
                    jl.log(f"digest: flagged unverified refs {sorted(set(flagged))}")
                d = jl.state_dir() / "digests"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{date}.md").write_text(digest_md)
                # Mutations are no longer pre-filtered on ref strings. The
                # old filter dropped every `check` citing a merged PR — the
                # only kind that ever closes a todo — so the ledger could
                # only grow. todos.verify_evidence gates each check with a
                # real per-ref lookup; `add` has no evidence field to check,
                # so it gets the same disproof test the digest uses.
                keep: list[dict] = []
                disproved: list[dict] = []
                for mut in muts:
                    (disproved if _add_is_disproved(mut) else keep).append(mut)
                if disproved:
                    jl.log(f"todo adds dropped — ref cannot exist: {disproved}")
                applied = td.apply_mutations(keep)
                counts["digest"] = 1
                jl.log(f"digest written; mutations: {applied}")
    return counts


def seed_offsets() -> int:
    """Mark all current transcript bytes as seen, without dispatching.
    First-install behavior: jeeves starts from now, not from the beginning
    of history."""
    offs = offsets_load()
    n = 0
    for path in discover_sessions():
        key = str(path)
        if key in offs:
            continue
        # Same vanishing-transcript race as run_once: discover_sessions()
        # globbed this path, and a worktree removal can delete it before
        # either the read or the stat lands. Unguarded, one dead path
        # aborted the whole seed before offsets_save() ran, so every
        # transcript already walked was seeded again from zero on the next
        # attempt — the first run then re-extracted all of history.
        try:
            lines, new_off, _ = jl.read_delta(path, 0)
            size = path.stat().st_size
        except FileNotFoundError:
            jl.log(f"transcript vanished during seed, skipping: {key}")
            continue
        offs[key] = {"offset": new_off, "size": size}
        n += 1
    offsets_save(offs)
    jl.log(f"seeded offsets for {n} transcripts")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(prog="collect.py")
    ap.add_argument("--seed", action="store_true",
                    help="mark all current transcript bytes as seen and exit")
    a = ap.parse_args()
    if a.seed:
        print(json.dumps({"seeded": seed_offsets()}))
        return
    lock = (jl.state_dir() / "collect.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        jl.log("previous run still active; skipping")
        print("jeeves: previous run still active; skipping")
        return
    counts = run_once()
    jl.log(f"run complete: {counts}")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
