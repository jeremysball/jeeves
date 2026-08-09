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
    sections = {s: [] for s in SECTIONS}
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


HEX_RE = re.compile(r"^commit ([0-9a-f]{7,40})$", re.I)
PR_RE = re.compile(r"^PR #(\d+)$", re.I)
FILE_RE = re.compile(r"^file (.+)$", re.I)


def _runs(args) -> int:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=15).returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 1


def verify_evidence(evidence: str, repo) -> bool:
    m = HEX_RE.match(evidence)
    if m:
        if not repo:
            return False
        return _runs(["git", "-C", str(repo), "cat-file", "-e", f"{m.group(1)}^{{commit}}"]) == 0
    m = PR_RE.match(evidence)
    if m:
        if not repo:
            return False
        return _runs(["gh-axi", "pr", "view", m.group(1), "-R", str(repo)]) == 0
    m = FILE_RE.match(evidence)
    if m:
        if not repo:
            return False
        return (Path(repo) / m.group(1).strip()).exists()
    return False


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
    items = []
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
    elif a.pending:
        print(json.dumps(load_pending(), indent=1))
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
