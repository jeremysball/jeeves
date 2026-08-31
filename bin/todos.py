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

# AXI — Agent eXperience Interface constants
BIN_PATH = str(Path(__file__).resolve()).replace(str(Path.home()), "~")
DESCRIPTION = "Jeeves todo ledger — local-first grounding for agentic work"

def _toon_escape(s: str) -> str:
    if s is None:
        return '""'
    s = str(s)
    # TOON quoting: only when needed (newline, comma, quote, colon, bracket)
    if any(c in s for c in ['\n', '"', ',', ':', '[', ']', '{', '}']) or s.strip() != s or s == "":
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    return s

def _toon_row(values, fields):
    parts = []
    for v, f in zip(values, fields):
        if f in ("evidence", "line", "repo", "body"):
            # truncate long text fields in default view
            sv = str(v) if v is not None else ""
            if len(sv) > 500:
                sv = sv[:500] + f" ... (truncated, {len(str(v))} chars total)"
            parts.append(_toon_escape(sv))
        else:
            parts.append(_toon_escape(v))
    return ",".join(parts)

def _emit_toon_header(name, count, fields):
    # e.g. pending[3]{line,state}:
    if fields:
        return f"{name}[{count}]{{{','.join(fields)}}}:"
    return f"{name}[{count}]:"

def _print_axi_error(msg: str, help_hint: str = "", exit_code: int = 2):
    # Structured errors to stdout per AXI 6.2, exit 2 for usage, 1 for error
    print(f"error: {msg}")
    if help_hint:
        print(f"help: {help_hint}")
    sys.exit(exit_code)

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


# Trailing prose is the norm, not the exception: the synthesis ferry writes
# "commit 3c33869 reset" and "PR #419 merged", never a bare ref. Anchoring
# these on $ meant every real PR check fell through to False and queued in
# pending.json forever.
HEX_RE = re.compile(r"^commit ([0-9a-f]{7,40})\b", re.I)
PR_RE = re.compile(r"^PR #(\d+)\b", re.I)
ISSUE_RE = re.compile(r"^issue #(\d+)\b", re.I)
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


def _pending_subject(line: str) -> str:
    """Identity for matching re-queued checks to the row already in the queue.

    Same normalized-equality rule `find_match` applies to ledger lines, minus
    the checkbox bullet: the one real variance observed between hours is the
    ferry quoting the same ledger line with and without its leading "- [ ] ".
    Two subjects that collide here collide in find_match too, so folding them
    is consistent with how the rest of the ledger treats them."""
    s = (line or "").strip()
    for b in ("- [ ] ", "- [ ]", "- [x] ", "- [x]", "- [] "):
        if s.startswith(b):
            s = s[len(b):].strip()
            break
    return jl.normalize(s)


def _push_pending(mut: dict, reason: str) -> str:
    """Queue a demoted check, folding it into an existing row for the same
    subject instead of appending a copy.

    The synthesis ferry re-proposes the same check every hour whose slices
    still show stale-looking evidence, and _push_pending used to append
    unconditionally. One stuck row reached eight copies in the queue (the
    taskferry fix-issue checks, 2026-08-14 through 08-19), each paying its
    own re-verify in prune and each stealing a slot from real signal in
    --pending's 30-row window. The fold keeps the subject's age (`queued`
    stays at the first demotion, which is what the card reads as
    stale-evidence standing), takes the freshest evidence/repo/reason from
    the latest attempt, and counts attempts in `seen` so the recurrence
    reads as signal instead of noise."""
    items = load_pending()
    op = mut.get("op") or "check"
    subject = _pending_subject(mut.get("line", ""))
    for row in items:
        if (row.get("op") or "check") == op and _pending_subject(row.get("line", "")) == subject:
            row.update({k: v for k, v in mut.items() if k in ("line", "evidence", "repo", "kind", "source")})
            row["reason"] = reason
            row["seen"] = int(row.get("seen", 1)) + 1
            save_pending(items)
            return "merged"
    items.append({**mut, "op": op, "reason": reason, "seen": 1,
                  "queued": jl.now_et().isoformat(timespec="seconds")})
    save_pending(items)
    return "new"


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
                # A re-queue of a subject already in the queue folded the row
                # instead of growing the queue: count it deduped, like the add
                # op's re-ad of a known line, so `pending` tracks queue length.
                outcome = _push_pending(mut, "evidence did not verify")
                counts["pending" if outcome == "new" else "deduped"] += 1
                jl.log(f"check demoted to pending (evidence): {mut}")
                continue
            try:
                apply_check(line, mut.get("evidence", "verified"))
                seen.set_status(h, "done")
                counts["applied"] += 1
            except AmbiguousMatch:
                outcome = _push_pending(mut, "no unique ledger match")
                counts["pending" if outcome == "new" else "deduped"] += 1
                jl.log(f"check demoted to pending (match): {mut}")
        else:
            counts["failed"] += 1
            jl.log(f"unknown op: {mut}")
    seen.save()
    return counts


