#!/usr/bin/env python3
"""Todo ledger mutations. Code owns all writes: normalized unique-match only,
never deletes, everything provenance-tagged."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import jeeves_lib as jl

SKELETON = "# jeeves todo ledger\n\n## open\n\n## done\n\n## dismissed\n"
SECTIONS = ("open", "done", "dismissed")


class AmbiguousMatch(Exception):
    pass


def ledger_path() -> Path:
    p = jl.data_dir() / "todo.md"
    if not p.exists():
        p.write_text(SKELETON)
    return p


def parse_ledger(text: str) -> dict:
    sections: dict[str, list[str]] = {s: [] for s in SECTIONS}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            current = name if name in sections else None
        elif current is not None and line.strip():
            sections[current].append(line)
    return sections


def render(sections: dict) -> str:
    out = ["# jeeves todo ledger", ""]
    for s in SECTIONS:
        out.append(f"## {s}")
        out.extend(sections[s])
        out.append("")
    return "\n".join(out)


def _write(sections: dict) -> None:
    p = ledger_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(render(sections))
    tmp.replace(p)


def find_match(sections: dict, query: str, only: str = "open"):
    nq = jl.normalize(query)
    hits = [(s, i, ln) for s in SECTIONS if s == only or only is None
            for i, ln in enumerate(sections[s]) if jl.normalize(ln) == nq]
    if not hits:
        return None
    if len(hits) > 1:
        raise AmbiguousMatch(f"{len(hits)} ledger lines match {query!r}")
    return hits[0][0], hits[0][1]


def apply_add(item: str, kind: str, source: str) -> str:
    tag = f"(jeeves: {kind}, {source}, {jl.today_et()})"
    line = f"- [ ] {item} {tag}"
    sections = parse_ledger(ledger_path().read_text())
    sections["open"].append(line)
    _write(sections)
    jl.log(f"todo add: {line}")
    return line


def apply_check(query: str, evidence: str) -> str:
    sections = parse_ledger(ledger_path().read_text())
    m = find_match(sections, query, only="open")
    if m is None:
        raise AmbiguousMatch(f"no open ledger line matches {query!r}")
    _, i = m
    line = sections["open"].pop(i)
    if line.startswith("- [ ] "):
        checked = "- [x] " + line[len("- [ ] "):] + f" (jeeves: {evidence}, {jl.today_et()})"
    else:
        checked = line.replace("- [ ]", "- [x]", 1)
    sections["done"].append(checked)
    _write(sections)
    jl.log(f"todo check: {checked}")
    return checked


def apply_dismiss(query: str) -> str:
    sections = parse_ledger(ledger_path().read_text())
    m = find_match(sections, query, only="open")
    if m is None:
        raise AmbiguousMatch(f"no open ledger line matches {query!r}")
    _, i = m
    line = sections["open"].pop(i)
    dismissed = f"{line} (dismissed {jl.today_et()})"
    sections["dismissed"].append(dismissed)
    _write(sections)
    jl.log(f"todo dismiss: {dismissed}")
    return dismissed


FILE_RE = re.compile(r"^file (.+)$", re.I)

# The old anchored single-ref forms (`^commit <sha>$`, `^PR #N$`) read every
# multi-ref evidence string as unparseable -- the extraction ferry routinely
# emits several refs in one string instead:
#   "commit 062563c, commit e0056ed"
#   "commit dcfcab4 + 01442dd (PR #114, PR #115)"
#   "commit e0056ed (#364), 062563c (#360)"
# These scan for every ref in the string instead of demanding exactly one.
# `issue #N` is matched and carved out first so PR_SCAN_RE's bare `#N` doesn't
# also claim the same digits as a pr candidate.
SHA_SCAN_RE = re.compile(r"\b([0-9a-f]{7,40})\b", re.I)
ISSUE_SCAN_RE = re.compile(r"\bissue\s*#(\d+)\b", re.I)
PR_SCAN_RE = re.compile(r"#(\d+)\b")


def _extract_refs(evidence: str) -> list:
    """Every (kind, value) *candidate* ref in an evidence string, in order.

    A `file <path>` evidence is treated as whole-string, since a path has no
    reliable token boundary to scan for.

    Hex tokens are candidates only -- `[0-9a-f]{7,40}` also matches any 7+ digit
    number and hex-lettered words like "defaced", and evidence does not reliably
    say the word "commit" next to its shas. Rather than gate on that keyword
    (which drops real refs in strings like "062563c and e0056ed shipped"),
    every candidate is emitted here and git decides: classification drops the
    ones that resolve to no object, so a false positive costs a cheap cat-file
    and nothing else.
    """
    m = FILE_RE.match(evidence.strip())
    if m:
        return [("file", m.group(1).strip())]
    issue_hits = [(m.start(), m.end(), m.group(1)) for m in ISSUE_SCAN_RE.finditer(evidence)]
    issue_spans = [(s, e) for s, e, _ in issue_hits]
    # Sorted by match position, not concatenated per kind: "in order" is what
    # the contract promises, and scanning each pattern separately grouped every
    # commit ahead of every pr, so "e0056ed (#364), 062563c (#360)" came back
    # with both prs trailing both commits and lost which pr went with which.
    hits = ([(m.start(), "commit", m.group(1)) for m in SHA_SCAN_RE.finditer(evidence)]
            + [(m.start(), "pr", m.group(1)) for m in PR_SCAN_RE.finditer(evidence)
               if not any(s <= m.start() < e for s, e in issue_spans)]
            + [(s, "issue", v) for s, e, v in issue_hits])
    return [(kind, value) for _, kind, value in sorted(hits, key=lambda h: h[0])]


def _runs(args, cwd=None) -> int:
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=15, cwd=cwd).returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, NotADirectoryError):
        return 1


def _capture(args, cwd=None) -> str:
    """stdout of a successful run, or "" on any failure. Never raises."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=15, cwd=cwd)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, NotADirectoryError):
        return ""
    return p.stdout if p.returncode == 0 else ""


