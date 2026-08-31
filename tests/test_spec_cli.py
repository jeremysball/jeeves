import json
import os
import subprocess
import sys
from pathlib import Path

import todos as td

TODOS_PY = Path(__file__).parent.parent / "bin" / "todos.py"

LEDGER_HEADER = "# jeeves todo ledger\n\n## open\n\n## done\n\n## dismissed\n"


def _env(tmp_path):
    return dict(os.environ,
                JEEVES_DATA_DIR=str(tmp_path),
                JEEVES_STATE_DIR=str(tmp_path / "state"))


def _ledger(tmp_path, monkeypatch, body=None):
    monkeypatch.setenv("JEEVES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path / "state"))
    p = tmp_path / "todo.md"
    p.write_text(body or LEDGER_HEADER)
    return p


def _sections(body):
    sections = {}
    current = None
    for ln in body.splitlines():
        if ln.startswith("## "):
            current = ln[3:].strip()
            sections[current] = []
        elif current is not None and ln.strip():
            sections[current].append(ln)
    return sections


def _row(line, evidence, repo, reason="verify failed"):
    return {"op": "check", "line": line, "evidence": evidence,
            "repo": repo, "reason": reason, "queued": "2026-08-09T00:00:00Z"}


def _git_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    h = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                       capture_output=True, text=True).stdout.strip()
    return repo, h


def _commit_on_branch(repo, branch, filename, content):
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)
    (repo / filename).write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "side"], check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _empty_git_repo(tmp_path, name="empty"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_prune_pending_flag_drains_queue_and_prints_counts_json(tmp_path, monkeypatch):
    repo, h = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] landed thing\n- [ ] kept thing\n\n"
            "## done\n- [x] moot thing\n\n## dismissed\n")
    td.save_pending([
        _row("landed thing", f"commit {h[:10]}", str(repo)),
        _row("kept thing", "commit deadbeef00", str(repo)),
        _row("moot thing", "commit 0000000", str(repo)),
        _row("stale thing", "commit 0000000", str(repo)),
    ])
    r = subprocess.run([sys.executable, str(TODOS_PY), "--prune-pending", "--format", "json"],
                       capture_output=True, text=True, env=_env(tmp_path))
    assert r.returncode == 0
    counts = json.loads(r.stdout.strip())
    assert set(counts) == {"applied", "moot", "stale", "kept"}
    assert counts == {"applied": 1, "moot": 1, "stale": 1, "kept": 1}
    after = td.load_pending()
    assert [row["line"] for row in after] == ["kept thing"]
    secs = _sections((tmp_path / "todo.md").read_text())
    assert "landed thing" not in "".join(secs["open"])
    assert "kept thing" in "".join(secs["open"])
    assert any(ln.startswith("- [x] landed thing") for ln in secs["done"])


def test_prune_pending_empty_queue_prints_zero_counts(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    td.save_pending([])
    r = subprocess.run([sys.executable, str(TODOS_PY), "--prune-pending", "--format", "json"],
                       capture_output=True, text=True, env=_env(tmp_path))
    assert r.returncode == 0
    counts = json.loads(r.stdout.strip())
    assert set(counts) == {"applied", "moot", "stale", "kept"}
    assert all(v == 0 for v in counts.values())


def test_pending_flag_prints_each_row_with_state_key(tmp_path, monkeypatch):
    repo, h = _git_repo(tmp_path)
    side_h = _commit_on_branch(repo, "feature", "g.txt", "y")
    _ledger(tmp_path, monkeypatch)
    td.save_pending([
        _row("landed thing", f"commit {h[:10]}", str(repo)),
        _row("side thing", f"commit {side_h[:10]}", str(repo)),
        _row("unknown thing", "commit deadbeef00", str(repo)),
        _row("no repo thing", "commit abc1234", None),
    ])
    r = subprocess.run([sys.executable, str(TODOS_PY), "--pending", "--format", "json"],
                       capture_output=True, text=True, env=_env(tmp_path))
    assert r.returncode == 0
    out = json.loads(r.stdout.strip())
    assert isinstance(out, list)
    assert len(out) == 4
    by_line = {row["line"]: row for row in out}
    assert by_line["landed thing"]["state"] == td.LANDED
    assert by_line["side thing"]["state"] == td.OUTSTANDING
    assert by_line["unknown thing"]["state"] == td.UNKNOWN
    assert by_line["no repo thing"]["state"] == td.UNKNOWN
    for row in out:
        assert "state" in row
        assert row["state"] in {td.LANDED, td.OUTSTANDING, td.UNKNOWN}


def test_pending_flag_does_not_drain_the_queue(tmp_path, monkeypatch):
    repo, h = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch)
    td.save_pending([
        _row("landed thing", f"commit {h[:10]}", str(repo)),
        _row("kept thing", "commit deadbeef00", str(repo)),
    ])
    r = subprocess.run([sys.executable, str(TODOS_PY), "--pending", "--format", "json"],
                       capture_output=True, text=True, env=_env(tmp_path))
    assert r.returncode == 0
    after = td.load_pending()
    assert [row["line"] for row in after] == ["landed thing", "kept thing"]


