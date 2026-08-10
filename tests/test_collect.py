import pytest
import json
from pathlib import Path

import collect as cc
import jeeves_lib as jl


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JEEVES_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JEEVES_PROJECTS_ROOT", str(tmp_path / "projects"))
    cfg = tmp_path / "state"
    cfg.mkdir(parents=True)
    (cfg / "config").write_text("model = test/model\ntrivial_min = 1\n")


def _mk_session(root, slug, sid, texts):
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    lines = [json.dumps({"sessionId": sid, "timestamp": f"t{i}",
                         "message": {"role": "user", "content": t}})
             for i, t in enumerate(texts)]
    p.write_text("\n".join(lines) + "\n")
    return p


def _staging_ferry(monkeypatch, *sids):
    """Stand in for a ferry that reads its staged slices and writes one
    summary file per session, which is the real contract."""
    captured = {"text": "", "dir": None}

    def fake_ferry(prompt, model, wait_s=420, directory=""):
        if not directory:  # the rendered-prose fallback path
            return {"ok": True, "task_id": "oc_fb", "error": "",
                    "message": '```json\n[]\n```'}
        d = Path(directory)
        captured["dir"] = d
        captured["text"] += "".join(f.read_text() for f in d.glob("*.jsonl"))
        for sid in sids:
            (d / f"summary-{sid}.json").write_text(json.dumps(
                {"session": sid, "shipped": [], "oversaw": [], "loose_ends": [],
                 "tangents": [], "overlooked": [], "failures": [], "shape": "s"}))
        return {"ok": True, "task_id": "oc_st", "error": "", "message": "Status: DONE"}

    monkeypatch.setattr(jl, "ferry", fake_ferry)
    return captured


def test_offsets_roundtrip(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    offs = {"/a.jsonl": {"offset": 42, "size": 42}}
    cc.offsets_save(offs)
    assert cc.offsets_load() == offs


def test_group_slices_merges_small_same_dir():
    slices = [{"dir": "/p", "sid": "a", "entries": [1, 2]},
              {"dir": "/p", "sid": "b", "entries": [1]},
              {"dir": "/q", "sid": "c", "entries": [1]},
              {"dir": "/p", "sid": "d", "entries": list(range(50))}]
    groups = cc.group_slices(slices, batch_under=10, batch_max=4)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 1, 2]  # a+b merged, c solo, d solo (too big)


