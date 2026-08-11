import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))


@pytest.fixture(autouse=True)
def _isolate_jeeves_state(tmp_path_factory, monkeypatch):
    """Point every jeeves directory at a tmp path and clear todos.py's memo
    caches, for the whole suite.

    `jl.log()` resolves its file through `state_dir()`, which falls back to
    the real `~/.local/state/jeeves` when `JEEVES_STATE_DIR` is unset. Any
    test that reaches a logging code path without setting that env var
    writes into the live cron log — `test_ferry_failure_paths` was appending
    two `model/x ... boom` lines to it on every single run, which is
    indistinguishable from a real dispatch failure when reading the log
    later.

    Separately, todos.py's disk-backed evidence memo persists to state by
    default, and `_MEMO` — a lazy-once-per-process singleton — stays
    populated for the rest of the pytest session once any test touches it.
    A later test using the same evidence string then gets served the first
    test's cached verdict instead of hitting its own fakes. Reproduced live:
    a real 32KB leak into this machine's actual
    ~/.local/state/jeeves/evidence_memo.json from a run before this fixture
    existed.

    Autouse and suite-wide rather than a per-test patch: the leak is a
    property of a test not isolating state, so any test added later has the
    same hole. Tests that want their own directories still call
    `monkeypatch.setenv` themselves and win, since theirs runs after this.
    """
    base = tmp_path_factory.mktemp("jeeves-isolated")
    monkeypatch.setenv("JEEVES_STATE_DIR", str(base / "state"))
    monkeypatch.setenv("JEEVES_DATA_DIR", str(base / "data"))
    monkeypatch.setenv("JEEVES_PROJECTS_ROOT", str(base / "projects"))
    # todos.py's coverage check shells out to auditing-worktrees' coverage-score
    # if it happens to be installed on the box. Left alone, every commit-evidence
    # test would take a different code path on a developer machine than on CI,
    # which is the kind of divergence that only ever surfaces as a mystery CI
    # failure. Point it at nothing by default; the tests that actually mean to
    # exercise the pass set it themselves and win, same as the directories above.
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(base / "no-such-bin"))
    monkeypatch.delenv("WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD", raising=False)
    import todos as td
    for cache_name in (
        "_MEMO", "_PR_CACHE", "_ISSUE_CACHE", "_PULLS_CACHE", "_DEFAULT_BRANCH_CACHE",
        "_COVERAGE_CACHE",
    ):
        monkeypatch.setattr(td, cache_name, None if cache_name == "_MEMO" else {}, raising=False)
