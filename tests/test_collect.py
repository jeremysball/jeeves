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

    def fake_ferry(prompt, model, wait_s=420):
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