def test_run_once_happy_path(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    root = tmp_path / "projects"
    _mk_session(root, "-home-x-proj1", "ses1", ["did the thing", "shipped it"])
    extracted = {"ok": True, "task_id": "oc_t_1", "error": "",
                 "message": '```json\n[{"session": "ses1", "shipped": [{"item": "thing", "evidence": "file f.txt"}], "oversaw": [], "loose_ends": ["edge trim"], "tangents": [], "overlooked": [], "shape": "short focused"}]\n```\nStatus: DONE'}
    digest = {"ok": True, "task_id": "oc_t_2", "error": "",
              "message": '```markdown\n# jeeves digest — D\n**Shipped**\n- thing\n```\n```json\n[{"op": "add", "line": "edge trim", "kind": "loose-end", "source": "proj1", "repo": null}]\n```\nStatus: DONE'}
    calls = []

    def fake_ferry(prompt, model, wait_s=420, directory=""):
        calls.append(prompt)
        return extracted if len(calls) == 1 else digest

    monkeypatch.setattr(jl, "ferry", fake_ferry)
    monkeypatch.setattr(cc, "tf_diff", lambda: "(test notes)")  # never shell out in tests
    counts = cc.run_once()
    assert counts["sessions"] == 1 and counts["extracted"] == 1
    assert counts["digest"] == 1
    # summary + digest files landed
    state = tmp_path / "state"
    assert list((state / "summaries").rglob("*.md"))
    assert list((state / "digests").glob("*.md"))
    # mutation applied to ledger
    assert "edge trim" in (tmp_path / "data" / "todo.md").read_text()
    # offset advanced — second run finds nothing new
    counts2 = cc.run_once()
    assert counts2["sessions"] == 0


def test_seed_offsets_skips_history(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    root = tmp_path / "projects"
    p = _mk_session(root, "-home-x-proj1", "ses1", ["old work"])
    n = cc.seed_offsets()
    assert n == 1
    off = cc.offsets_load()[str(p)]["offset"]
    assert off == p.stat().st_size  # everything so far marked seen
    # a collect run now finds nothing...
    monkeypatch.setattr(cc, "tf_diff", lambda: "(test notes)")
    monkeypatch.setattr(jl, "ferry", lambda *a, **k:
                        {"ok": True, "message": "```json\n[]\n```\nStatus: DONE",
                         "task_id": "oc_seed_1", "error": ""})
    counts = cc.run_once()
    assert counts["sessions"] == 0
    # ...but new bytes still get processed
    with p.open("a") as fh:
        fh.write(json.dumps({"timestamp": "t9", "message": {"role": "user", "content": "fresh"}}) + "\n")
    counts = cc.run_once()
    assert counts["sessions"] == 1


def test_collect_cwds():
    lines = [json.dumps({"cwd": "/home/x/a", "message": {"role": "user"}}),
             json.dumps({"cwd": "/home/x/b"}),
             "garbage"]
    assert cc.collect_cwds(lines) == {"/home/x/a", "/home/x/b"}


def test_run_once_tolerates_mislabeled_single_slice(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    root = tmp_path / "projects"
    _mk_session(root, "-home-x-proj1", "ses1", ["work happened", "more work"])
    # ferry returns one valid entry but labels it with the wrong session id
    extracted = {"ok": True, "task_id": "oc_t_9", "error": "",
                 "message": '```json\n[{"session": "WRONG-NAME", "shipped": [{"item": "thing", "evidence": "file f.txt"}], "oversaw": [], "loose_ends": [], "tangents": [], "overlooked": [], "shape": "fine"}]\n```\nStatus: DONE'}
    monkeypatch.setattr(jl, "ferry", lambda *a, **k: extracted)
    monkeypatch.setattr(cc, "tf_diff", lambda: "(test notes)")
    counts = cc.run_once()
    assert counts["extracted"] == 1
    summary = next((tmp_path / "state" / "summaries").rglob("*--ses1--*.md")).read_text()
    assert "WRONG-NAME" not in summary
    assert "thing" in summary  # content kept, session re-keyed


def test_run_once_crash_does_not_advance(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    root = tmp_path / "projects"
    _mk_session(root, "-home-x-proj1", "ses1", ["real work"])
    monkeypatch.setattr(jl, "ferry", lambda *a, **k:
                        {"ok": False, "message": "", "task_id": "", "error": "crashed"})
    counts = cc.run_once()
    assert counts["extracted"] == 0 and counts["failed"] == 1
    # offset NOT advanced: next run sees the session again
    counts2 = cc.run_once()
    assert counts2["sessions"] == 1
    assert "crashed" in (tmp_path / "state" / "collect.log").read_text()


def test_parse_origin_tolerates_trailing_newline_and_git_suffix():
    # `git remote get-url` output keeps its newline; the old [^/.]+ class ate
    # it, producing repos/owner/name\n/pulls -> invalid control character
    assert cc._parse_origin("https://github.com/jeremysball/dotfiles-stow\n") == \
        "jeremysball/dotfiles-stow"
    assert cc._parse_origin("https://github.com/jeremysball/jeeves.git\n") == \
        "jeremysball/jeeves"
    assert cc._parse_origin("git@github.com:jeremysball/taskferry.git\n") == \
        "jeremysball/taskferry"
    assert cc._parse_origin("https://gitlab.com/x/y\n") is None


def test_flag_refs_leaves_real_refs_alone(monkeypatch):
    monkeypatch.setattr(cc, "_repo_index", lambda: {"taskferry": "o/taskferry"})
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 500)
    text = "- taskferry: concurrency limits merged (`PR #413`)\n"
    out, flagged = cc._flag_unverified_refs(text)
    assert out == text and flagged == []


def test_flag_refs_marks_out_of_range_ref(monkeypatch):
    monkeypatch.setattr(cc, "_repo_index", lambda: {"taskferry": "o/taskferry"})
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 500)
    out, flagged = cc._flag_unverified_refs("- taskferry: invented (`PR #9001`)\n")
    assert flagged == ["9001"] and "UNVERIFIED" in out


def test_flag_refs_ignores_unresolvable_repo(monkeypatch):
    # no repo context => no claim either way; smearing it would be the bug
    monkeypatch.setattr(cc, "_repo_index", lambda: {})
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 1)
    text = "- some-thing: see #9001\n"
    out, flagged = cc._flag_unverified_refs(text)
    assert out == text and flagged == []


def test_flag_refs_scopes_per_line(monkeypatch):
    monkeypatch.setattr(cc, "_repo_index", lambda: {"taskferry": "o/taskferry"})
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 500)
    text = "- taskferry: real (`PR #413`)\n- mystery-repo: unknown (`PR #9001`)\n"
    out, flagged = cc._flag_unverified_refs(text)
    assert flagged == []  # second line names no known repo, so untouched
    assert out == text


