import json


import jeeves_lib as jl
import todos as td


def _ledger(tmp_path, monkeypatch, body=""):
    monkeypatch.setenv("JEEVES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path / "state"))
    p = tmp_path / "todo.md"
    p.write_text(body or "# jeeves todo ledger\n\n## open\n\n## done\n\n## dismissed\n")
    return p


def _git_repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "landed"], check=True)
    landed = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "side"], check=True)
    (repo / "g.txt").write_text("y")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "unlanded"], check=True)
    unlanded = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    return repo, landed, unlanded


def _row(line, evidence, repo, reason="evidence not landed"):
    return {"op": "check", "line": line, "evidence": evidence,
            "repo": str(repo) if repo else None, "reason": reason, "queued": "2026-08-01"}


def test_prune_applies_row_whose_evidence_landed(tmp_path, monkeypatch):
    repo, landed, _ = _git_repo(tmp_path)
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n- [ ] init the repo (jeeves: loose-end, x, 2026-07-30)\n\n## done\n\n## dismissed\n")
    td.save_pending([_row("- [ ] init the repo (jeeves: loose-end, x, 2026-07-30)",
                          f"commit {landed[:10]}", repo)])
    counts = td.prune_pending()
    assert counts["applied"] == 1
    s = td.parse_ledger(p.read_text())
    assert s["open"] == []
    assert len(s["done"]) == 1
    assert s["done"][0].startswith("- [x]")
    assert "init the repo" in s["done"][0]
    assert td.load_pending() == []


def test_prune_moots_row_when_line_now_in_done(tmp_path, monkeypatch):
    repo, landed, _ = _git_repo(tmp_path)
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n\n## done\n- [x] fix the parser (jeeves: loose-end, x, 2026-07-30)\n\n## dismissed\n")
    before = p.read_text()
    td.save_pending([_row("- [ ] fix the parser (jeeves: loose-end, x, 2026-07-30)",
                          f"commit {landed[:10]}", repo)])
    counts = td.prune_pending()
    assert counts["moot"] == 1
    assert p.read_text() == before
    assert td.load_pending() == []


def test_prune_moots_row_when_line_now_in_dismissed(tmp_path, monkeypatch):
    repo, landed, _ = _git_repo(tmp_path)
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n\n## done\n\n## dismissed\n- [ ] stale idea (jeeves: loose-end, x, 2026-07-30) (dismissed 2026-08-09)\n")
    before = p.read_text()
    td.save_pending([_row("- [ ] stale idea (jeeves: loose-end, x, 2026-07-30)",
                          f"commit {landed[:10]}", repo)])
    counts = td.prune_pending()
    assert counts["moot"] == 1
    assert p.read_text() == before
    assert td.load_pending() == []


def test_prune_stales_row_whose_line_is_nowhere_in_ledger(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch)
    before = p.read_text()
    td.save_pending([_row("- [ ] vanished item (jeeves: loose-end, x, 2026-07-30)",
                          "commit abc1234", None)])
    counts = td.prune_pending()
    assert counts["stale"] == 1
    assert p.read_text() == before
    assert td.load_pending() == []


def test_prune_stales_row_with_no_line_text(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch)
    before = p.read_text()
    td.save_pending([_row("", "commit abc1234", None)])
    counts = td.prune_pending()
    assert counts["stale"] == 1
    assert p.read_text() == before
    assert td.load_pending() == []


def test_prune_empty_queue_returns_zero_counts_and_touches_nothing(tmp_path, monkeypatch):
    p = _ledger(tmp_path, monkeypatch)
    before = p.read_text()
    counts = td.prune_pending()
    assert counts == {"applied": 0, "moot": 0, "stale": 0, "kept": 0}
    assert p.read_text() == before


def test_prune_ambiguous_duplicate_lines_does_not_raise_or_check_off(tmp_path, monkeypatch):
    # Two identical open lines make the exact-match lookup ambiguous; the prune
    # must not crash, must not silently check the row off, and must still drain
    # the rest of the queue.
    repo, landed, _ = _git_repo(tmp_path)
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n- [ ] fix the thing\n- [ ] fix the thing\n\n## done\n- [x] already done item\n\n## dismissed\n")
    ambiguous = _row("- [ ] fix the thing", f"commit {landed[:10]}", repo)
    moot = _row("- [ ] already done item", "commit abc1234", None)
    td.save_pending([ambiguous, moot])
    counts = td.prune_pending()
    assert counts["moot"] == 1
    assert counts["kept"] == 1
    s = td.parse_ledger(p.read_text())
    assert len(s["open"]) == 2
    assert all(line.startswith("- [ ]") for line in s["open"])
    assert td.load_pending() == [ambiguous]


