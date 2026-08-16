#!/usr/bin/env python3
"""Todo ledger mutations. Code owns all writes: normalized unique-match only,
never deletes, everything provenance-tagged."""
import argparse
import atexit
import hashlib
import json
import os
import re
import subprocess
import sys
import time
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

# The single-ref anchored forms above are what the extraction ferry is *asked*
# to emit, but it routinely emits several refs in one string instead:
#   "commit 062563c, commit e0056ed"
#   "commit dcfcab4 + 01442dd (PR #114, PR #115)"
#   "commit e0056ed (#364), 062563c (#360)"
# Anchored single-ref matching read every one of those as unparseable, so real
# and fully verifiable evidence demoted to pending and stayed there forever.
# These scan for every ref in the string instead of demanding exactly one.
SHA_SCAN_RE = re.compile(r"\b([0-9a-f]{7,40})\b", re.I)
PR_SCAN_RE = re.compile(r"#(\d+)\b")
# `issue #391` is a real evidence shape the synthesis ferry emits, but
# GitHub's own auto-close keywords (close/closes/closed, fix/fixes/fixed,
# resolve/resolves/resolved) are a far more common phrasing to quote from a
# commit or PR body -- without them, "resolves #391" fell through to the
# keyword-less PR_SCAN_RE and misclassified as a PR. Scanned separately
# because `gh-axi pr view` fails on an issue number, so classifying every
# `#N` as a pr would leave issue-backed evidence permanently unknown.
# `\s*` (not `\s+`): the ferry also glues the form as `issue#391`/`fixes#391`,
# and a space requirement misclassifies that as a pr and strands it in pending
# forever.
ISSUE_SCAN_RE = re.compile(
    r"\b(?:issues?|close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*#(\d+)\b", re.I)


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
    # A ferry mutation can carry `evidence: null` (JSON-null survives parsing as
    # a present-but-None value) or a non-string. `.strip()` would crash on those,
    # so treat anything that isn't a string as having no refs -> UNKNOWN, rather
    # than letting a single bad evidence field crash the whole run.
    if not isinstance(evidence, str):
        return []
    m = FILE_RE.match(evidence.strip())
    if m:
        return [("file", m.group(1).strip())]
    # Sorted by match position, not concatenated per kind: "in order" is what
    # the contract promises, and scanning each pattern separately grouped every
    # commit ahead of every pr, so "e0056ed (#364), 062563c (#360)" came back
    # with both prs trailing both commits and lost which pr went with which.
    issues = [(m.start(1), "issue", m.group(1)) for m in ISSUE_SCAN_RE.finditer(evidence)]
    taken = {pos for pos, _, _ in issues}
    hits = ([(m.start(), "commit", m.group(1)) for m in SHA_SCAN_RE.finditer(evidence)]
            + [(m.start(1), "pr", m.group(1)) for m in PR_SCAN_RE.finditer(evidence)
               if m.start(1) not in taken]
            + issues)
    return [(kind, value) for _, kind, value in sorted(hits, key=lambda h: h[0])]


def _runs(args, cwd=None) -> int:
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=15, cwd=cwd).returncode
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, NotADirectoryError):
        return 1


