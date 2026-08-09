"""Spec-driven tests for jeeves evidence verification (SPEC Parts 1-2).

Written blind against /tmp/jeeves-spec/SPEC.md. The implementation in
skills/jeeves/bin/ is an old pre-fix version and is expected to fail most
of these tests; that is the point. Do not edit the modules to make them
pass.
"""

import subprocess
from pathlib import Path

import pytest

import jeeves_lib as jl
import todos as td


# ---------------------------------------------------------------------------
# git helpers: real repos, real commits, no mocks
# ---------------------------------------------------------------------------

def _commit(repo, fname, content, msg):
    (repo / fname).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", msg], check=True)
    return _head(repo)


def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _git_repo(tmp_path, branch="main"):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
    h = _commit(repo, "f.txt", "x", "init")
    return repo, h


def _side_commit(repo, branch="feature"):
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)
    return _commit(repo, "g.txt", "y", "side work")


def _bare_origin(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


def _patch_subprocess(monkeypatch, calls):
    """Make every subprocess entry point fail like a missing executable.

    Records the argv of each call so tests can assert on what was invoked.
    """
    def fake_run(args, **kw):
        calls.append(args)
        raise FileNotFoundError("no network")

    patched = False
    if hasattr(td, "subprocess"):
        monkeypatch.setattr(td.subprocess, "run", fake_run)
        monkeypatch.setattr(td.subprocess, "check_output", fake_run)
        monkeypatch.setattr(td.subprocess, "Popen", fake_run)
        patched = True
    if hasattr(td, "run"):
        monkeypatch.setattr(td, "run", fake_run)
        patched = True
    assert patched, "todos.py exposes no subprocess entry point to patch"


# ---------------------------------------------------------------------------
# Part 1: normalize() and line_hash()
# ---------------------------------------------------------------------------

def test_normalize_removes_checkbox():
    assert jl.normalize("- [ ] fix pinentry") == "fix pinentry"
    assert jl.normalize("- [x] done thing") == "done thing"


def test_normalize_collapses_whitespace_trims_and_casefolds():
    assert jl.normalize("  Fix   Pinentry  TTY handling  ") == "fix pinentry tty handling"


def test_normalize_applies_nfkc():
    # Fullwidth latin letters, separated by ideographic spaces (U+3000). NFKC
    # folds both to ascii. U+FF40 is a fullwidth *grave accent*, not a space --
    # using it here asserted that NFKC turns a backtick into a space.
    assert jl.normalize("\uff46\uff49\uff58\u3000\uff54\uff48\uff45\u3000\uff54\uff48\uff49\uff4e\uff47") == "fix the thing"


def test_normalize_strips_stacked_provenance_groups():
    line = "- [ ] dead thing (jeeves: loose-end, x, 2026-08-09) (dismissed 2026-08-09)"
    assert jl.normalize(line) == "dead thing"


def test_normalize_strips_any_number_of_trailing_provenance_groups():
    line = "- [ ] thing (jeeves: a, b, 2026-07-30) (jeeves: c, d, 2026-08-01) (dismissed 2026-08-09)"
    assert jl.normalize(line) == "thing"


def test_normalize_preserves_non_provenance_parentheses():
    assert jl.normalize("- [ ] fix parse(x) handling") == "fix parse(x) handling"
    assert jl.normalize("- [ ] read the (docs)") == "read the (docs)"


def test_normalize_full_pipeline():
    line = "  - [ ]  Fix   Pinentry  TTY  (jeeves: loose-end, hearth, 2026-07-29) (dismissed 2026-08-09)  "
    assert jl.normalize(line) == "fix pinentry tty"


def test_line_hash_equal_for_stacked_provenance_and_bare():
    line = "- [ ] dead thing (jeeves: loose-end, x, 2026-08-09) (dismissed 2026-08-09)"
    assert jl.normalize(line) == jl.normalize("dead thing")
    assert jl.line_hash(line) == jl.line_hash("dead thing")


# ---------------------------------------------------------------------------
# Part 2: verdict constants
# ---------------------------------------------------------------------------

def test_verdict_constants_distinct_strings():
    assert isinstance(td.LANDED, str)
    assert isinstance(td.OUTSTANDING, str)
    assert isinstance(td.UNKNOWN, str)
    assert len({td.LANDED, td.OUTSTANDING, td.UNKNOWN}) == 3


# ---------------------------------------------------------------------------
# Part 2: _extract_refs()
# ---------------------------------------------------------------------------

def test_extract_refs_pr():
    assert td._extract_refs("PR #302") == [("pr", "302")]


def test_extract_refs_two_commits_with_keyword():
    assert td._extract_refs("commit 062563c, commit e0056ed") == [
        ("commit", "062563c"), ("commit", "e0056ed")]


def test_extract_refs_two_commits_second_without_keyword():
    assert td._extract_refs("commit 062563c, e0056ed") == [
        ("commit", "062563c"), ("commit", "e0056ed")]


def test_extract_refs_commits_then_prs():
    assert td._extract_refs("commit dcfcab4 + 01442dd (PR #114, PR #115)") == [
        ("commit", "dcfcab4"), ("commit", "01442dd"), ("pr", "114"), ("pr", "115")]


def test_extract_refs_interleaved_commits_and_prs_in_order():
    assert td._extract_refs("commit e0056ed (#364), 062563c (#360)") == [
        ("commit", "e0056ed"), ("pr", "364"), ("commit", "062563c"), ("pr", "360")]


def test_extract_refs_bare_hex_without_keyword():
    assert td._extract_refs("062563c and e0056ed shipped") == [
        ("commit", "062563c"), ("commit", "e0056ed")]


@pytest.mark.parametrize("keyword", [
    "close", "closes", "closed", "fix", "fixes", "fixed",
    "resolve", "resolves", "resolved",
])
def test_extract_refs_github_closing_keywords_are_issues(keyword):
    # GitHub's own auto-close keywords are a far more common phrasing than
    # "issue #N" for a synthesis ferry to quote from a commit/PR body.
    # ISSUE_SCAN_RE only matched the literal word "issue(s)", so these fell
    # through to the keyword-less PR_SCAN_RE and misclassified as a PR.
    assert td._extract_refs(f"{keyword} #391") == [("issue", "391")]


def test_extract_refs_pr_and_closing_keyword_issue_together():
    # The trickiest interaction: a PR ref and a keyword-only issue ref in the
    # same string must both extract, in position order, with the PR hit not
    # swallowed by the issue's `taken` de-dupe (they're at different positions).
    assert td._extract_refs("PR #114 fixes #391") == [
        ("pr", "114"), ("issue", "391")]


def test_extract_refs_file_form():
    assert td._extract_refs("file f.txt") == [("file", "f.txt")]
    assert td._extract_refs("file docs/notes with spaces.md") == [("file", "docs/notes with spaces.md")]


def test_extract_refs_file_form_hex_looking_path_is_not_a_commit():
    assert td._extract_refs("file abc1234") == [("file", "abc1234")]


def test_extract_refs_no_reference_returns_empty():
    assert td._extract_refs("vibes") == []
    assert td._extract_refs("") == []


def test_extract_refs_hex_lettered_word_is_a_candidate():
    assert td._extract_refs("defaced") == [("commit", "defaced")]


# ---------------------------------------------------------------------------
# Part 2: classify_evidence() — repo/ref gating
# ---------------------------------------------------------------------------

def test_classify_unknown_without_repo():
    assert td.classify_evidence("commit abc1234", None) == td.UNKNOWN
    assert td.classify_evidence("file f.txt", None) == td.UNKNOWN
    assert td.classify_evidence("PR #1", None) == td.UNKNOWN


def test_classify_unknown_without_references(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("vibes", str(repo)) == td.UNKNOWN


def test_classify_unknown_for_nonexistent_repo():
    assert td.classify_evidence("commit abc1234", "/nonexistent/path") == td.UNKNOWN


# ---------------------------------------------------------------------------
# Part 2: classify_evidence() — commit resolution and base branch
# ---------------------------------------------------------------------------

def test_classify_landed_commit_on_base_branch(tmp_path):
    repo, h = _git_repo(tmp_path)
    assert td.classify_evidence(f"commit {h[:10]}", str(repo)) == td.LANDED


def test_classify_outstanding_commit_on_side_branch(tmp_path):
    repo, _ = _git_repo(tmp_path)
    side = _side_commit(repo)
    assert td.classify_evidence(f"commit {side[:10]}", str(repo)) == td.OUTSTANDING


def test_classify_unknown_for_unresolvable_commit(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("commit deadbeef00", str(repo)) == td.UNKNOWN


def test_hex_lettered_word_discarded_at_classification(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("defaced", str(repo)) == td.UNKNOWN


def test_base_branch_falls_back_to_master(tmp_path):
    repo, h = _git_repo(tmp_path, branch="master")
    assert td.classify_evidence(f"commit {h[:10]}", str(repo)) == td.LANDED


def test_base_branch_falls_back_to_main(tmp_path):
    repo, h = _git_repo(tmp_path, branch="main")
    assert td.classify_evidence(f"commit {h[:10]}", str(repo)) == td.LANDED


def test_commit_unknown_when_no_base_branch_resolves(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "weird", str(repo)], check=True)
    h = _commit(repo, "f.txt", "x", "init")
    assert td.classify_evidence(f"commit {h[:10]}", str(repo)) == td.UNKNOWN


def test_commit_landed_via_origin_head(tmp_path):
    origin = _bare_origin(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    h = _commit(repo, "f.txt", "x", "init")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "set-head", "origin", "main"], check=True)
    side = _side_commit(repo)
    assert td.classify_evidence(f"commit {h[:10]}", str(repo)) == td.LANDED
    assert td.classify_evidence(f"commit {side[:10]}", str(repo)) == td.OUTSTANDING


def test_base_branch_prefers_origin_head_over_origin_main(tmp_path):
    origin = _bare_origin(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(repo, "f.txt", "x", "init")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "trunk"], check=True)
    h_trunk = _commit(repo, "g.txt", "y", "trunk work")
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "trunk"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "set-head", "origin", "trunk"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    h_main2 = _commit(repo, "h.txt", "z", "main work after trunk")
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
    assert td.classify_evidence(f"commit {h_trunk[:10]}", str(repo)) == td.LANDED
    assert td.classify_evidence(f"commit {h_main2[:10]}", str(repo)) == td.OUTSTANDING


# ---------------------------------------------------------------------------
# Part 2: classify_evidence() — combination rules
# ---------------------------------------------------------------------------

def test_all_discarded_candidates_unknown(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("commit deadbeef00, commit 1234567", str(repo)) == td.UNKNOWN


def test_discarded_candidate_does_not_drag_landed_verdict(tmp_path):
    repo, h = _git_repo(tmp_path)
    assert td.classify_evidence(f"commit deadbeef00, commit {h[:10]}", str(repo)) == td.LANDED


def test_discarded_candidate_does_not_drag_outstanding_verdict(tmp_path):
    repo, _ = _git_repo(tmp_path)
    side = _side_commit(repo)
    assert td.classify_evidence(f"commit deadbeef00, commit {side[:10]}", str(repo)) == td.OUTSTANDING


def test_any_outstanding_reference_makes_verdict_outstanding(tmp_path):
    repo, h = _git_repo(tmp_path)
    side = _side_commit(repo)
    assert td.classify_evidence(f"commit {h[:10]}, commit {side[:10]}", str(repo)) == td.OUTSTANDING


def test_all_landed_references_landed(tmp_path):
    repo, h1 = _git_repo(tmp_path)
    h2 = _commit(repo, "g.txt", "y", "second main commit")
    assert td.classify_evidence(f"commit {h1[:10]}, commit {h2[:10]}", str(repo)) == td.LANDED


def test_mixed_landed_commit_and_unknown_pr_is_unknown(tmp_path):
    repo, h = _git_repo(tmp_path)
    assert td.classify_evidence(f"commit {h[:10]} (PR #1)", str(repo)) == td.UNKNOWN


# ---------------------------------------------------------------------------
# Part 2: classify_evidence() — file evidence
# ---------------------------------------------------------------------------

def test_file_evidence_landed_when_exists(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("file f.txt", str(repo)) == td.LANDED


def test_file_evidence_outstanding_when_missing(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("file nope.txt", str(repo)) == td.OUTSTANDING


def test_file_evidence_unknown_without_repo():
    assert td.classify_evidence("file f.txt", None) == td.UNKNOWN


def test_none_evidence_is_unknown_not_a_crash(tmp_path):
    # A ferry mutation with a JSON-null evidence field survives parsing as a
    # present-but-None value; `mut.get("evidence", "")`'s default only fires
    # when the key is absent, so None reaches classify_evidence here too.
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence(None, str(repo)) == td.UNKNOWN
    assert td.verify_evidence(None, str(repo)) is False


def test_file_evidence_is_whole_string_and_absorbs_trailing_refs(tmp_path):
    # Per spec: `file <path>` yields exactly one file ref "and nothing else",
    # and a path may contain spaces. So everything after `file ` is the path --
    # here a path that does not exist, hence OUTSTANDING. The commit is not
    # separately extracted, and must not rescue the verdict to LANDED.
    repo, h = _git_repo(tmp_path)
    evidence = f"file f.txt, commit {h[:10]}"
    assert td._extract_refs(evidence) == [("file", f"f.txt, commit {h[:10]}")]
    assert td.classify_evidence(evidence, str(repo)) == td.OUTSTANDING


def test_missing_file_with_landed_commit_is_outstanding(tmp_path):
    repo, h = _git_repo(tmp_path)
    assert td.classify_evidence(f"file nope.txt, commit {h[:10]}", str(repo)) == td.OUTSTANDING


def test_file_evidence_absolute_path_does_not_escape_repo(tmp_path):
    # Path(repo) / value discards `repo` entirely when `value` is absolute
    # (pathlib behavior), so `file /etc/passwd` used to resolve to a real
    # host file and report LANDED regardless of the repo's own contents.
    repo, _ = _git_repo(tmp_path)
    assert Path("/etc/passwd").exists()  # the escape only matters if this is real
    assert td.classify_evidence("file /etc/passwd", str(repo)) == td.OUTSTANDING


def test_file_evidence_dotdot_traversal_does_not_escape_repo(tmp_path):
    repo, _ = _git_repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    assert td.classify_evidence(f"file ../{outside.name}", str(repo)) == td.OUTSTANDING


# ---------------------------------------------------------------------------
# Part 2: classify_evidence() — pr references and remote slug
# ---------------------------------------------------------------------------

def test_pr_unknown_without_origin_remote(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    calls = []
    _patch_subprocess(monkeypatch, calls)
    assert td.classify_evidence("PR #42", str(repo)) == td.UNKNOWN


@pytest.mark.parametrize("url", [
    "git@github.com:acme/widget.git",
    "https://github.com/acme/widget.git",
    "https://github.com/acme/widget",
])
def test_pr_lookup_derives_owner_repo_slug_from_origin(url, tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)
    calls = []
    # Only the pr lookup is stubbed out. Failing *every* subprocess call also
    # killed the `git remote get-url origin` the slug is read from, so the slug
    # came back empty and the lookup was never reached -- the assertion below
    # could then only ever fail.
    real_run = subprocess.run

    def fake_run(args, **kw):
        calls.append(list(args))
        if args and args[0] == "git":
            return real_run(args, **kw)
        raise FileNotFoundError("no network")

    monkeypatch.setattr(td.subprocess, "run", fake_run)
    assert td.classify_evidence("PR #42", str(repo)) == td.UNKNOWN
    assert any("acme/widget" in " ".join(a) for a in calls), f"slug not found in {calls}"


# ---------------------------------------------------------------------------
# Part 2: verify_evidence()
# ---------------------------------------------------------------------------

def test_verify_evidence_true_only_for_landed(tmp_path):
    repo, h = _git_repo(tmp_path)
    assert td.verify_evidence(f"commit {h[:10]}", str(repo)) is True
    assert td.verify_evidence("file f.txt", str(repo)) is True
    side = _side_commit(repo)
    assert td.verify_evidence(f"commit {side[:10]}", str(repo)) is False
    assert td.verify_evidence("file nope.txt", str(repo)) is False
    assert td.verify_evidence("commit deadbeef00", str(repo)) is False
    assert td.verify_evidence("PR #1", str(repo)) is False
    assert td.verify_evidence("vibes", str(repo)) is False
    assert td.verify_evidence("commit abc1234", None) is False
