import json
from pathlib import Path

import pytest

import jeeves_lib as jl
import todos as td


def _ledger(tmp_path, monkeypatch, body=""):
    monkeypatch.setenv("JEEVES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path / "state"))
    p = tmp_path / "todo.md"
    p.write_text(body or "# jeeves todo ledger\n\n## open\n\n## done\n\n## dismissed\n")
    return p


def test_init_creates_skeleton(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_DATA_DIR", str(tmp_path))
    p = td.ledger_path()
    assert p.exists()
    assert "## open" in p.read_text()


def test_add_appends_with_provenance_tag(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch)
    td.apply_add("fix pinentry tty handling", kind="loose-end", source="hearth")
    open_lines = td.parse_ledger(p.read_text())["open"]
    assert len(open_lines) == 1
    assert open_lines[0].startswith("- [ ] fix pinentry tty handling (jeeves: loose-end, hearth,")


def test_check_moves_to_done_with_evidence(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n- [ ] drop cursor tracking (jeeves: loose-end, pypitui, 2026-07-29)\n\n## done\n\n## dismissed\n")
    td.apply_check("drop cursor tracking", evidence="commit a1b2c34")
    s = td.parse_ledger(p.read_text())
    assert s["open"] == []
    assert "- [x] drop cursor tracking" in s["done"][0]
    assert "commit a1b2c34" in s["done"][0]


def test_check_ambiguous_match_raises(tmp_path, monkeypatch):
    # two lines that normalize to the same string — genuinely ambiguous
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n- [ ] fix the thing\n- [ ] fix   the  thing\n\n## done\n\n## dismissed\n")
    with pytest.raises(td.AmbiguousMatch):
        td.apply_check("fix the thing", evidence="commit abc1234")


def test_dismiss_moves_and_tags_never_deletes(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n- [ ] stale idea\n\n## done\n\n## dismissed\n")
    td.apply_dismiss("stale idea")
    s = td.parse_ledger(p.read_text())
    assert s["open"] == []
    assert "(dismissed" in s["dismissed"][0]
    assert "stale idea" in s["dismissed"][0]


def test_never_deletes_manual_lines(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n- [ ] a hand-written line\n\n## done\n\n## dismissed\n")
    td.apply_add("new item", kind="loose-end", source="x")
    assert "a hand-written line" in p.read_text()


def _git_repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    h = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                       capture_output=True, text=True).stdout.strip()
    return repo, h


def test_verify_commit_real(tmp_path):
    repo, h = _git_repo(tmp_path)
    assert td.verify_evidence(f"commit {h[:10]}", str(repo)) is True
    assert td.verify_evidence("commit deadbeef00", str(repo)) is False


def test_verify_file(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.verify_evidence("file f.txt", str(repo)) is True
    assert td.verify_evidence("file nope.txt", str(repo)) is False


def test_verify_requires_repo(tmp_path):
    assert td.verify_evidence("commit abc1234", None) is False


def test_verify_unknown_shape_false():
    assert td.verify_evidence("vibes", "/tmp") is False


def test_apply_mutations_add_deduped(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    muts = [{"op": "add", "line": "rework X", "kind": "loose-end", "source": "hearth"},
            {"op": "add", "line": "rework X", "kind": "loose-end", "source": "hearth"}]
    res = td.apply_mutations(muts)
    assert res["applied"] == 1 and res["deduped"] == 1
    s = td.parse_ledger(td.ledger_path().read_text())
    assert len(s["open"]) == 1


def test_apply_mutations_add_skips_dismissed(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    td.apply_add("killed idea", kind="tangent", source="x")
    td.apply_dismiss("killed idea")
    # register the dismissal in the seen store the way apply_mutations would
    seen = jl.SeenStore.load()
    seen.upsert(jl.line_hash("killed idea"), "killed idea", status="dismissed")
    seen.save()
    res = td.apply_mutations([{"op": "add", "line": "killed idea", "kind": "tangent", "source": "y"}])
    assert res["applied"] == 0 and res["deduped"] == 1


def test_check_demotes_to_pending_on_bad_evidence(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] fix the parser\n\n## done\n\n## dismissed\n")
    res = td.apply_mutations([{"op": "check", "line": "fix the parser",
                               "evidence": "commit deadbeef00", "repo": "/nonexistent"}])
    assert res["pending"] == 1
    assert len(td.load_pending()) == 1
    s = td.parse_ledger(td.ledger_path().read_text())
    assert len(s["open"]) == 1  # untouched


def test_check_applies_on_good_evidence(tmp_path, monkeypatch):
    repo, h = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] init the repo\n\n## done\n\n## dismissed\n")
    res = td.apply_mutations([{"op": "check", "line": "init the repo",
                               "evidence": f"commit {h[:10]}", "repo": str(repo)}])
    assert res["applied"] == 1
    assert td.parse_ledger(td.ledger_path().read_text())["open"] == []


def test_duplicate_of_bumps_recurrence(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    td.apply_mutations([{"op": "add", "line": "nagging thing", "kind": "loose-end", "source": "a"}])
    td.apply_mutations([{"op": "duplicate_of", "line": "nagging THING", "existing": "nagging thing"}])
    seen = jl.SeenStore.load()
    rec = seen.check(jl.line_hash("nagging thing"))
    assert rec["count"] >= 2


def test_reconcile_registers_and_records_hand_deleted(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch)
    td.apply_add("gone tomorrow", kind="loose-end", source="x")
    td.reconcile()  # registers ledger lines into the seen store
    seen = jl.SeenStore.load()
    h = jl.line_hash("gone tomorrow")
    assert seen.check(h)["status"] == "open"
    # user hand-deletes the line
    tag = f"(jeeves: loose-end, x, {jl.today_et()})"
    p.write_text(p.read_text().replace(f"- [ ] gone tomorrow {tag}\n", ""))
    td.reconcile()
    seen = jl.SeenStore.load()
    assert seen.check(h)["status"] == "dismissed"


def test_wake_updates_last_wake(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    td.wake()
    assert (jl.state_dir() / "last_wake").read_text().strip() != ""


def test_cli_add(tmp_path):
    import subprocess, sys
    todos_py = str(Path(__file__).parent.parent / "bin" / "todos.py")
    env = dict(__import__("os").environ,
               JEEVES_DATA_DIR=str(tmp_path), JEEVES_STATE_DIR=str(tmp_path / "state"))
    r = subprocess.run([sys.executable, todos_py, "--add", "manual item",
                        "--kind", "manual", "--source", "user"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert "manual item" in (tmp_path / "todo.md").read_text()


def test_ingest_imports_open_items_only(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    repo = tmp_path / "proj"
    repo.mkdir()
    f = repo / "TODO.md"
    f.write_text(
        "# Todos\n"
        "- [ ] write the docs\n"
        "- [x] already done\n"
        "[x] done without dash\n"
        "[ ] open without dash\n"
        "plain dash item\n"
        "--------------------------\n"
        "(continuation of something above)\n"
        "Bugs:\n"
        "Feat\n"
        "ok\n"
    )
    res = td.ingest_repo_todos([str(repo)])
    # imported: write the docs, and "open without dash" with the bare line
    # after it joined as a wrapped continuation — nothing else
    assert res["ingested"] == 2
    body = (tmp_path / "todo.md").read_text()
    assert "write the docs (jeeves: import, proj," in body
    assert "open without dash plain dash item (jeeves: import, proj," in body
    for junk in ("already done", "done without dash", "----", "(continuation", "Bugs", "Feat"):
        assert junk not in body
    assert f.read_text().startswith("# Todos")  # input untouched


def test_ingest_content_hash_gates_reingest(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    repo = tmp_path / "proj"
    repo.mkdir()
    f = repo / "TODO.txt"
    f.write_text("- [ ] one thing\n")
    assert td.ingest_repo_todos([str(repo)])["ingested"] == 1
    assert td.ingest_repo_todos([str(repo)])["ingested"] == 0  # unchanged file
    f.write_text("- [ ] one thing\n- [ ] a new thing\n")
    res = td.ingest_repo_todos([str(repo)])
    assert res["ingested"] == 1  # only the new item
    assert (tmp_path / "todo.md").read_text().count("one thing") == 1


def test_verify_commit_accepts_trailing_prose(tmp_path):
    repo, h = _git_repo(tmp_path)
    # the synthesis ferry writes "commit 3c33869 reset", never a bare ref
    assert td.verify_evidence(f"commit {h[:10]} reset", str(repo)) is True


def test_verify_pr_accepts_trailing_prose_and_runs_in_repo(tmp_path, monkeypatch):
    seen = {}

    def fake_runs(args, cwd=None):
        seen["args"], seen["cwd"] = args, cwd
        return 0

    monkeypatch.setattr(td, "_runs", fake_runs)
    assert td.verify_evidence("PR #419 merged", "/repo/path") is True
    assert seen["args"] == ["gh-axi", "pr", "view", "419"]
    assert seen["cwd"] == "/repo/path"  # -R takes OWNER/REPO, not a path


def test_verify_issue_kind_supported(tmp_path, monkeypatch):
    seen = {}

    def fake_runs(args, cwd=None):
        seen["args"] = args
        return 0

    monkeypatch.setattr(td, "_runs", fake_runs)
    assert td.verify_evidence("issue #391 filed", "/repo/path") is True
    assert seen["args"] == ["gh-axi", "issue", "view", "391"]


def test_verify_pr_missing_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "_runs", lambda args, cwd=None: 1)
    assert td.verify_evidence("PR #99999", "/repo/path") is False
