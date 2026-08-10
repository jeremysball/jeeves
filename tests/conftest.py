import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))


@pytest.fixture(autouse=True)
def _isolate_jeeves_dirs(tmp_path_factory, monkeypatch):
    """Point every jeeves directory at a tmp path for the whole suite.

    `jl.log()` resolves its file through `state_dir()`, which falls back to
    the real `~/.local/state/jeeves` when `JEEVES_STATE_DIR` is unset. Any
    test that reaches a logging code path without setting that env var
    writes into the live cron log — `test_ferry_failure_paths` was appending
    two `model/x ... boom` lines to it on every single run, which is
    indistinguishable from a real dispatch failure when reading the log
    later.

    Autouse and suite-wide rather than a fix to that one test: the leak is a
    property of a test not setting the env var, so any test added later has
    the same hole. Tests that want their own directories still call
    `monkeypatch.setenv` themselves and win, since theirs runs after this.
    """
    base = tmp_path_factory.mktemp("jeeves-isolated")
    monkeypatch.setenv("JEEVES_STATE_DIR", str(base / "state"))
    monkeypatch.setenv("JEEVES_DATA_DIR", str(base / "data"))
    monkeypatch.setenv("JEEVES_PROJECTS_ROOT", str(base / "projects"))