def test_flag_refs_resolves_repo_named_mid_line(monkeypatch):
    # real digest shape: "- taskferry PR #421 (3.2.0), #401, #407"
    monkeypatch.setattr(cc, "_repo_index", lambda: {"taskferry": "o/taskferry"})
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 500)
    out, flagged = cc._flag_unverified_refs(
        "- taskferry PR #421 (3.2.0 release), #401 (ro/rw), #9001 (invented)\n")
    assert flagged == ["9001"]
    assert "#421 [UNV" not in out and "#401 [UNV" not in out


def test_flag_refs_handles_attached_repo_form(monkeypatch):
    # real digest shape: "- External PR taskferry#417 (ubf-hunter)"
    monkeypatch.setattr(cc, "_repo_index",
                        lambda: {"taskferry": "o/taskferry", "catbow": "o/catbow"})
    monkeypatch.setattr(cc, "_ref_ceiling",
                        lambda full: 500 if full == "o/taskferry" else 9)
    out, flagged = cc._flag_unverified_refs("- Open: taskferry#417, catbow#98\n")
    assert flagged == ["98"]
    assert "taskferry#417," in out


def test_flag_refs_abstains_when_two_repos_share_a_line(monkeypatch):
    # judging a bare ref against the wrong repo's numbering is the old bug
    monkeypatch.setattr(cc, "_repo_index",
                        lambda: {"taskferry": "o/taskferry", "catbow": "o/catbow"})
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 9)
    text = "- taskferry and catbow both touched by #413\n"
    out, flagged = cc._flag_unverified_refs(text)
    assert flagged == [] and out == text


def _env_carry(tmp_path, monkeypatch, trivial_min=4):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JEEVES_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JEEVES_PROJECTS_ROOT", str(tmp_path / "projects"))
    cfg = tmp_path / "state"
    cfg.mkdir(parents=True)
    (cfg / "config").write_text(f"model = test/model\ntrivial_min = {trivial_min}\n")
    # keep the run hermetic: no taskferry, no gh, no cross-repo git scan
    monkeypatch.setattr(cc, "tf_diff", lambda: "(test notes)")
    monkeypatch.setattr(cc, "gh_review", lambda: "(test github)")
    monkeypatch.setattr(cc, "git_state", lambda: "(test git state)")
    monkeypatch.setattr(cc, "_flag_unverified_refs", lambda t: (t, []))


def _age(path, hours):
    import os
    import time
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_trivial_slice_holds_offset_and_accumulates(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch)
    root = tmp_path / "projects"
    p = _mk_session(root, "-home-x-proj1", "ses1", ["one line"])
    monkeypatch.setattr(jl, "ferry", lambda *a, **k: pytest.fail("should not dispatch"))
    counts = cc.run_once()
    assert counts["skipped"] == 1 and counts["extracted"] == 0
    assert cc.offsets_load() == {}  # offset held, not advanced past the content

    # session resumes and crosses the threshold: the held lines come too
    with p.open("a") as fh:
        for i in range(5):
            fh.write(json.dumps({"timestamp": f"t{i}",
                                 "message": {"role": "user", "content": f"more {i}"}}) + "\n")
    staged = _staging_ferry(monkeypatch, "ses1")
    cc.run_once()
    # the ferry read the lines off disk, and the originally-skipped one is there
    assert "one line" in staged["text"]


def test_trivial_slice_on_a_dead_session_is_extracted_not_dropped(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch)
    root = tmp_path / "projects"
    p = _mk_session(root, "-home-x-proj1", "ses1", ["last words"])
    _age(p, 48)
    staged = _staging_ferry(monkeypatch, "ses1")
    counts = cc.run_once()
    assert counts["extracted"] == 1
    assert "last words" in staged["text"]


def test_missing_session_block_holds_offset_for_retry(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch, trivial_min=1)
    root = tmp_path / "projects"
    _mk_session(root, "-home-x-proj1", "ses1", ["a", "b"])
    _mk_session(root, "-home-x-proj1", "ses2", ["c", "d"])
    # ferry answers for ses1 only; ses2's slice went unread
    monkeypatch.setattr(jl, "ferry", lambda *a, **k: {
        "ok": True, "task_id": "oc_3", "error": "",
        "message": '```json\n[{"session": "ses1", "shipped": [], "oversaw": [], "loose_ends": [], "tangents": [], "overlooked": [], "shape": "s"}]\n```'})
    counts = cc.run_once()
    assert counts["extracted"] == 1 and counts["failed"] == 1
    held = [k for k in cc.offsets_load()]
    assert not any("ses2" in k for k in held)  # ses2 retries next run