def prune_pending() -> dict:
    """Re-run the gates over the pending queue and drain what no longer belongs.

    pending.json is append-only in spirit: _push_pending folds a re-queued
    subject into its existing row rather than adding a second, and before this
    existed nothing ever removed anything, so a check queued against a
    since-merged PR sat in the queue forever and every wake re-reported it as
    a live loose end.

    Each row resolves one of five ways:
      applied  - evidence now shows LANDED and the ledger line is still open,
                 so the check it was demoted from finally goes through
      moot     - the ledger line is no longer open (checked or dismissed since),
                 so there is nothing left for this row to do
      stale    - the line was never in the ledger at all
      kept     - still genuinely pending; evidence has not landed yet
      merged   - a kept row's same-subject sibling, folded into that survivor
                 (present as a count only while any coalescing actually fired;
                 kept + merged together still equals the kept rows drained in)
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
    # Survivors fold down to one row per subject; the live pre-fold queue
    # reached eight copies of the same 2026-08-17 taskferry check, each
    # paying its own re-verify and each eating a --pending display slot.
    # The newest row in file order wins as the base (freshest evidence), the
    # first demotion's `queued` survives (age is the stale-evidence signal),
    # and `seen` sums the attempts.
    survivors: list = []
    slot: dict = {}
    merged = 0
    for row in kept:
        key = (row.get("op") or "check", _pending_subject(row.get("line", "")))
        if key in slot:
            prev = survivors[slot[key]]
            row["seen"] = int(prev.get("seen", 1)) + int(row.get("seen", 1))
            if prev.get("queued"):
                row["queued"] = prev["queued"]
            survivors[slot[key]] = row
            merged += 1
        else:
            slot[key] = len(survivors)
            survivors.append(row)
    counts["kept"] = len(survivors)
    if merged:
        counts["merged"] = merged
    save_pending(survivors)
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


class AxiParser(argparse.ArgumentParser):
    def error(self, message):
        # AXI 6: help always passes, even with unknown flags; stdout is structured, exit 2 for usage
        if "-h" in sys.argv or "--help" in sys.argv:
            print(_axi_help_text(self.prog))
            sys.exit(0)
        m = re.search(r"unrecognized arguments: (.+)", message)
        if m:
            flags = m.group(1).strip()
            valid = ", ".join(a.option_strings[0] for a in self._actions if a.option_strings)
            print(f"error: unknown flag {flags} for `{self.prog}`")
            print(f"help: valid flags for `{self.prog}`: {valid} (--help always allowed)")
            sys.exit(2)
        # missing value, e.g. "argument --add: expected one argument"
        if "expected one argument" in message or "expected at least one argument" in message:
            print(f"error: {message}")
            print(f"help: Run `{self.prog} --help` for details")
            sys.exit(2)
        print(f"error: {message}")
        print(f"help: Run `{self.prog} --help` for details")
        sys.exit(2)
    def print_help(self, file=None):
        # Override to emit AXI help with bin/description
        if file is None:
            file = sys.stdout
        print(_axi_help_text(self.prog), file=file)

def _axi_help_text(prog="todos.py"):
    return "\n".join([
        f"bin: {BIN_PATH}",
        f"description: {DESCRIPTION}",
        f"usage: {prog} [options]",
        "options:",
        "  --add <text>                 Add a new todo (requires --kind/--source)",
        "  --dismiss <query>            Dismiss an open todo by normalized match",
        "  --delta                      Show ledger counts (open/done/dismissed/pending)",
        "  --pending                    List pending checks with live evidence state",
        "  --prune-pending              Re-verify pending queue, drain resolved, coalesce duplicates",
        "  --wake                       Mark wake time",
        "  --reconcile                  Reconcile ledger with SeenStore",
        "  --apply-mutations <file>     Apply ferry mutations JSON",
        "  --fields <a,b,c>             Limit output fields (delta/pending)",
        "  --limit <n>                  Limit pending rows shown (default 30)",
        "  --full                       Show complete evidence without truncation",
        "  --format <toon|json>         Output format (default toon)",
        "  -h, --help                   Show this help",
        "examples:",
        "  todos.py                     # dashboard (content first)",
        "  todos.py --delta --fields open,pending",
        "  todos.py --pending --limit 10 --fields line,state",
        "  todos.py --dismiss \"fix auth bug\"",
    ])

def _print_delta(data, fields=None, fmt="toon"):
    if fmt == "json":
        print(json.dumps(data, indent=1))
        return
    print(f"bin: {BIN_PATH}")
    print(f"description: {DESCRIPTION}")
    # Handle filtered fields: only print what was requested
    if fields:
        parts = []
        for k in ["open", "done", "dismissed", "pending"]:
            if k in data:
                parts.append(f"{data[k]} {k}")
        if parts:
            print(f"ledger: {', '.join(parts)}")
        if "top_recurrence" in data and data.get("top_recurrence"):
            print(_emit_toon_header("top", len(data["top_recurrence"]), ["line", "count"]))
            for line, cnt in data["top_recurrence"]:
                sv = line[:120] + (" ... (truncated)" if len(line) > 120 else "")
                print(f"  {_toon_escape(sv)},{cnt}")
    else:
        print(f"ledger: {data['open']} open, {data['done']} done, {data['dismissed']} dismissed, {data['pending']} pending")
        if data.get("top_recurrence"):
            print(_emit_toon_header("top", len(data["top_recurrence"]), ["line", "count"]))
            for line, cnt in data["top_recurrence"]:
                sv = line[:120] + (" ... (truncated)" if len(line) > 120 else "")
                print(f"  {_toon_escape(sv)},{cnt}")
        if data.get("pending", 0) > 0:
            print("help[2]:")
            print("  Run `todos.py --pending --limit 10` for details")
            print("  Run `todos.py --pending --full` to see complete evidence")
        elif data.get("top_recurrence"):
            # still show help even when pending 0 but top exists
            pass
    # help when filtered but pending still >0
    if fields and data.get("pending", 0) > 0:
        print("help[2]:")
        print("  Run `todos.py --pending --limit 10` for details")

def _print_pending(rows, fields=None, limit=30, full=False, fmt="toon"):
    if fmt == "json":
        print(json.dumps(rows, indent=1))
        return
    # definitive empty state per AXI 5
    if not rows:
        print("pending: 0 pending checks found")
        print(f"bin: {BIN_PATH}")
        return
    # aggregates
    total = len(rows)
    show = rows[:limit] if limit else rows
    # minimal schema per AXI 2: default 4 fields
    default_fields = ["line", "state", "repo", "evidence"]
    use_fields = fields.split(",") if fields else default_fields
    # normalize field names
    use_fields = [f.strip() for f in use_fields if f.strip()]
    # emit header
    print(f"bin: {BIN_PATH}")
    print(f"description: {DESCRIPTION}")
    print(f"count: {len(show)} of {total} total")
    print(_emit_toon_header("pending", len(show), use_fields))
    for r in show:
        vals = []
        for f in use_fields:
            v = r.get(f, "")
            if not full and f in ("line", "evidence") and isinstance(v, str) and len(v) > 500:
                v = v[:500] + f" ... (truncated, {len(r.get(f,''))} chars total)"
            vals.append(v)
        print(f"  {_toon_row(vals, use_fields)}")
    # help hints
    if total > len(show):
        print(f"help: showing {len(show)} of {total}; Run `todos.py --pending --limit {total}` for all")
    if any(not full and len(str(r.get("evidence",""))) > 500 for r in show):
        print("help: Run `todos.py --pending --full` to see complete evidence")
    print("help[2]:")
    print("  Run `todos.py --delta` for ledger counts")
    print("  Run `todos.py --dismiss \"<line>\"` to dismiss")

def main() -> None:
    # Content first per AXI 8: no args shows dashboard (not help)
    if len(sys.argv) == 1:
        # Dashboard: live counts + digest preview
        data = delta_summary()
        print(f"bin: {BIN_PATH}")
        print(f"description: {DESCRIPTION}")
        print(f"ledger: {data['open']} open, {data['done']} done, {data['dismissed']} dismissed, {data['pending']} pending")
        if data["pending"] > 0:
            rows = [{**r, "state": classify_evidence(r.get("evidence", ""), r.get("repo"))} for r in load_pending()[:3]]
            if rows:
                print(_emit_toon_header("pending", len(rows), ["line", "state"]))
                for r in rows:
                    line = r["line"][:80] + ("..." if len(r["line"]) > 80 else "")
                    print(f"  {_toon_escape(line)},{r['state']}")
        elif data["open"] == 0:
            print("pending: 0 pending checks found — ledger clean")
        print("help[3]:")
        print("  Run `todos.py --delta` for full counts")
        print("  Run `todos.py --pending` for pending checks")
        print("  Run `todos.py --help` for all commands")
        sys.exit(0)

    ap = AxiParser(prog="todos.py", add_help=False)
    ap.add_argument("-h", "--help", action="store_true")
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
    ap.add_argument("--fields")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--format", choices=["toon", "json"], default="toon")
    # manually handle --help always passes per AXI 6
    args = None
    try:
        args = ap.parse_args()
    except SystemExit as e:
        # AxiParser already printed structured error and exited; propagate
        sys.exit(e.code)
    if args.help:
        print(_axi_help_text())
        sys.exit(0)
    # dispatch with AXI output channels: stdout structured, stderr diagnostics
    try:
        if args.add:
            # validate required
            if not args.add.strip():
                _print_axi_error("--add requires a non-empty value", "todos.py --add \"<text>\" --kind loose-end --source myproj", 2)
            res = apply_add(args.add, args.kind, args.source)
            if args.format == "json":
                print(json.dumps({"result": res}))
            else:
                print(f"todo: added \"{args.add}\" (no-op if already open)" if "already" in res else f"todo: {res}")
                print("help: Run `todos.py --delta` to see counts")
            sys.exit(0)
        elif args.dismiss:
            try:
                res = apply_dismiss(args.dismiss)
            except AmbiguousMatch as e:
                # structured error to stdout per AXI 6, exit 1 (intent cannot be satisfied) vs 2 for usage
                print(f"error: {e}")
                print("help: Run `todos.py --pending --fields line` to see matching lines")
                sys.exit(1)
            if args.format == "json":
                print(json.dumps({"result": res}))
            else:
                print(f"todo: dismissed \"{args.dismiss}\"")
                # idempotent no-op already handled by find_match raising; dismissed is success
            sys.exit(0)
        elif args.apply_mutations:
            muts = json.loads(Path(args.apply_mutations).read_text())
            out = apply_mutations(muts)
            if args.format == "json":
                print(json.dumps(out, indent=1))
            else:
                print(f"mutations: {out.get('applied',0)} applied, {out.get('pending',0)} pending, {out.get('deduped',0)} deduped")
                if out.get("pending"):
                    print("help: Run `todos.py --pending` to see pending")
            sys.exit(0)
        elif args.reconcile:
            out = reconcile()
            if args.format == "json":
                print(json.dumps(out, indent=1))
            else:
                print(f"reconcile: {out.get('registered',0)} registered, {out.get('dismissed',0)} dismissed")
            sys.exit(0)
        elif args.wake:
            wake()
            if args.format != "json":
                print("wake: marked")
            sys.exit(0)
        elif args.delta:
            data = delta_summary()
            # --fields filtering for delta
            if args.fields:
                allowed = {"open","done","dismissed","pending","top_recurrence"}
                req = [f.strip() for f in args.fields.split(",")]
                filtered = {k: v for k, v in data.items() if k in req}
                # also handle invalid fields
                invalid = [f for f in req if f not in allowed]
                if invalid:
                    print(f"error: unknown field {invalid[0]} for --delta")
                    print(f"help: valid fields for --delta: {', '.join(sorted(allowed))}")
                    sys.exit(2)
                data = filtered
            _print_delta(data, fields=args.fields, fmt=args.format)
            sys.exit(0)
        elif args.prune_pending:
            out = prune_pending()
            if args.format == "json":
                print(json.dumps(out, indent=1))
            else:
                # aggregates
                out_str = (f"prune: {out.get('applied',0)} applied, {out.get('moot',0)} moot, "
                           f"{out.get('stale',0)} stale, {out.get('kept',0)} kept")
                if out.get("merged"):
                    out_str += f" ({out['merged']} merged)"
                print(out_str)
                if out.get("kept",0) == 0 and out.get("applied",0) == 0:
                    print("pending: 0 pending checks found")
                else:
                    print("help: Run `todos.py --pending` to see remaining")
            sys.exit(0)
        elif args.pending:
            rows = [{**r, "state": classify_evidence(r.get("evidence", ""), r.get("repo"))} for r in load_pending()]
            limit = args.limit if args.limit is not None else 30
            # --fields validation
            if args.fields:
                valid = {"line","evidence","repo","state","op","reason","queued","seen","kind","source"}
                req = [f.strip() for f in args.fields.split(",")]
                invalid = [f for f in req if f not in valid]
                if invalid:
                    print(f"error: unknown field {invalid[0]} for --pending")
                    print(f"help: valid fields for --pending: {', '.join(sorted(valid))}")
                    sys.exit(2)
            _print_pending(rows, fields=args.fields, limit=limit, full=args.full, fmt=args.format)
            sys.exit(0)
        else:
            # no recognized flag but not no-args (e.g. only --kind)
            print(_axi_help_text())
            sys.exit(0)
    except AmbiguousMatch as e:
        print(f"error: {e}")
        print("help: Run `todos.py --help` for valid flags")
        sys.exit(1)
    except Exception as e:
        # never leak stack trace per AXI 6
        print(f"error: {e}")
        print("help: Run `todos.py --help` for details")
        sys.exit(1)


if __name__ == "__main__":
    main()
