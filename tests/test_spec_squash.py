"""A squash-merged branch commit is landed work, and ancestry cannot see it.

`git merge-base --is-ancestor` answers "is this commit reachable from the base
branch". A squash merge rewrites the branch into one new commit, so the original
sha is reachable from nothing and the answer is no -- for work that shipped.
This repo squash-merges its own PRs, so this is the common case, not a corner.

Verified against real history before these tests were written: 9581947 (the head
of jeeves PR #1) is not an ancestor of origin/main, and `git cherry` also fails
to match it, because four commits were squashed into one and no single patch-id
survives. The only source that knows is GitHub, via the pull requests carrying
the commit.
"""
import subprocess

import todos as td


def _commit(repo, fname, content, msg):
    (repo / fname).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", msg], check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _repo_with_unmerged_branch(tmp_path):
    """A repo whose branch commit is not reachable from main, as after a squash."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    main_sha = _commit(repo, "f.txt", "x", "init")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/o/r.git"], check=True)
    # Simulate a real fetch without a reachable remote: origin/main matches
    # local main, giving `_default_branch` a real base to measure against.
    subprocess.run(["git", "-C", str(repo), "update-ref",
                    "refs/remotes/origin/main", main_sha], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True)
    branch_sha = _commit(repo, "g.txt", "y", "branch work")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    return repo, branch_sha


def _stub_gh(monkeypatch, output, calls=None, exit_nonzero=False):
    """Answer gh-axi from a fixture; let every real git call through untouched."""
    # The memo is process-global and two of these repos can produce the same sha
    # when built in the same second, so start every test from an empty one.
    monkeypatch.setattr(td, "_PULLS_CACHE", {})
    real_capture = td._capture

    def fake_capture(args):
        if args and args[0] == "gh-axi":
            if calls is not None:
                calls.append(args)
            return "" if exit_nonzero else output
        return real_capture(args)

    monkeypatch.setattr(td, "_capture", fake_capture)


# gh-axi api output is TOON. A merged PR carries a quoted timestamp; an open one
# carries a bare null. Captured verbatim from a live call against jeeves PR #1.
MERGED = """[1]:
  -
    number: 1
    state: closed
    merged_at: "2026-08-09T15:30:46Z"
    merge_commit_sha: 77cf93f53e70ecfc5d4060bd19396f64d43e4c15
"""

OPEN = """[1]:
  -
    number: 2
    state: open
    merged_at: null
"""

NO_PULLS = "[]\n"


def test_squash_merged_commit_is_landed(tmp_path, monkeypatch):
    repo, sha = _repo_with_unmerged_branch(tmp_path)
    _stub_gh(monkeypatch, MERGED)
    assert td.classify_evidence(f"commit {sha}", repo) == td.LANDED


def test_commit_in_an_open_pr_is_outstanding(tmp_path, monkeypatch):
    repo, sha = _repo_with_unmerged_branch(tmp_path)
    _stub_gh(monkeypatch, OPEN)
    assert td.classify_evidence(f"commit {sha}", repo) == td.OUTSTANDING


def test_commit_no_pull_request_carries_is_outstanding(tmp_path, monkeypatch):
    repo, sha = _repo_with_unmerged_branch(tmp_path)
    _stub_gh(monkeypatch, NO_PULLS)
    assert td.classify_evidence(f"commit {sha}", repo) == td.OUTSTANDING


def test_origin_without_resolved_base_does_not_trust_local_main(tmp_path, monkeypatch):
    """An unfetched origin makes a local-only commit's merge status unknown."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(repo, "f.txt", "x", "init")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/o/r.git"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True)
    sha = _commit(repo, "g.txt", "y", "local-only work")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    calls = []
    _stub_gh(monkeypatch, MERGED, calls=calls)
    assert td.classify_evidence(f"commit {sha}", repo) == td.UNKNOWN
    assert calls == []


def test_unreachable_github_is_unknown_never_outstanding(tmp_path, monkeypatch):
    """Could-not-ask and confirmed-not-merged are different answers.

    Reporting OUTSTANDING here would put "still live work" on a card on the
    strength of a failed network call, which is the plausible-but-wrong value
    the fail-fast rule exists to prevent.
    """
    repo, sha = _repo_with_unmerged_branch(tmp_path)
    _stub_gh(monkeypatch, "", exit_nonzero=True)
    assert td.classify_evidence(f"commit {sha}", repo) == td.UNKNOWN


def test_ancestor_commit_never_asks_github(tmp_path, monkeypatch):
    """The offline path stays offline: a commit already on the base branch is
    landed without a network call, so the common case costs nothing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    sha = _commit(repo, "f.txt", "x", "init")
    calls = []
    _stub_gh(monkeypatch, MERGED, calls=calls)
    assert td.classify_evidence(f"commit {sha}", repo) == td.LANDED
    assert calls == []


def test_repeated_classification_asks_github_once(tmp_path, monkeypatch):
    """prune_pending and --pending both classify every row; without memoing,
    listing a queue of N rows spends N network round trips to learn one fact."""
    repo, sha = _repo_with_unmerged_branch(tmp_path)
    calls = []
    _stub_gh(monkeypatch, MERGED, calls=calls)
    td.classify_evidence(f"commit {sha}", repo)
    td.classify_evidence(f"commit {sha}", repo)
    assert len(calls) == 1, f"expected one gh-axi call, got {len(calls)}"


def test_non_github_origin_asks_nobody_and_is_unknown(tmp_path, monkeypatch):
    """A filesystem origin is not a GitHub repo, so there is nothing to ask.

    The slug regex matched the tail of any URL-shaped string, so a local origin
    like `/tmp/x/origin.git` yielded the slug `x/origin` -- a plausible-looking
    value naming a repo that does not exist. That was invisible while the slug
    was only used for PR evidence; now it decides commit evidence too.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _commit(repo, "f.txt", "x", "init")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    str(tmp_path / "origin.git")], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True)
    sha = _commit(repo, "g.txt", "y", "branch work")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    calls = []
    _stub_gh(monkeypatch, MERGED, calls=calls)
    assert td._repo_slug(repo) == ""
    assert td.classify_evidence(f"commit {sha}", repo) == td.UNKNOWN
    assert calls == []


def test_pull_lookup_targets_the_commit_and_repo_slug(tmp_path, monkeypatch):
    repo, sha = _repo_with_unmerged_branch(tmp_path)
    calls = []
    _stub_gh(monkeypatch, MERGED, calls=calls)
    td.classify_evidence(f"commit {sha}", repo)
    assert calls, "no gh-axi call was made"
    assert calls[0] == ["gh-axi", "api", f"/repos/o/r/commits/{sha}/pulls"]


def test_failed_pull_lookup_is_not_cached(monkeypatch):
    """A dropped network call must not poison later lookups for the same
    (slug, sha): None means "could not ask", not "no merged PR carries it",
    so it is never memoized and the next call retries."""
    calls = []

    def fake_capture(args):
        calls.append(args)
        return "" if len(calls) == 1 else MERGED

    monkeypatch.setattr(td, "_capture", fake_capture)
    monkeypatch.setattr(td, "_PULLS_CACHE", {})
    sha = "a" * 40
    assert td._merged_pr_carries(sha, "o/r") is None
    assert td._merged_pr_carries(sha, "o/r") is True
    assert len(calls) == 2
