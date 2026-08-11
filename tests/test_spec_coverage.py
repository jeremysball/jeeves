"""Content coverage answers the squash that ancestry and tree matching both miss.

`test_spec_squash.py` establishes that a squash merge leaves the original sha
reachable from nothing, so ancestry reports unmerged for work that shipped, and
concludes that only GitHub knows. That is one case short of the truth: the work
is still sitting in the repo, as content, and content is measurable offline.

The shape that defeats every offline check jeeves and orient had is the ordinary
one -- cut a branch, let the base gain an unrelated commit, then squash-merge.
The squash commit's tree is `new-base + branch changes`; the branch's own tree is
`old-base + branch changes`. Never equal. auditing-worktrees' coverage-score
scores residual content instead of comparing trees, so the drift does not reach
it, and jeeves now asks it before spending a network round trip -- or instead of
one, for a repo whose origin is not GitHub and which therefore had no answer at
all before.
"""
import subprocess
from pathlib import Path

import pytest

import todos as td

REAL_COVERAGE_BIN = Path.home() / ".claude" / "skills" / "auditing-worktrees" / "bin"

needs_real_cli = pytest.mark.skipif(
    not (REAL_COVERAGE_BIN / "coverage-score").is_file(),
    reason="auditing-worktrees is not installed; the stub-backed tests below still "
           "cover the integration, this one covers the real boundary",
)


def _git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode != 0:
        # git puts the actual reason on stderr, and check=True throws it away.
        # Losing it cost a CI round trip to diagnose a one-line fixture bug.
        raise AssertionError(f"git {' '.join(args)} exited {p.returncode}:\n{p.stderr}")
    return p.stdout.strip()


def _init(tmp_path, repo):
    """A repo with an identity of its own, not the machine's.

    Set locally rather than passed per-commit: `git merge --squash` needs a
    committer identity too when the merge is not a fast-forward, which is
    exactly the shape these fixtures build. Per-commit `-c` flags cover the
    commits and leave that one call to fall back to the machine's global git
    config -- fine here, fatal on a CI runner that has none.
    """
    repo.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")


def _commit(repo, fname, content, msg):
    (repo / fname).write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _squashed_onto_advanced_base(tmp_path, origin="https://github.com/o/r.git"):
    """A repo in the shape neither ancestry nor a tree match can see.

    Returns (repo, branch_sha). The branch's content is fully in main, arrived
    there via squash, and main moved in between -- so the branch tip's tree is
    not any tree in main's history either.
    """
    repo = tmp_path / "repo"
    _init(tmp_path, repo)
    _commit(repo, "a.txt", "a\n", "init")
    _git(repo, "remote", "add", "origin", origin)
    _git(repo, "checkout", "-q", "-b", "feature")
    branch_sha = _commit(repo, "feat.txt", "feature work\n", "add feat")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "b.txt", "b\n", "unrelated main commit")
    _git(repo, "merge", "-q", "--squash", "feature")
    _git(repo, "commit", "-qm", "squash: add feat (#1)")
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "main"))
    return repo, branch_sha


def _unmerged_branch(tmp_path, origin="https://github.com/o/r.git"):
    """A repo whose branch commit is genuinely not in main, by content or ancestry."""
    repo = tmp_path / "repo"
    _init(tmp_path, repo)
    main_sha = _commit(repo, "a.txt", "a\n", "init")
    _git(repo, "remote", "add", "origin", origin)
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "checkout", "-q", "-b", "feature")
    branch_sha = _commit(repo, "open.txt", "".join(f"line {i}\n" for i in range(20)),
                         "real unshipped work")
    _git(repo, "checkout", "-q", "main")
    return repo, branch_sha


MERGED = """[1]:
  -
    number: 1
    state: closed
    merged_at: "2026-08-09T15:30:46Z"
"""


def _stub_gh(monkeypatch, output=MERGED):
    """Answer gh-axi from a fixture, recording every call; real git runs untouched."""
    calls = []
    real_capture = td._capture

    def fake_capture(args):
        if args and args[0] == "gh-axi":
            calls.append(args)
            return output
        return real_capture(args)

    monkeypatch.setattr(td, "_capture", fake_capture)
    return calls


