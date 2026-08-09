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


def _branch_commit(repo, branch="feature"):
    """A commit that exists but is NOT in the base branch's ancestry."""
    import subprocess
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True)
    (repo / "g.txt").write_text("y")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "on branch"], check=True)
    h = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                       capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-"], check=True)
    return h


def test_unmerged_branch_commit_is_outstanding_not_landed(tmp_path):
    """A commit object existing proves nothing about it being merged. The old
    verify_evidence used `git cat-file -e`, so an unmerged branch commit read
    as shipped work."""
    repo, _ = _git_repo(tmp_path)
    bh = _branch_commit(repo)
    assert td.classify_evidence(f"commit {bh[:10]}", str(repo)) == td.OUTSTANDING
    assert td.verify_evidence(f"commit {bh[:10]}", str(repo)) is False


def test_merged_commit_is_landed(tmp_path):
    repo, h = _git_repo(tmp_path)
    assert td.classify_evidence(f"commit {h[:10]}", str(repo)) == td.LANDED


def test_missing_commit_is_unknown_not_outstanding(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("commit deadbeef00", str(repo)) == td.UNKNOWN


def test_commit_existence_checked_once_per_ref(tmp_path, monkeypatch):
    """classify_evidence's own cat-file -e gate already filters out shas that
    do not resolve, so _classify_commit must not re-run the identical check."""
    repo, h = _git_repo(tmp_path)
    calls = []
    real_runs = td._runs

    def fake_runs(args, cwd=None):
        calls.append(args)
        return real_runs(args, cwd=cwd)

    monkeypatch.setattr(td, "_runs", fake_runs)
    assert td.classify_evidence(f"commit {h[:10]}", str(repo)) == td.LANDED
    cat_files = [a for a in calls if a[:5] == ["git", "-C", str(repo), "cat-file", "-e"]]
    assert len(cat_files) == 1, f"expected one cat-file -e, got {len(cat_files)}"


def test_repo_slug_parses_ssh_and_https_remotes(tmp_path):
    import subprocess
    repo, _ = _git_repo(tmp_path)
    for url in ("https://github.com/o/r.git", "https://github.com/o/r",
                "git@github.com:o/r.git", "ssh://git@github.com/o/r.git"):
        subprocess.run(["git", "-C", str(repo), "remote", "remove", "origin"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)
        assert td._repo_slug(repo) == "o/r", url


def test_repo_slug_empty_without_remote(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td._repo_slug(repo) == ""


def test_prune_pending_applies_now_landed_evidence(tmp_path, monkeypatch):
    repo, h = _git_repo(tmp_path)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] init the repo\n\n## done\n\n## dismissed\n")
    td.save_pending([{"op": "check", "line": "init the repo",
                      "evidence": f"commit {h[:10]}", "repo": str(repo),
                      "reason": "evidence did not verify", "queued": "2026-08-01T00:00:00-04:00"}])
    res = td.prune_pending()
    assert res["applied"] == 1 and res["kept"] == 0
    assert td.load_pending() == []
    assert td.parse_ledger(td.ledger_path().read_text())["open"] == []


def test_prune_pending_drops_moot_row_for_already_dismissed_line(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    td.apply_add("dead thing", kind="loose-end", source="x")
    line = td.parse_ledger(td.ledger_path().read_text())["open"][0]
    text = line.replace("- [ ]", "").strip()
    td.apply_dismiss(text)
    td.save_pending([{"op": "check", "line": text, "evidence": "PR #1",
                      "repo": None, "reason": "evidence did not verify",
                      "queued": "2026-08-01T00:00:00-04:00"}])
    res = td.prune_pending()
    assert res["moot"] == 1 and res["kept"] == 0
    assert td.load_pending() == []


def test_prune_pending_keeps_genuinely_pending_row(tmp_path, monkeypatch):
    repo, _ = _git_repo(tmp_path)
    bh = _branch_commit(repo)
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] land the branch\n\n## done\n\n## dismissed\n")
    td.save_pending([{"op": "check", "line": "land the branch",
                      "evidence": f"commit {bh[:10]}", "repo": str(repo),
                      "reason": "evidence did not verify", "queued": "2026-08-01T00:00:00-04:00"}])
    res = td.prune_pending()
    assert res["kept"] == 1 and res["applied"] == 0
    assert len(td.load_pending()) == 1


def test_prune_pending_survives_duplicate_ledger_lines(tmp_path, monkeypatch):
    """Duplicated ledger lines make find_match raise; prune must keep the row
    for a human instead of crashing the whole drain."""
    _ledger(tmp_path, monkeypatch,
            "# jeeves todo ledger\n\n## open\n- [ ] dupe\n- [ ] dupe\n\n## done\n\n## dismissed\n")
    td.save_pending([{"op": "check", "line": "dupe", "evidence": "PR #1", "repo": None,
                      "reason": "evidence did not verify", "queued": "2026-08-01T00:00:00-04:00"}])
    res = td.prune_pending()
    assert res["stale"] + res["kept"] == 1


def test_prune_pending_empty_queue_is_a_noop(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    assert td.prune_pending() == {"applied": 0, "moot": 0, "stale": 0, "kept": 0}


def test_extract_refs_handles_multi_ref_evidence():
    """The ferry emits several refs in one string; anchored single-ref matching
    read all of these as unparseable and demoted them to pending forever."""
    assert td._extract_refs("commit 062563c, commit e0056ed") == [
        ("commit", "062563c"), ("commit", "e0056ed")]
    assert td._extract_refs("commit 062563c, e0056ed") == [
        ("commit", "062563c"), ("commit", "e0056ed")]
    assert td._extract_refs("commit dcfcab4 + 01442dd (PR #114, PR #115)") == [
        ("commit", "dcfcab4"), ("commit", "01442dd"), ("pr", "114"), ("pr", "115")]
    assert td._extract_refs("PR #302") == [("pr", "302")]
    assert td._extract_refs("file docs/x.md") == [("file", "docs/x.md")]


def test_extract_refs_emits_shas_without_the_word_commit(tmp_path):
    """A commit reference is just a sha; evidence does not reliably say
    "commit" next to it. Gating on that keyword dropped real refs."""
    assert td._extract_refs("062563c and e0056ed shipped") == [
        ("commit", "062563c"), ("commit", "e0056ed")]


def test_hex_shaped_non_sha_is_dropped_not_counted_unknown(tmp_path):
    """A hex-lettered word is a candidate at extraction, but git resolves it to
    nothing, so it must not drag a real landed ref down to UNKNOWN."""
    repo, h = _git_repo(tmp_path)
    assert ("commit", "defaced") in td._extract_refs("defaced things")
    assert td.classify_evidence(f"defaced {h[:10]}", repo) == td.LANDED
    assert td.classify_evidence("defaced things", repo) == td.UNKNOWN


def test_extract_refs_empty_for_freetext_evidence():
    """Free-text evidence with no ref-shaped token at all."""
    assert td._extract_refs("pipeline zero-output fix") == []


def test_multi_ref_landed_only_when_every_ref_landed(tmp_path):
    repo, h = _git_repo(tmp_path)
    bh = _branch_commit(repo)
    assert td.classify_evidence(f"commit {h[:10]}", repo) == td.LANDED
    # one merged + one unmerged is not "landed"
    assert td.classify_evidence(f"commit {h[:10]}, {bh[:10]}", repo) == td.OUTSTANDING


def test_freetext_evidence_is_unknown(tmp_path):
    repo, _ = _git_repo(tmp_path)
    assert td.classify_evidence("archived 5 branches", repo) == td.UNKNOWN


def test_issue_evidence_is_classified_as_an_issue_not_a_pr(monkeypatch):
    """`gh-axi pr view` fails on an issue number, so classifying every `#N`
    as a pr would leave issue-backed evidence permanently unknown."""
    calls = []

    def fake_capture(args):
        calls.append(args)
        if args[:3] == ["gh-axi", "issue", "view"]:
            return "  state: closed\n"
        return ""

    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", fake_capture)
    monkeypatch.setattr(td, "_ISSUE_CACHE", {})
    assert td.classify_evidence("issue #391 filed", "/repo") == td.LANDED
    assert ["gh-axi", "issue", "view", "391", "-R", "o/r"] in calls


def test_open_issue_is_outstanding(monkeypatch):
    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", lambda args: "  state: open\n")
    monkeypatch.setattr(td, "_ISSUE_CACHE", {})
    assert td.classify_evidence("issue #391", "/repo") == td.OUTSTANDING


def test_bare_hash_ref_is_still_read_as_a_pr(monkeypatch):
    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", lambda args: "  state: merged\n")
    monkeypatch.setattr(td, "_PR_CACHE", {})
    assert td.classify_evidence("PR #419 merged", "/repo") == td.LANDED


def test_classify_pr_memoized_per_repo_and_number(monkeypatch):
    """Repeated evidence citing the same PR must not re-hit gh-axi."""
    calls = []

    def fake_capture(args):
        calls.append(args)
        return "  state: merged\n"

    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", fake_capture)
    if hasattr(td, "_PR_CACHE"):
        monkeypatch.setattr(td, "_PR_CACHE", {})
    assert td._classify_pr("424242", "/repo") == td.LANDED
    assert td._classify_pr("424242", "/repo") == td.LANDED
    assert len(calls) == 1


def test_classify_issue_memoized_per_repo_and_number(monkeypatch):
    """Repeated evidence citing the same issue must not re-hit gh-axi."""
    calls = []

    def fake_capture(args):
        calls.append(args)
        return "  state: closed\n"

    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", fake_capture)
    if hasattr(td, "_ISSUE_CACHE"):
        monkeypatch.setattr(td, "_ISSUE_CACHE", {})
    assert td._classify_issue("424243", "/repo") == td.LANDED
    assert td._classify_issue("424243", "/repo") == td.LANDED
    assert len(calls) == 1


def test_repo_slug_accepts_an_owner_repo_value(tmp_path):
    # the ferry is asked for a local path but sometimes writes owner/repo
    assert td._repo_slug("jeremysball/taskferry") == "jeremysball/taskferry"
    assert td._repo_slug("/no/such/dir/anywhere") == ""


def test_repo_slug_still_prefers_a_real_checkout(tmp_path):
    repo, _ = _git_repo(tmp_path)
    import subprocess
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/o/r.git"], check=True)
    assert td._repo_slug(str(repo)) == "o/r"


def _memo_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(td, "_MEMO", None)


def _stub_gh_axi(monkeypatch, calls):
    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", lambda args: calls.append(args) or "  state: merged\n")
    monkeypatch.setattr(td, "_PR_CACHE", {})


def test_classify_evidence_memoized_in_process(tmp_path, monkeypatch):
    """Two calls with the same (evidence, repo) in one process: the second is
    served from the in-memory memo, not a second gh-axi round trip."""
    _memo_env(tmp_path, monkeypatch)
    calls = []
    _stub_gh_axi(monkeypatch, calls)
    assert td.classify_evidence("PR #424244", "/repo") == td.LANDED
    assert td.classify_evidence("PR #424244", "/repo") == td.LANDED
    assert len(calls) == 1


def test_classify_evidence_memo_shared_across_processes(tmp_path, monkeypatch):
    """A fresh process must be served by the disk memo, not re-ask gh-axi."""
    import os
    import subprocess
    import sys
    _memo_env(tmp_path, monkeypatch)
    calls = []
    _stub_gh_axi(monkeypatch, calls)
    assert td.classify_evidence("PR #424245", "/repo") == td.LANDED
    assert len(calls) == 1
    td._save_memo()
    code = (
        "import sys; sys.path.insert(0, %r); import todos as td; "
        "calls = []; "
        "td._repo_slug = lambda repo: 'o/r'; "
        "td._capture = lambda args: calls.append(args) or '  state: merged\\n'; "
        "td._PR_CACHE = {}; "
        "v = td.classify_evidence('PR #424245', '/repo'); "
        "print(v, len(calls))"
    ) % (str(Path(__file__).parent.parent / "bin"),)
    env = dict(os.environ, JEEVES_STATE_DIR=str(tmp_path / "state"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    verdict, n_calls = r.stdout.strip().split()
    assert verdict == td.LANDED
    assert n_calls == "0"


def test_classify_evidence_stale_memo_entry_is_recomputed(tmp_path, monkeypatch):
    """An entry older than the TTL is treated as absent and recomputed."""
    import time
    _memo_env(tmp_path, monkeypatch)
    stale = {"/repo\x1fPR #424246": {"verdict": td.LANDED, "t": time.time() - 400}}
    (jl.state_dir() / "evidence_memo.json").write_text(json.dumps(stale))
    calls = []
    _stub_gh_axi(monkeypatch, calls)
    assert td.classify_evidence("PR #424246", "/repo") == td.LANDED
    assert len(calls) == 1


def test_classify_evidence_survives_malformed_memo_file(tmp_path, monkeypatch):
    """Garbage in the memo file is not an error: fall back to computing fresh."""
    _memo_env(tmp_path, monkeypatch)
    (jl.state_dir() / "evidence_memo.json").write_text("{not json!!")
    calls = []
    _stub_gh_axi(monkeypatch, calls)
    assert td.classify_evidence("PR #424247", "/repo") == td.LANDED
    assert len(calls) == 1


# Regression pair: `_MEMO` is a lazy-once-per-process module global with no
# per-test reset, so without project-wide isolation, whichever test runs
# first populates it and every later test in the same pytest process reuses
# that in-memory dict -- silently serving a stale verdict instead of hitting
# its own fakes. Reproduced live: `test_open_issue_is_outstanding` passed in
# isolation but failed on a second full-suite run because a prior test had
# already cached "issue #391 filed" as LANDED, and this pair's real disk
# write additionally leaked into ~/.local/state/jeeves/evidence_memo.json.
def test_memo_first_pass_writes_landed(monkeypatch):
    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", lambda args: "  state: merged\n")
    monkeypatch.setattr(td, "_PR_CACHE", {})
    assert td.classify_evidence("PR #900001", "/repo") == td.LANDED


def test_memo_second_pass_is_not_polluted_by_the_first(monkeypatch):
    calls = []
    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")
    monkeypatch.setattr(td, "_capture", lambda args: calls.append(args) or "  state: open\n")
    monkeypatch.setattr(td, "_PR_CACHE", {})
    assert td.classify_evidence("PR #900001", "/repo") == td.OUTSTANDING
    assert calls, "served from a memo the first test populated, not this test's fake"


# Round-2 review findings on the memo itself (fixed in this same PR, verified
# by ferrying-code-review before merge): the disk memo unconditionally cached
# UNKNOWN from a transient/undeterminable lookup, contradicting the inner
# caches' own "never cache a failure" rule, and a valid-but-wrong-shape memo
# file crashed classify_evidence instead of degrading to an empty cache.

def test_memo_never_caches_unknown(tmp_path, monkeypatch):
    """A transient failure (gh-axi unreachable) must not poison a later,
    successful call within the same TTL window the way a real answer would."""
    _memo_env(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(td, "_repo_slug", lambda repo: "o/r")

    def fake_capture(args):
        calls.append(args)
        return "" if len(calls) == 1 else "  state: merged\n"

    monkeypatch.setattr(td, "_capture", fake_capture)
    monkeypatch.setattr(td, "_PR_CACHE", {})
    assert td.classify_evidence("PR #900002", "/repo") == td.UNKNOWN
    assert td.classify_evidence("PR #900002", "/repo") == td.LANDED
    assert len(calls) == 2, "second call was served from the memo instead of retrying"


def test_memo_survives_wrong_shape_json(tmp_path, monkeypatch):
    """Syntactically valid JSON that is the wrong shape (not a dict of
    entries) must degrade to an empty cache, not crash classify_evidence."""
    _memo_env(tmp_path, monkeypatch)
    (jl.state_dir() / "evidence_memo.json").write_text("null")
    calls = []
    _stub_gh_axi(monkeypatch, calls)
    assert td.classify_evidence("PR #900003", "/repo") == td.LANDED
    assert len(calls) == 1


def test_memo_survives_a_list_instead_of_a_dict(tmp_path, monkeypatch):
    _memo_env(tmp_path, monkeypatch)
    (jl.state_dir() / "evidence_memo.json").write_text("[1, 2, 3]")
    calls = []
    _stub_gh_axi(monkeypatch, calls)
    assert td.classify_evidence("PR #900004", "/repo") == td.LANDED
    assert len(calls) == 1


def test_memo_survives_an_entry_missing_required_keys(tmp_path, monkeypatch):
    """A single malformed entry (e.g. schema drift, a hand edit) must not
    crash the lookup for that key -- it degrades to a cache miss."""
    _memo_env(tmp_path, monkeypatch)
    (jl.state_dir() / "evidence_memo.json").write_text(
        json.dumps({"/repo\x1fPR #900005": {"verdict": td.LANDED}})  # missing "t"
    )
    calls = []
    _stub_gh_axi(monkeypatch, calls)
    assert td.classify_evidence("PR #900005", "/repo") == td.LANDED
    assert len(calls) == 1