def test_flag_refs_does_not_match_inside_a_longer_sibling_name(monkeypatch):
    # `\b` sits between a word char and `-`, so a plain \bcatbow\b would
    # match inside `catbow-tools` and judge it against catbow's numbering
    monkeypatch.setattr(cc, "_repo_index", lambda: {"catbow": "o/catbow"})
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 9)
    text = "- catbow-tools: shipped (`PR #9001`)\n"
    out, flagged = cc._flag_unverified_refs(text)
    assert flagged == [] and out == text
    # the real catbow still resolves
    _, flagged2 = cc._flag_unverified_refs("- catbow: shipped (`PR #9001`)\n")
    assert flagged2 == ["9001"]


def test_repo_origins_is_memoized_per_state_dir(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch)
    cc._ORIGINS.clear()
    (tmp_path / "state" / "project-dirs.txt").write_text("/nope/a\n/nope/b\n")
    calls = []
    real = cc.subprocess.run
    monkeypatch.setattr(cc.subprocess, "run",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    cc._repo_origins()
    first = len(calls)
    cc._repo_origins()
    assert len(calls) == first  # second call served from cache


def test_ref_ceiling_retries_once_before_caching_a_failure(monkeypatch):
    cc._CEILINGS.clear()
    attempts = []

    def flaky(args, timeout=45):
        attempts.append(args)
        return None if len(attempts) == 1 else [{"number": 42}]

    monkeypatch.setattr(cc, "_gh_json", flaky)
    assert cc._ref_ceiling("o/r") == 42
    assert len(attempts) == 2  # transient failure did not stick


def test_add_mutation_citing_an_impossible_ref_is_dropped(monkeypatch):
    monkeypatch.setattr(cc, "_repo_origins", lambda: [("/repo", "o/r")])
    monkeypatch.setattr(cc, "_ref_ceiling", lambda full: 500)
    bogus = {"op": "add", "line": "chase PR #9001 review", "repo": "/repo"}
    real = {"op": "add", "line": "chase PR #413 review", "repo": "/repo"}
    unresolvable = {"op": "add", "line": "chase PR #9001", "repo": None}
    check = {"op": "check", "line": "x", "evidence": "PR #9001", "repo": "/repo"}
    assert cc._add_is_disproved(bogus) is True
    assert cc._add_is_disproved(real) is False
    assert cc._add_is_disproved(unresolvable) is False
    assert cc._add_is_disproved(check) is False  # checks are verify_evidence's job


def test_staged_slice_is_raw_not_denoised(tmp_path, monkeypatch):
    """The whole point: tool calls and sidechains reach the model. denoise
    drops both, so the staged file must be the unfiltered delta."""
    _env_carry(tmp_path, monkeypatch, trivial_min=1)
    root = tmp_path / "projects"
    d = root / "-home-x-proj1"
    d.mkdir(parents=True)
    (d / "ses1.jsonl").write_text("\n".join([
        json.dumps({"timestamp": "t0", "message": {"role": "user", "content": "go"}}),
        json.dumps({"timestamp": "t1", "isSidechain": True,
                    "message": {"role": "assistant", "content": "subagent work"}}),
        json.dumps({"timestamp": "t2", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "git commit"}}]}}),
        json.dumps({"toolUseResult": {"stdout": "[main abc1234] shipped it"}}),
    ]) + "\n")
    staged = _staging_ferry(monkeypatch, "ses1")
    cc.run_once()
    text = staged["text"]
    assert "subagent work" in text      # isSidechain survives
    assert "tool_use" in text           # tool calls survive
    assert "abc1234" in text            # the evidence ref survives
    assert len(jl.denoise(text.splitlines(), 800)) < 4  # denoise would have cut it


def test_staged_summary_file_beats_the_final_message(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch, trivial_min=1)
    _mk_session(tmp_path / "projects", "-home-x-proj1", "ses1", ["a", "b"])

    def fake_ferry(prompt, model, wait_s=420, directory=""):
        if directory:
            (Path(directory) / "summary-ses1.json").write_text(json.dumps(
                {"session": "ses1", "shape": "from the file"}))
        return {"ok": True, "task_id": "oc_1", "error": "",
                "message": '```json\n[{"session": "ses1", "shape": "from the message"}]\n```'}

    monkeypatch.setattr(jl, "ferry", fake_ferry)
    counts = cc.run_once()
    assert counts["extracted"] == 1
    summary = next((tmp_path / "state" / "summaries").rglob("*--ses1--*.md")).read_text()
    assert "from the file" in summary and "from the message" not in summary