# Evidence verdicts. `landed` is the only one that checks a todo off; the split
# exists because "this ref exists" and "this work is merged" are different
# questions, and an earlier version of verify_evidence answered the first while
# the ledger asked the second.
LANDED = "landed"        # merged into the base branch / present on disk
OUTSTANDING = "outstanding"  # the ref is real but not merged: still live work
UNKNOWN = "unknown"      # could not determine; never treated as landed


def _default_branch(repo) -> str:
    """The base branch to measure "merged" against. origin/HEAD when set,
    else the first of main/master that resolves. Empty when neither does --
    callers must degrade to UNKNOWN rather than guess a base."""
    head = _capture(["git", "-C", str(repo), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"]).strip()
    if head:
        return head.removeprefix("refs/remotes/")
    for cand in ("origin/main", "origin/master", "main", "master"):
        if _runs(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"]) == 0:
            return cand
    return ""


def _classify_commit(sha: str, repo) -> str:
    if _runs(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"]) != 0:
        return UNKNOWN
    base = _default_branch(repo)
    if not base:
        return UNKNOWN
    return LANDED if _runs(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, base]) == 0 else OUTSTANDING


def _classify_gh_ref(kind: str, num: str, repo, landed_state: str) -> str:
    """LANDED when gh-axi reports `landed_state` ("merged" for a pr, "closed"
    for an issue -- closing is the closest proxy an issue offers for "the work
    behind this is done"), OUTSTANDING for any other real state, UNKNOWN when
    the ref can't be resolved at all.

    Runs inside the repo rather than passing -R when repo is a real directory:
    that flag takes OWNER/REPO, so handing it a local path returns NOT_FOUND
    for every ref that exists. The ferry is asked for a local path but
    sometimes writes `owner/repo`; that shape is exactly what -R wants, so
    take it either way rather than failing a verifiable ref."""
    if Path(repo).is_dir():
        out = _capture(["gh-axi", kind, "view", num], cwd=str(repo))
    elif re.fullmatch(r"[\w.-]+/[\w.-]+", str(repo)):
        out = _capture(["gh-axi", kind, "view", num, "-R", str(repo)])
    else:
        return UNKNOWN
    if not out:
        return UNKNOWN
    # gh-axi prints a YAML-ish block; the state line is `  state: merged`.
    m = re.search(r"^\s*state:\s*\"?(\w+)", out, re.M)
    if not m:
        return UNKNOWN
    return LANDED if m.group(1).lower() == landed_state else OUTSTANDING


def _classify_pr(num: str, repo) -> str:
    return _classify_gh_ref("pr", num, repo, "merged")


def _classify_issue(num: str, repo) -> str:
    return _classify_gh_ref("issue", num, repo, "closed")


def classify_evidence(evidence: str, repo) -> str:
    """Classify evidence as LANDED / OUTSTANDING / UNKNOWN.

    A commit that exists but is not in the base branch's ancestry is a branch
    commit that still needs merging, not shipped work; a PR that is open is the
    same. Both report OUTSTANDING so the card can say so instead of silently
    presenting either as done.

    Evidence citing several refs is LANDED only when every ref landed -- half a
    change being merged is not the change being merged. A single OUTSTANDING ref
    dominates, since that is the part still needing action; UNKNOWN otherwise.
    """
    # Guard before _extract_refs, not after: a ferry mutation with an explicit
    # "evidence": null survives JSON parsing as a present-but-None value, and
    # `mut.get("evidence", "")` (the default-arg form used at every call site)
    # does not catch that -- the default only fires when the key is absent.
    # _extract_refs then calls evidence.strip() and raises AttributeError,
    # crashing the whole --pending/--prune-pending invocation over one
    # malformed row instead of demoting it.
    if not evidence or not repo:
        return UNKNOWN
    refs = _extract_refs(evidence)
    if not refs:
        return UNKNOWN
    verdicts = []
    for kind, value in refs:
        if kind == "commit":
            if _runs(["git", "-C", str(repo), "cat-file", "-e", f"{value}^{{commit}}"]) != 0:
                continue  # not a sha at all, just a hex-shaped token -- not evidence
            verdicts.append(_classify_commit(value, repo))
        elif kind == "pr":
            verdicts.append(_classify_pr(value, repo))
        elif kind == "issue":
            verdicts.append(_classify_issue(value, repo))
        else:
            verdicts.append(LANDED if (Path(repo) / value).exists() else OUTSTANDING)
    if not verdicts:
        return UNKNOWN
    if OUTSTANDING in verdicts:
        return OUTSTANDING
    return LANDED if all(v == LANDED for v in verdicts) else UNKNOWN


def verify_evidence(evidence: str, repo) -> bool:
    """True only when the evidence shows the work actually landed."""
    return classify_evidence(evidence, repo) == LANDED


def pending_path() -> Path:
    return jl.state_dir() / "pending.json"


def load_pending() -> list:
    p = pending_path()
    return json.loads(p.read_text()) if p.exists() and p.read_text().strip() else []


def save_pending(items: list) -> None:
    tmp = pending_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1))
    tmp.replace(pending_path())


def _push_pending(mut: dict, reason: str) -> None:
    items = load_pending()
    items.append({**mut, "reason": reason, "queued": jl.now_et().isoformat(timespec="seconds")})
    save_pending(items)


def apply_mutations(muts: list) -> dict:
    """Apply ferry-emitted mutations with all gates. Never raises on content —
    failures demote to pending or dedup, logged either way."""
    seen = jl.SeenStore.load()
    counts = {"applied": 0, "pending": 0, "deduped": 0, "failed": 0}
    for mut in muts:
        op = mut.get("op")
        line = (mut.get("line") or "").strip()
        if not line:
            counts["failed"] += 1
            jl.log(f"mutation skipped, no line: {mut}")
            continue
        h = jl.line_hash(line)
        if op == "add":
            rec = seen.check(h)
            if rec is not None:  # known: open, done, or dismissed — never re-add
                seen.upsert(h, rec["line"], status=rec["status"])
                counts["deduped"] += 1
                continue
            apply_add(line, mut.get("kind", "loose-end"), mut.get("source", "session"))
            seen.upsert(h, line)
            counts["applied"] += 1
        elif op == "duplicate_of":
            ex = seen.check(jl.line_hash(mut.get("existing", "")))
            if ex:
                seen.upsert(ex["hash"], ex["line"], status=ex["status"])
                counts["deduped"] += 1
            else:
                counts["failed"] += 1
                jl.log(f"duplicate_of: existing line unknown: {mut}")
        elif op == "check":
            if not verify_evidence(mut.get("evidence", ""), mut.get("repo")):
                _push_pending(mut, "evidence did not verify")
                counts["pending"] += 1
                jl.log(f"check demoted to pending (evidence): {mut}")
                continue
            try:
                apply_check(line, mut.get("evidence", "verified"))
                seen.set_status(h, "done")
                counts["applied"] += 1
            except AmbiguousMatch:
                _push_pending(mut, "no unique ledger match")
                counts["pending"] += 1
                jl.log(f"check demoted to pending (match): {mut}")
        else:
            counts["failed"] += 1
            jl.log(f"unknown op: {mut}")
    seen.save()
    return counts


def prune_pending() -> dict:
    """Re-run the gates over the pending queue and drain what no longer belongs.

    pending.json is otherwise append-only: _push_pending adds, and before this
    existed nothing ever removed, so a check queued against a since-merged PR
    sat in the queue forever and every wake re-reported it as a live loose end.

    Each row resolves one of four ways:
      applied  - evidence now shows LANDED and the ledger line is still open,
                 so the check it was demoted from finally goes through
      moot     - the ledger line is no longer open (checked or dismissed since),
                 so there is nothing left for this row to do
      stale    - the line was never in the ledger at all
      kept     - still genuinely pending; evidence has not landed yet
    """
    AMBIGUOUS = object()

    def _match(sections, line, only):
        """find_match raises on duplicate ledger lines; a duplicated line is a
        reason to keep the row for a human, never to crash the whole prune.

        Ambiguity returns its own sentinel rather than None. Collapsing it to
        None read as "not in the open section", which sent the row down the
        moot/stale path and dropped it from the queue outright -- the one thing
        the queue exists to prevent, and the opposite of what this docstring
        promised."""
        try:
            return find_match(sections, line, only=only)
        except AmbiguousMatch:
            return AMBIGUOUS

    items = load_pending()
    if not items:
        return {"applied": 0, "moot": 0, "stale": 0, "kept": 0}
    seen = jl.SeenStore.load()
    sections = parse_ledger(ledger_path().read_text())
    counts = {"applied": 0, "moot": 0, "stale": 0, "kept": 0}
    kept = []
    for row in items:
        line = (row.get("line") or "").strip()
        if not line:
            counts["stale"] += 1
            continue
        open_hit = _match(sections, line, "open")
        if open_hit is AMBIGUOUS:
            # Several open lines match, so there is no safe one to check off and
            # no basis for calling the row resolved. Keep it for a human.
            kept.append(row)
            counts["kept"] += 1
            jl.log(f"prune-pending: kept (ambiguous ledger match): {line}")
            continue
        if open_hit is None:
            # Distinguish "resolved since" from "never existed" so a genuinely
            # lost line is visible rather than silently swallowed as handled.
            done_hit = _match(sections, line, "done")
            dismissed_hit = _match(sections, line, "dismissed")
            if done_hit is AMBIGUOUS or dismissed_hit is AMBIGUOUS:
                # A duplicate in done/dismissed is just as ambiguous as one in
                # open -- `known = a or b` treated this sentinel as truthy and
                # dropped the row as "moot", which is exactly the silent drop
                # the open-branch AMBIGUOUS case above exists to prevent. Keep
                # it here too.
                kept.append(row)
                counts["kept"] += 1
                jl.log(f"prune-pending: kept (ambiguous ledger match): {line}")
                continue
            known = done_hit or dismissed_hit
            counts["moot" if known else "stale"] += 1
            jl.log(f"prune-pending: dropped ({'moot' if known else 'stale'}): {line}")
            continue
        if not verify_evidence(row.get("evidence", ""), row.get("repo")):
            kept.append(row)
            counts["kept"] += 1
            continue
        try:
            apply_check(line, row.get("evidence", "verified"))
            seen.set_status(jl.line_hash(line), "done")
            counts["applied"] += 1
            jl.log(f"prune-pending: applied: {line}")
            sections = parse_ledger(ledger_path().read_text())
        except AmbiguousMatch:
            kept.append(row)
            counts["kept"] += 1
    seen.save()
    save_pending(kept)
    return counts


def reconcile() -> dict:
    """Ledger wins. Register untracked lines; record vanished lines as dismissed."""
    seen = jl.SeenStore.load()
    sections = parse_ledger(ledger_path().read_text())
    present = {}
    for s in SECTIONS:
        status = {"open": "open", "done": "done", "dismissed": "dismissed"}[s]
        for ln in sections[s]:
            present[jl.line_hash(ln)] = (status, ln)
    added = dismissed = 0
    for h, (status, ln) in present.items():
        if seen.check(h) is None:
            text = re.sub(r"^-\s*\[[ x]\]\s*", "", ln).strip()
            seen.upsert(h, text, status=status)
            added += 1
    for h, rec in list(seen.rows.items()):
        if rec["status"] != "dismissed" and h not in present:
            seen.set_status(h, "dismissed")
            dismissed += 1
    seen.save()
    jl.log(f"reconcile: +{added} registered, {dismissed} recorded dismissed")
    return {"registered": added, "dismissed": dismissed}


def wake() -> None:
    p = jl.state_dir() / "last_wake"
    p.write_text(jl.now_et().isoformat(timespec="seconds") + "\n")


def last_wake() -> str:
    p = jl.state_dir() / "last_wake"
    return p.read_text().strip() if p.exists() else ""


def delta_summary() -> dict:
    """Counts of ledger state for the invocation card."""
    seen = jl.SeenStore.load()
    sections = parse_ledger(ledger_path().read_text())
    top = sorted(seen.by_status("open"), key=lambda r: -r["count"])[:3]
    return {"open": len(sections["open"]), "done": len(sections["done"]),
            "dismissed": len(sections["dismissed"]), "pending": len(load_pending()),
            "top_recurrence": [(r["line"], r["count"]) for r in top if r["count"] > 1]}


def _todo_files(d: Path):
    for pat in ("TODO*", "todo*"):
        for f in sorted(d.glob(pat)):
            if f.is_file() and f.suffix.lower() in (".md", ".txt", ""):
                yield f


def _open_items(text: str) -> list:
    """Parse a loose checklist file into open items, joining wrapped
    continuation lines onto whichever item they physically follow — a
    continuation of a `[x]` (done) item is not a separate open item."""
    items: list[str] = []
    cur_text = None
    cur_checked = None

    def flush():
        nonlocal cur_text, cur_checked
        if cur_text and not cur_checked:
            t = cur_text.strip()
            if t and not t.endswith(":") and len(t) >= 4 and len(t.split()) >= 2:
                items.append(t)
        cur_text = None
        cur_checked = None

    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            flush()
            continue
        if s.startswith("#") or re.match(r"^[-=_]{3,}$", s):
            flush()
            continue
        m = re.match(r"^([-*]\s*)?\[([ xX])\]\s*(.*)$", s)
        if m:
            flush()
            cur_checked = m.group(2).lower() == "x"
            cur_text = m.group(3)
            continue
        m2 = re.match(r"^[-*]\s+(.*)$", s)
        if m2:
            flush()
            cur_checked = False
            cur_text = m2.group(1)
            continue
        # bare single-word line ("Feat", "Bugs"): section header — ends any
        # open item and is itself dropped, even mid-item
        if re.fullmatch(r"\w+", s):
            flush()
            continue
        # bare line with no active item: skip
        if cur_text is None:
            continue
        # a "(fixed in ...)" continuation marks the whole item resolved
        if re.match(r"^\(fixed\b", s, re.IGNORECASE):
            cur_checked = True
        cur_text += " " + s
    flush()
    return items


def ingest_repo_todos(dirs) -> dict:
    """One-time-per-content-hash import of repo todo files. Inputs are
    read-only: jeeves never writes to them."""
    seen = jl.SeenStore.load()
    hf = jl.state_dir() / "imports.ndjson"
    known = {}
    if hf.exists():
        for line in hf.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                known[r["path"]] = r["hash"]
    counts = {"scanned": 0, "ingested": 0, "skipped": 0}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        counts["scanned"] += 1
        for f in _todo_files(d):
            try:
                body = f.read_text()
            except OSError as e:
                jl.log(f"ingest read failed {f}: {e}")
                continue
            fh = hashlib.sha256(body.encode()).hexdigest()
            if known.get(str(f)) == fh:
                counts["skipped"] += 1
                continue
            known[str(f)] = fh
            for item in _open_items(body):
                ih = jl.line_hash(item)
                if seen.check(ih) is None:
                    apply_add(item, kind="import", source=d.name)
                    seen.upsert(ih, item)
                    counts["ingested"] += 1
    seen.save()
    tmp = hf.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps({"path": p, "hash": v}) + "\n" for p, v in known.items()))
    tmp.replace(hf)
    jl.log(f"ingest: {counts}")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(prog="todos.py")
    ap.add_argument("--add")
    ap.add_argument("--kind", default="manual")
    ap.add_argument("--source", default="user")
    ap.add_argument("--dismiss")
    ap.add_argument("--apply-mutations", metavar="FILE")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--wake", action="store_true")
    ap.add_argument("--delta", action="store_true")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--prune-pending", action="store_true")
    a = ap.parse_args()
    if a.add:
        print(apply_add(a.add, a.kind, a.source))
    elif a.dismiss:
        print(apply_dismiss(a.dismiss))
    elif a.apply_mutations:
        muts = json.loads(Path(a.apply_mutations).read_text())
        print(json.dumps(apply_mutations(muts)))
    elif a.reconcile:
        print(json.dumps(reconcile()))
    elif a.wake:
        wake()
    elif a.delta:
        print(json.dumps(delta_summary(), indent=1))
    elif a.prune_pending:
        print(json.dumps(prune_pending(), indent=1))
    elif a.pending:
        # Annotate each row with its live evidence state so a caller can tell a
        # merged PR from an unmerged branch commit without re-deriving it by hand.
        rows = [{**r, "state": classify_evidence(r.get("evidence", ""), r.get("repo"))}
                for r in load_pending()]
        print(json.dumps(rows, indent=1))
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