def _capture(args) -> str:
    """stdout of a successful run, or "" on any failure. Never raises."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    return p.stdout if p.returncode == 0 else ""


# Evidence verdicts. `landed` is the only one that checks a todo off; the split
# exists because "this ref exists" and "this work is merged" are different
# questions, and an earlier version of verify_evidence answered the first while
# the ledger asked the second.
LANDED = "landed"        # merged into the base branch / present on disk
OUTSTANDING = "outstanding"  # the ref is real but not merged: still live work
UNKNOWN = "unknown"      # could not determine; never treated as landed


# The base branch is a property of the repo, not of any one evidence row, and
# resolving it costs up to three git calls; memoize per repo path.
_DEFAULT_BRANCH_CACHE: dict[str, str] = {}


def _default_branch(repo) -> str:
    """The base branch to measure "merged" against. origin/HEAD when set,
    else the first resolved origin/main or origin/master. A local main/master
    fallback is used only when no origin remote is configured. Empty when
    neither does — callers must degrade to UNKNOWN rather than guess a base."""
    key = str(repo)
    if key in _DEFAULT_BRANCH_CACHE:
        return _DEFAULT_BRANCH_CACHE[key]
    head = _capture(["git", "-C", str(repo), "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"]).strip()
    if head:
        # symbolic-ref only reads what the symref points at; it does not check
        # the target exists. A dangling origin/HEAD (branch deleted upstream)
        # must not win over a resolved origin/main, so verify before trusting.
        branch = head.removeprefix("refs/remotes/")
        if _runs(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}"]) == 0:
            _DEFAULT_BRANCH_CACHE[key] = branch
            return branch
    has_origin = _runs(["git", "-C", str(repo), "remote", "get-url", "origin"]) == 0
    candidates = ("origin/main", "origin/master") if has_origin else (
        "origin/main", "origin/master", "main", "master")
    for cand in candidates:
        if _runs(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"]) == 0:
            _DEFAULT_BRANCH_CACHE[key] = cand
            return cand
    _DEFAULT_BRANCH_CACHE[key] = ""
    return ""


def _coverage_score_bin() -> Path:
    """auditing-worktrees' coverage-score CLI, wherever it is installed.

    Read at call time rather than at import so a test (or a caller pointing at
    a worktree copy) can move it with AUDIT_WORKTREES_BIN.
    """
    root = os.environ.get("AUDIT_WORKTREES_BIN") or str(
        Path.home() / ".claude" / "skills" / "auditing-worktrees" / "bin")
    return Path(root) / "coverage-score"


def _coverage_threshold() -> int:
    """Coverage % at or above which a commit's content counts as already in base.

    Shares WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD with auditing-worktrees and
    orient rather than hardcoding a third copy of 95. A malformed value falls
    back to the default instead of being read as some other number: 0 is
    rejected with the rest, since it would call every commit landed.
    """
    raw = os.environ.get("WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD")
    if raw is not None and re.fullmatch(r"[1-9][0-9]?|100", raw):
        return int(raw)
    return 95


# Per (repo, base, sha) coverage verdict, in-process. prune_pending and --pending
# classify the same rows repeatedly, and each call re-spawns a coverage-score
# subprocess that runs a merge-tree plus two diffs -- several hundred ms on a
# large repo, and the whole reason to cache the GitHub pull lookup in
# _PULLS_CACHE applies here too. Keyed by all three so a verdict is never
# reused across bases or repos.
_COVERAGE_CACHE: dict[tuple[str, str, str], bool] = {}

# _capture's 15s budget is tuned for quick git status/log calls. A coverage
# score runs a merge-tree plus two diffs across the whole history, which on a
# large repo can exceed it; the verifier reproduced a 700k-file repo flapping
# between SCORED 100 at 14.46s and a silent "" at 15.0s run-to-run. The offline
# pass needs its own, longer budget, separate from _capture so a slow repo does
# not stretch the budget of every unrelated git call.
_COVERAGE_TIMEOUT = 60


def _coverage_landed(sha: str, base: str, repo) -> bool:
    """True when coverage-score says this commit's content is already in base.

    Offline, and it answers the squash shape ancestry cannot see. Once base
    advances between the branch point and the squash, the squash commit's tree
    is `new-base + branch changes` while the branch's own tree is still
    `old-base + branch changes`, so neither ancestry nor a tree comparison
    matches. Scoring residual content does.

    Only a score at or above the threshold is an answer. UNSCORED (a binary or
    mode-only row) and UNKNOWN (criss-cross history, a merge conflict) mean the
    scorer declined to judge, not that the work is outstanding, so the caller
    keeps asking. A missing CLI reads the same way: this closes a gap in the
    offline path, it does not make auditing-worktrees a dependency.

    The score is a percentage of content present in base, so a value outside
    0-100 is not a valid verdict -- it means a CLI regression or a mispointed
    AUDIT_WORKTREES_BIN, and is treated as "declined to judge" rather than
    trusted as landed. The subprocess gets its own longer timeout so a large
    repo cannot silently time out the offline pass.
    """
    key = (str(repo), base, sha)
    if key in _COVERAGE_CACHE:
        return _COVERAGE_CACHE[key]
    try:
        p = subprocess.run(
            [str(_coverage_score_bin()), str(repo), base, sha],
            capture_output=True, text=True, timeout=_COVERAGE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    out = (p.stdout if p.returncode == 0 else "").strip()
    m = re.fullmatch(r"SCORED (\d+)", out)
    if not m:
        return False
    score = int(m.group(1))
    if not 0 <= score <= 100:
        return False
    landed = score >= _coverage_threshold()
    _COVERAGE_CACHE[key] = landed
    return landed


def _repo_slug(repo) -> str:
    """owner/repo for gh-axi's -R flag, read from origin's URL. gh-axi rejects
    a filesystem path there (exit 1), which an earlier version of this function
    passed -- so every PR-evidenced check failed and demoted to pending forever."""
    # The synthesis ferry is asked for a local path but sometimes writes
    # `owner/repo` instead. That is already the shape -R wants, so take it
    # rather than failing a ref that is perfectly verifiable.
    if not Path(repo).is_dir() and re.fullmatch(r"[\w.-]+/[\w.-]+", str(repo)):
        return str(repo)
    url = _capture(["git", "-C", str(repo), "remote", "get-url", "origin"]).strip()
    if not url:
        return ""
    return jl.parse_github_slug(url) or ""


# Answers per (slug, sha) for the run. prune_pending classifies every queued row
# and --pending classifies them all again for listing, so without this a queue of
# N rows spends N network round trips rediscovering one unchanging fact.
_PULLS_CACHE: dict[tuple[str, str], bool] = {}
# Same shape for pr/issue views: a ledger full of rows citing the same PR or
# issue would otherwise spend one gh-axi round trip per row.
_PR_CACHE: dict[tuple[str, str], str] = {}
_ISSUE_CACHE: dict[tuple[str, str], str] = {}


def _merged_pr_carries(sha: str, slug: str):
    """True when a merged PR carries this commit, False when none does, None
    when GitHub could not be asked at all.

    The three-way return matters: "no merged PR carries it" and "the lookup
    failed" are different answers, and collapsing them would report live work
    on the strength of a dropped network call.
    """
    key = (slug, sha)
    if key not in _PULLS_CACHE:
        out = _capture(["gh-axi", "api", f"/repos/{slug}/commits/{sha}/pulls"])
        # gh-axi exits nonzero on a sha GitHub does not know, so _capture returns
        # "" for a failed lookup and "[]" for a real answer of "no pulls".
        # A failed lookup is not an answer, so it is never cached: None means
        # "could not ask", and the next call for this key must retry, not reuse
        # a stale failure.
        if out:
            # In gh-axi's TOON output a merged pull carries a quoted timestamp
            # and an open one carries a bare `null`, so the quote is the
            # discriminator, not the presence of the key.
            _PULLS_CACHE[key] = bool(re.search(r'^\s*merged_at:\s*"', out, re.M))
    return _PULLS_CACHE.get(key)


def _classify_commit(sha: str, repo) -> str:
    """LANDED once the work is in the base branch, however it got there.

    Ancestry alone cannot answer this. A squash or rebase merge rewrites the
    branch into a new commit, so the sha the ferry cited as evidence stays
    reachable from nothing and `merge-base --is-ancestor` reports it unmerged
    for work that shipped -- and this repo squash-merges its own PRs, so that is
    the ordinary case. Patch-id matching (`git cherry`) does not rescue it
    either: a squash of several commits into one matches no single patch-id,
    confirmed against jeeves PR #1, whose four commits all came back unmatched.

    Content coverage measures *content present in base*, not mergedness. A
    squash-merged branch scores SCORED 100, but so does an independently-identical
    or cherry-picked-but-never-merged commit. Treating a pure coverage match as
    LANDED therefore calls duplicate work done on the strength of coincidence.
    So coverage is authoritative only when it is the *only* possible arbiter: a
    repo with no GitHub origin, which had no answer at all before coverage
    existed. When a GitHub origin exists, GitHub is the final arbiter even when
    coverage says landed -- coverage is not even run, since it could not decide.

    The caller (classify_evidence) has already verified the sha resolves via
    cat-file -e, so this function does not re-check it.
    """
    base = _default_branch(repo)
    if not base:
        return UNKNOWN
    # Ancestry (free, offline, exact).
    if _runs(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, base]) == 0:
        return LANDED
    slug = _repo_slug(repo)
    if not slug:
        # No GitHub remote to ask. Coverage is the only offline answer for the
        # squash shape, and authoritative here -- there was no answer before it.
        if _coverage_landed(sha, base, repo):
            return LANDED
        # Coverage said no (or declined). A fetched origin that is not GitHub
        # could still have squash-merged the work and there is no way to ask it
        # further, so that case is UNKNOWN; only with no origin at all is
        # "still live" a confident OUTSTANDING.
        if _runs(["git", "-C", str(repo), "remote", "get-url", "origin"]) == 0:
            return UNKNOWN
        return OUTSTANDING
    # GitHub origin exists: GitHub is the arbiter, since coverage cannot
    # distinguish merged from coincidentally-present content.
    carried = _merged_pr_carries(sha, slug)
    if carried is None:
        return UNKNOWN
    return LANDED if carried else OUTSTANDING


def _classify_pr(num: str, repo) -> str:
    slug = _repo_slug(repo)
    if not slug:
        return UNKNOWN
    key = (slug, num)
    if key not in _PR_CACHE:
        out = _capture(["gh-axi", "pr", "view", num, "-R", slug])
        # A failed lookup is not an answer, so it is never cached: the next
        # call for this key retries instead of reusing a stale failure.
        if out:
            # gh-axi prints a YAML-ish block; the state line is `  state: merged`.
            m = re.search(r"^\s*state:\s*\"?(\w+)", out, re.M)
            if m:
                _PR_CACHE[key] = LANDED if m.group(1).lower() == "merged" else OUTSTANDING
    return _PR_CACHE.get(key, UNKNOWN)


def _classify_issue(num: str, repo) -> str:
    """An issue is landed when it is closed. `state: closed` covers both
    completed and not-planned; the ledger only needs "no longer open"."""
    slug = _repo_slug(repo)
    if not slug:
        return UNKNOWN
    key = (slug, num)
    if key not in _ISSUE_CACHE:
        out = _capture(["gh-axi", "issue", "view", num, "-R", slug])
        # A failed lookup is not an answer, so it is never cached: the next
        # call for this key retries instead of reusing a stale failure.
        if out:
            m = re.search(r"^\s*state:\s*\"?(\w+)", out, re.M)
            if m:
                _ISSUE_CACHE[key] = LANDED if m.group(1).lower() == "closed" else OUTSTANDING
    return _ISSUE_CACHE.get(key, UNKNOWN)


# Disk-backed verdict memo, keyed by f"{repo}\x1f{evidence}". The in-process
# caches above die with the process, but SKILL.md's --prune-pending step is
# immediately followed by a separate --pending invocation over the same rows,
# seconds apart; without this, the second process redoes every gh-axi round
# trip the first already paid for. A short TTL bounds the reuse to exactly
# that "run it again a few seconds later" case, never masking a real state
# change for long. Loaded once per process, saved once per process.
_MEMO_TTL = 300
_MEMO_PATH = "evidence_memo.json"
_MEMO = None


def _load_memo() -> dict:
    """The memo file is disposable: a missing, unreadable, or malformed file
    just means starting from an empty cache, never a wrong answer. "Malformed"
    covers both invalid JSON and syntactically valid JSON of the wrong shape
    (null, a list, a scalar) -- json.loads happily returns those without
    raising, so the shape is checked explicitly rather than trusted."""
    try:
        data = json.loads((jl.state_dir() / _MEMO_PATH).read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_memo() -> None:
    """Best-effort: a failed write (disk full, permissions) must never crash
    the CLI over a cache write."""
    if _MEMO is None:
        return
    try:
        (jl.state_dir() / _MEMO_PATH).write_text(json.dumps(_MEMO))
    except OSError:
        pass


def _classify_evidence_uncached(evidence: str, repo) -> str:
    """Classify evidence as LANDED / OUTSTANDING / UNKNOWN.

    A commit that exists but is not in the base branch's ancestry is a branch
    commit that still needs merging, not shipped work; a PR that is open is the
    same. Both report OUTSTANDING so the card can say so instead of silently
    presenting either as done.

    Evidence citing several refs is LANDED only when every ref landed -- half a
    change being merged is not the change being merged. A single OUTSTANDING ref
    dominates, since that is the part still needing action; UNKNOWN otherwise.
    """
    refs = _extract_refs(evidence)
    if not refs or not repo:
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
            if not Path(repo).is_dir():
                # The repo directory itself does not exist, so nothing could
                # be checked at all -- UNKNOWN, not a confident OUTSTANDING.
                verdicts.append(UNKNOWN)
                continue
            target = (Path(repo) / value).resolve()
            in_repo = target.is_relative_to(Path(repo).resolve())
            verdicts.append(LANDED if in_repo and target.exists() else OUTSTANDING)
    if not verdicts:
        return UNKNOWN
    if OUTSTANDING in verdicts:
        return OUTSTANDING
    return LANDED if all(v == LANDED for v in verdicts) else UNKNOWN


def classify_evidence(evidence: str, repo) -> str:
    """Memoized entry point: same verdict as _classify_evidence_uncached, with
    a short-TTL disk cache so consecutive processes over the same rows do not
    redo each other's gh-axi round trips.

    UNKNOWN is never cached, the same rule the inner caches (_PULLS_CACHE,
    _PR_CACHE, _ISSUE_CACHE) already apply to a failed lookup: it means
    "could not determine" rather than a real answer, and caching it would let
    a transient gh-axi outage outlive itself for up to _MEMO_TTL seconds --
    exactly the "run --prune-pending then --pending seconds apart" workflow
    this memo exists to speed up would then see a stale UNKNOWN instead of a
    retry. A malformed entry (missing "t"/"verdict", e.g. from schema drift)
    is treated as a cache miss rather than trusted.
    """
    global _MEMO
    if _MEMO is None:
        _MEMO = _load_memo()
        atexit.register(_save_memo)
    key = f"{repo}\x1f{evidence}"
    hit = _MEMO.get(key)
    if (
        isinstance(hit, dict)
        and isinstance(hit.get("t"), (int, float))
        and hit.get("verdict") in (LANDED, OUTSTANDING, UNKNOWN)
        and time.time() - hit["t"] < _MEMO_TTL
    ):
        return hit["verdict"]
    verdict = _classify_evidence_uncached(evidence, repo)
    if verdict != UNKNOWN:
        _MEMO[key] = {"verdict": verdict, "t": time.time()}
    return verdict


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


def _apply_check_gate(line: str, evidence: str, repo: str, seen: jl.SeenStore) -> bool:
    """Verify evidence and apply check mutation to ledger and seen store.

    Returns True if evidence verified and check was applied.
    Returns False if evidence failed verification.
    Raises AmbiguousMatch if evidence verified but ledger match was not unique/found.
    """
    if not verify_evidence(evidence, repo):
        return False
    apply_check(line, evidence or "verified")
    seen.set_status(jl.line_hash(line), "done")
    return True


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
            evidence = mut.get("evidence", "")
            repo = mut.get("repo")
            try:
                if _apply_check_gate(line, evidence, repo, seen):
                    counts["applied"] += 1
                else:
                    _push_pending(mut, "evidence did not verify")
                    counts["pending"] += 1
                    jl.log(f"check demoted to pending (evidence): {mut}")
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
            # A line ambiguous in done/dismissed is NOT "resolved" -- a
            # duplicated match is a reason to keep the row for a human (same
            # rationale as the open-section AMBIGUOUS case above), not to drop
            # it as moot. The AMBIGUOUS sentinel is truthy, so it must be
            # checked explicitly rather than folded into `known`.
            done_hit = _match(sections, line, "done")
            dismissed_hit = _match(sections, line, "dismissed")
            if done_hit is AMBIGUOUS or dismissed_hit is AMBIGUOUS:
                kept.append(row)
                counts["kept"] += 1
                jl.log(f"prune-pending: kept (ambiguous done/dismissed match): {line}")
                continue
            known = done_hit or dismissed_hit
            counts["moot" if known else "stale"] += 1
            jl.log(f"prune-pending: dropped ({'moot' if known else 'stale'}): {line}")
            continue
        evidence = row.get("evidence", "")
        repo = row.get("repo")
        try:
            if _apply_check_gate(line, evidence, repo, seen):
                counts["applied"] += 1
                jl.log(f"prune-pending: applied: {line}")
                sections = parse_ledger(ledger_path().read_text())
            else:
                kept.append(row)
                counts["kept"] += 1
        except AmbiguousMatch:
            kept.append(row)
            counts["kept"] += 1
    seen.save()
    save_pending(kept)
    jl.log(f"prune-pending: {counts}")
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
