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


# --- re-queue folding (the eight-copies failure mode) ---------------------
# The same check can be re-proposed by synthesis every hour its evidence
# still looks stale. Pushed through _push_pending unconditionally -- as
# before this fix -- one queue row accreted eight copies spanning
# 2026-08-14 to 08-19, each paying its own re-verify at prune and each
# stealing a row of the 30-row --pending window from other subjects.

def _frozen_now(monkeypatch, when):
    monkeypatch.setattr(jl, "now_et", lambda: when)


def test_push_folds_requeue_of_same_subject_across_bullet_variance(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    _ledger(tmp_path, monkeypatch)
    t0 = datetime(2026, 8, 31, 9, 0, 0, tzinfo=timezone.utc)
    _frozen_now(monkeypatch, t0)
    first = {"op": "check", "line": "taskferry fix-issue ferries unverified",
             "evidence": "scan 2026-08-17: branches in main ancestry", "repo": "/taskferry"}
    assert td._push_pending(dict(first), "evidence did not verify") == "new"
    _frozen_now(monkeypatch, t0 + timedelta(days=5))
    later = {"op": "check", "line": "- [ ] TASKFERRY fix-issue ferries unverified",
             "evidence": "fix/issue-369/420/439/458/460 merged into main ancestry",
             "repo": "/taskferry"}
    assert td._push_pending(dict(later), "no unique ledger match") == "merged"
    items = td.load_pending()
    assert len(items) == 1
    row = items[0]
    assert row["seen"] == 2
    assert row["evidence"] == later["evidence"]         # freshest attempt wins
    assert row["reason"] == "no unique ledger match"
    assert row["queued"] == "2026-08-31T09:00:00+00:00"  # age from the first demotion


def test_push_keeps_distinct_subjects_apart(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    a = {"op": "check", "line": "row a", "evidence": "commit aaa111", "repo": None}
    b = {"op": "check", "line": "row b", "evidence": "commit bbb222", "repo": None}
    assert td._push_pending(dict(a), "evidence did not verify") == "new"
    assert td._push_pending(dict(b), "evidence did not verify") == "new"
    assert len(td.load_pending()) == 2


def test_requeue_of_known_subject_counts_deduped_not_pending(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] fix the parser\n\n## done\n\n## dismissed\n")
    mut = {"op": "check", "line": "fix the parser",
           "evidence": "commit deadbeef00", "repo": "/nonexistent"}
    res1 = td.apply_mutations([dict(mut)])
    assert res1["pending"] == 1 and res1["deduped"] == 0
    res2 = td.apply_mutations([dict(mut)])
    assert res2["pending"] == 0 and res2["deduped"] == 1
    items = td.load_pending()
    assert len(items) == 1 and items[0]["seen"] == 2


def test_prune_coalesces_pre_fold_survivor_duplicates(tmp_path, monkeypatch):
    repo, _, unlanded = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] still open item\n\n## done\n\n## dismissed\n")
    rows = [
        _row("still open item", f"scan 2026-08-10: commit {unlanded[:10]}", repo),
        _row("- [ ] still open item", f"scan 2026-08-15: commit {unlanded[:10]}", repo),
        _row("still open item", f"scan 2026-08-17: commit {unlanded[:10]}", repo),
    ]
    td.save_pending(rows)
    counts = td.prune_pending()
    assert counts == {"applied": 0, "moot": 0, "stale": 0, "kept": 1, "merged": 2}
    items = td.load_pending()
    assert len(items) == 1
    assert items[0]["seen"] == 3
    assert items[0]["evidence"] == rows[-1]["evidence"]  # latest attempt wins
    assert items[0]["queued"] == rows[0]["queued"]      # oldest age survives


def test_prune_without_duplicates_reports_no_merged_count(tmp_path, monkeypatch):
    # Existing exact-dict assertions across this suite stay green only because
    # `merged` is conditional; pin that explicitly for the one-row run.
    repo, _, unlanded = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] one item\n\n## done\n\n## dismissed\n")
    td.save_pending([_row("one item", f"commit {unlanded[:10]}", repo)])
    counts = td.prune_pending()
    assert counts == {"applied": 0, "moot": 0, "stale": 0, "kept": 1}