def test_pending_fields_accepts_seen(tmp_path, monkeypatch):
    # A folded row's re-queue count has to be reachable without the raw json
    # dump, or the only evidence of recurrence is invisible to a wake card
    # that reads --pending --fields.
    repo, _ = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch)
    td.save_pending([
        _row("folded row", "commit deadbeef00", str(repo)),
    ])
    r = subprocess.run([sys.executable, str(TODOS_PY), "--pending",
                        "--fields", "line,seen"],
                       capture_output=True, text=True, env=_env(tmp_path))
    assert r.returncode == 0
    assert "{line,seen}:" in r.stdout


def test_prune_pending_and_pending_are_distinct_flags(tmp_path, monkeypatch):
    # prune prints a counts object and empties the queue; pending prints the
    # queue rows. A flag wired to the wrong action fails one of the two runs.
    repo, _ = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch)
    env = _env(tmp_path)
    td.save_pending([_row("kept thing", "commit deadbeef00", str(repo))])
    r1 = subprocess.run([sys.executable, str(TODOS_PY), "--prune-pending", "--format", "json"],
                        capture_output=True, text=True, env=env)
    assert r1.returncode == 0
    counts = json.loads(r1.stdout.strip())
    assert isinstance(counts, dict)
    assert set(counts) == {"applied", "moot", "stale", "kept"}
    td.save_pending([_row("kept thing", "commit deadbeef00", str(repo))])
    r2 = subprocess.run([sys.executable, str(TODOS_PY), "--pending", "--format", "json"],
                        capture_output=True, text=True, env=env)
    assert r2.returncode == 0
    rows = json.loads(r2.stdout.strip())
    assert isinstance(rows, list)
    assert rows
    assert "state" in rows[0]


def test_classify_evidence_nonexistent_repo_returns_unknown(tmp_path):
    verdict = td.classify_evidence("commit abc1234", str(tmp_path / "no-such-repo"))
    assert verdict == td.UNKNOWN


def test_classify_evidence_plain_dir_is_not_a_repo_returns_unknown(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    verdict = td.classify_evidence("commit abc1234", str(plain))
    assert verdict == td.UNKNOWN


def test_classify_evidence_empty_string_returns_unknown(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("", str(repo)) == td.UNKNOWN


def test_classify_evidence_punctuation_only_returns_unknown(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("!!! --- ... (((", str(repo)) == td.UNKNOWN


def test_classify_evidence_repo_none_returns_unknown(tmp_path):
    assert td.classify_evidence("commit abc1234", None) == td.UNKNOWN


def test_classify_evidence_empty_git_repo_returns_unknown(tmp_path):
    # Every git subprocess against a repo with no commits exits non-zero; a
    # failed lookup must surface as UNKNOWN, never raise and never fall back
    # to LANDED.
    repo = _empty_git_repo(tmp_path)
    verdict = td.classify_evidence("commit abc1234", str(repo))
    assert verdict == td.UNKNOWN