def test_falls_back_to_prose_when_no_summary_file_appears(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch, trivial_min=1)
    _mk_session(tmp_path / "projects", "-home-x-proj1", "ses1", ["a", "b"])
    seen = []

    def fake_ferry(prompt, model, wait_s=420, directory=""):
        seen.append(bool(directory))
        return {"ok": True, "task_id": "oc_1", "error": "",
                "message": '```json\n[{"session": "ses1", "shape": "from the message"}]\n```'}

    monkeypatch.setattr(jl, "ferry", fake_ferry)
    counts = cc.run_once()
    assert seen[:2] == [True, False]  # staged first, then the prose fallback
    assert counts["extracted"] == 1
    summary = next((tmp_path / "state" / "summaries").rglob("*--ses1--*.md")).read_text()
    assert "from the message" in summary


def test_staging_is_pruned_transcript_copies_do_not_linger(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch)
    old = cc.staging_root() / "2026-08-01--000000--0"
    old.mkdir(parents=True)
    (old / "x.jsonl").write_text("secrets\n")
    _age(old, 48)
    fresh = cc.staging_root() / "2026-08-09--120000--0"
    fresh.mkdir(parents=True)
    assert cc.prune_staging() == 1
    assert not old.exists() and fresh.exists()


def test_staged_content_is_redacted_before_it_leaves_the_host():
    cases = [
        ("ghp_abcdefghijklmnopqrstuvwxyz01", "ghp_"),
        ("github_pat_11ABCDEFG0abcdefghijklmno", "github_pat_"),
        ("sk-proj-abcdefghijklmnopqrstuvwx", "sk-"),
        ("AKIAIOSFODNN7EXAMPLE", "AKIA"),
        ("xoxb-1234567890-abcdefghij", "xoxb-"),
        ("xoxe-notarealtoken-fakevalue", "xoxe-"),
        ("xoxe.xoxp-notarealtoken-fakevalue", "xoxp-"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJhbG"),
        ('OPENAI_API_KEY="hunter2hunter2"', "hunter2"),
        ("password: correct-horse-battery", "correct-horse"),
        # multi-segment / camelCase key names, not just the bare keyword
        ("AWS_SECRET_ACCESS_KEY=AKIAWXYZ0123SECRETVALUE", "SECRETVALUE"),
        ("myAuthToken=abc123def456", "abc123def456"),
        ("PGPASSWORD=hi", "hi"),  # under the old 8-char floor
        ("MYSQL_PWD=short1", "short1"),
        # DATABASE_URL / raw DSN with embedded creds
        ("DATABASE_URL=postgres://user:hunterpass@db.internal:5432/app", "hunterpass"),
        # PEM private key block
        ("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAABBBBCCCC\n-----END OPENSSH PRIVATE KEY-----",
         "AAAABBBBCCCC"),
    ]
    for raw, leaked in cases:
        out = cc.redact(raw)
        assert leaked not in out, f"leaked {leaked!r} from {raw!r} -> {out!r}"
        assert "[REDACTED]" in out, f"no redaction marker for {raw!r} -> {out!r}"
    # ordinary transcript content survives untouched
    keep = 'commit abc1234 shipped it; ran `npm test`, 1138 passed'
    assert cc.redact(keep) == keep


def test_redact_handles_json_escaped_quotes():
    # stage_slice writes raw JSONL bytes: a `KEY="value"` that appeared
    # inside a JSON string field is escaped on disk as `KEY=\"value\"`
    # (backslash then quote), not the bare unescaped form.
    raw = 'OPENAI_API_KEY=\\"hunter2hunter2\\"'
    out = cc.redact(raw)
    assert "hunter2hunter2" not in out
    assert "[REDACTED]" in out


def test_stage_slice_redacts_and_locks_down_permissions(tmp_path, monkeypatch):
    _env_carry(tmp_path, monkeypatch)
    staging = cc.staging_root() / "run"
    staging.mkdir(parents=True)
    s = {"slug": "proj", "sid": "ses1",
         "raw": [json.dumps({"toolUseResult": {"stdout": "GH_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz01"}})]}
    f = cc.stage_slice(staging, s)
    body = f.read_text()
    assert "ghp_abcdefghijklmnopqrstuvwxyz01" not in body and "[REDACTED]" in body
    assert oct(f.stat().st_mode)[-3:] == "600"