def test_prune_ambiguous_done_lines_are_kept_not_dropped_as_moot(tmp_path, monkeypatch):
    # The ledger line is no longer open, but it's duplicated in `done`, so a
    # human still needs to look -- the row must not be silently counted moot
    # and dropped from the queue just because a match exists somewhere.
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n\n## done\n- [x] dupe\n- [x] dupe\n\n## dismissed\n")
    row = _row("- [x] dupe", "", None)
    td.save_pending([row])
    counts = td.prune_pending()
    assert counts == {"applied": 0, "moot": 0, "stale": 0, "kept": 1}
    assert td.load_pending() == [row]


def test_prune_queue_file_holds_exactly_the_kept_rows(tmp_path, monkeypatch):
    repo, _, unlanded = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] still open item (jeeves: loose-end, x, 2026-07-30)\n\n## done\n- [x] done item\n\n## dismissed\n")
    kept = _row("- [ ] still open item (jeeves: loose-end, x, 2026-07-30)",
                f"commit {unlanded[:10]}", repo)
    moot = _row("- [ ] done item", "commit abc1234", None)
    td.save_pending([kept, moot])
    td.prune_pending()
    assert td.load_pending() == [kept]
    raw = json.loads((jl.state_dir() / "pending.json").read_text())
    assert raw == [kept]


def test_prune_counts_sum_to_rows_held_before_call(tmp_path, monkeypatch):
    # The four counts must be internally consistent: they add up to the number
    # of rows the queue held before the call.
    repo, _, unlanded = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] still open item (jeeves: loose-end, x, 2026-07-30)\n\n## done\n- [x] done item\n\n## dismissed\n")
    rows = [
        _row("- [ ] still open item (jeeves: loose-end, x, 2026-07-30)",
             f"commit {unlanded[:10]}", repo),
        _row("- [ ] done item", "commit abc1234", None),
        _row("- [ ] gone item", "commit abc1234", None),
    ]
    td.save_pending(rows)
    counts = td.prune_pending()
    assert sum(counts.values()) == len(rows)


def test_prune_mixed_queue_drains_each_outcome_in_one_call(tmp_path, monkeypatch):
    repo, landed, unlanded = _git_repo(tmp_path)
    p = _ledger(tmp_path, monkeypatch,
                "# jeeves todo ledger\n\n## open\n- [ ] init the repo (jeeves: loose-end, x, 2026-07-30)\n- [ ] still open item (jeeves: loose-end, x, 2026-07-30)\n\n## done\n- [x] done item\n\n## dismissed\n- [ ] dismissed item (jeeves: loose-end, x, 2026-07-30) (dismissed 2026-08-09)\n")
    rows = [
        _row("- [ ] init the repo (jeeves: loose-end, x, 2026-07-30)",
             f"commit {landed[:10]}", repo),
        _row("- [ ] done item", "commit abc1234", None),
        _row("- [ ] dismissed item (jeeves: loose-end, x, 2026-07-30)",
             "commit abc1234", None),
        _row("- [ ] gone item", "commit abc1234", None),
        _row("- [ ] still open item (jeeves: loose-end, x, 2026-07-30)",
             f"commit {unlanded[:10]}", repo),
    ]
    td.save_pending(rows)
    counts = td.prune_pending()
    assert counts == {"applied": 1, "moot": 2, "stale": 1, "kept": 1}
    s = td.parse_ledger(p.read_text())
    assert len(s["open"]) == 1
    # The ledger already held a done line, and the spec says nothing about where
    # a newly-applied one lands, so match on content rather than on index 0.
    applied = [ln for ln in s["done"] if "init the repo" in ln]
    assert len(applied) == 1
    assert applied[0].startswith("- [x]")
    assert td.load_pending() == [rows[4]]