def _stub_coverage(tmp_path, verdict):
    """A coverage-score standing in for the real one, printing a fixed verdict.

    The verdict strings are the CLI's published contract, so the integration is
    testable without auditing-worktrees installed -- but a stub only ever proves
    what jeeves does with an answer, never that the real scorer produces it.
    That half is `needs_real_cli`'s job.
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    cli = bin_dir / "coverage-score"
    cli.write_text(f'#!/bin/sh\necho "{verdict}"\n')
    cli.chmod(0o755)
    return bin_dir


@needs_real_cli
def test_squash_onto_advanced_base_is_landed_without_asking_github(tmp_path, monkeypatch):
    repo, sha = _squashed_onto_advanced_base(tmp_path)
    # Both offline checks that existed before genuinely fail on this repo, so
    # the test is measuring the new pass and not a shape the old ones handled.
    assert subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                           sha, "origin/main"]).returncode != 0
    tip_tree = _git(repo, "rev-parse", "feature^{tree}")
    assert tip_tree not in _git(repo, "log", "main", "--format=%T").split()

    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(REAL_COVERAGE_BIN))
    calls = _stub_gh(monkeypatch)
    assert td.classify_evidence(f"commit {sha}", repo) == td.LANDED
    assert calls == [], "coverage answered offline, so nothing should reach the network"


@needs_real_cli
def test_non_github_origin_gets_an_answer_at_last(tmp_path, monkeypatch):
    """The case that had no answer before: no slug to ask, so it was UNKNOWN."""
    repo, sha = _squashed_onto_advanced_base(tmp_path, origin=str(tmp_path / "origin.git"))
    assert td._repo_slug(repo) == "", "a filesystem origin has no slug to ask"
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(REAL_COVERAGE_BIN))
    calls = _stub_gh(monkeypatch)
    assert td.classify_evidence(f"commit {sha}", repo) == td.LANDED
    assert calls == []


@needs_real_cli
def test_genuinely_unmerged_work_is_not_scored_as_landed(tmp_path, monkeypatch):
    """The pass must not manufacture LANDED for work that never shipped."""
    repo, sha = _unmerged_branch(tmp_path)
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(REAL_COVERAGE_BIN))
    calls = _stub_gh(monkeypatch, output="[]\n")
    assert td.classify_evidence(f"commit {sha}", repo) == td.OUTSTANDING
    assert calls, "coverage declined, so GitHub still has to be asked"


def test_missing_cli_leaves_the_old_behaviour_exactly(tmp_path, monkeypatch):
    """auditing-worktrees is not a dependency; absent it, nothing changes."""
    repo, sha = _squashed_onto_advanced_base(tmp_path)
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(tmp_path / "nowhere"))
    calls = _stub_gh(monkeypatch)
    assert td.classify_evidence(f"commit {sha}", repo) == td.LANDED
    assert calls, "with no scorer to ask, GitHub is the only source left"


@pytest.mark.parametrize("verdict", ["UNSCORED binary", "UNKNOWN merge-conflict",
                                     "UNKNOWN no-merge-base"])
def test_declined_verdicts_keep_asking_rather_than_deciding(tmp_path, monkeypatch, verdict):
    """UNSCORED and UNKNOWN mean "I will not judge", not "still outstanding".

    Reading either as a verdict would let a binary-only diff or a criss-cross
    history mark live work done, or park shipped work as outstanding, on the
    strength of the scorer declining to answer.
    """
    repo, sha = _squashed_onto_advanced_base(tmp_path)
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(_stub_coverage(tmp_path, verdict)))
    calls = _stub_gh(monkeypatch)
    assert td.classify_evidence(f"commit {sha}", repo) == td.LANDED
    assert calls, f"{verdict!r} is not an answer, so GitHub must still be asked"


def test_a_score_under_the_threshold_is_not_landed(tmp_path, monkeypatch):
    repo, sha = _squashed_onto_advanced_base(tmp_path)
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(_stub_coverage(tmp_path, "SCORED 94")))
    calls = _stub_gh(monkeypatch, output="[]\n")
    assert td.classify_evidence(f"commit {sha}", repo) == td.OUTSTANDING
    assert calls


def test_threshold_env_var_moves_the_bar(tmp_path, monkeypatch):
    repo, sha = _squashed_onto_advanced_base(tmp_path)
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(_stub_coverage(tmp_path, "SCORED 94")))
    monkeypatch.setenv("WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD", "90")
    calls = _stub_gh(monkeypatch, output="[]\n")
    assert td.classify_evidence(f"commit {sha}", repo) == td.LANDED
    assert calls == []


@pytest.mark.parametrize("raw,expected", [
    (None, 95), ("90", 90), ("1", 1), ("100", 100),
    # Rejected, each falling back to the default rather than to some other
    # number: 0 would call every commit landed, 08 is an octal-looking value
    # the shell consumers reject for the same reason, and the rest are noise.
    ("0", 95), ("08", 95), ("101", 95), ("", 95), ("abc", 95), ("9.5", 95),
    ("18446744073709551616", 95),
])
def test_threshold_parsing_matches_the_shell_consumers(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD", raising=False)
    else:
        monkeypatch.setenv("WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD", raw)
    assert td._coverage_threshold() == expected


def test_bin_location_is_read_at_call_time(monkeypatch):
    """Read per call, not captured at import, so a test or a worktree can move it."""
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", "/somewhere/else/bin")
    assert td._coverage_score_bin() == Path("/somewhere/else/bin/coverage-score")
    monkeypatch.delenv("AUDIT_WORKTREES_BIN")
    assert td._coverage_score_bin() == (
        Path.home() / ".claude/skills/auditing-worktrees/bin/coverage-score")
