import json
import os
from pathlib import Path
import jeeves_lib as jl


def test_state_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path / "s"))
    d = jl.state_dir()
    assert d == tmp_path / "s"
    assert d.is_dir()
    assert oct(d.stat().st_mode & 0o777) == "0o700"


def test_state_dir_xdg_default(tmp_path, monkeypatch):
    monkeypatch.delenv("JEEVES_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert jl.state_dir() == tmp_path / "xdg" / "jeeves"


def test_data_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_DATA_DIR", str(tmp_path / "d"))
    assert jl.data_dir() == tmp_path / "d"


def test_load_config_defaults_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path))
    cfg = jl.load_config()
    assert cfg["trivial_min"] == 4
    assert cfg["truncate"] == 800
    (tmp_path / "config").write_text("model = opencode/mimo-v2.5-free\ntrivial_min = 6\n")
    cfg = jl.load_config()
    assert cfg["model"] == "opencode/mimo-v2.5-free"
    assert cfg["trivial_min"] == 6


def test_log_appends_with_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path))
    jl.log("hello")
    jl.log("world")
    lines = (tmp_path / "collect.log").read_text().splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(" hello")
    assert "T" in lines[0]  # ISO timestamp prefix


def _jl(**kw):
    e = {"timestamp": "2026-07-29T10:00:00Z", "isSidechain": False,
         "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}}
    e.update(kw)
    return json.dumps(e)


def test_read_delta_stops_at_last_newline(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"a":1}\n{"b":2}\n{"partial":')
    lines, off, status = jl.read_delta(p, 0)
    assert lines == ['{"a":1}', '{"b":2}']
    assert status == "ok"
    assert p.read_text()[:off].endswith("\n")  # offset at a newline boundary


def test_read_delta_detects_rotation(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"a":1}\n')
    lines, off, status = jl.read_delta(p, 5000)
    assert status == "rotated"
    assert off == 8  # processed from 0 to last newline


def test_read_delta_incremental(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"a":1}\n')
    _, off, _ = jl.read_delta(p, 0)
    p.write_text('{"a":1}\n{"b":2}\n')
    lines, off2, _ = jl.read_delta(p, off)
    assert lines == ['{"b":2}']
    assert off2 > off


def test_denoise_structural_selection():
    lines = [
        _jl(),
        json.dumps({"type": "last-prompt", "sessionId": "x"}),          # bookkeeping
        json.dumps({"timestamp": "t", "attachment": {"type": "hook"}}),  # attachment
        _jl(isSidechain=True),                                          # subagent sidechain
        _jl(message={"role": "assistant",
                     "content": [{"type": "tool_use", "name": "Bash"},
                                 {"type": "text", "text": "done"}]}),
        "{not json",                                                    # garbage
    ]
    out = jl.denoise(lines)
    assert [e["x"] for e in out] == ["hello", "done"]
    assert out[1]["r"] == "assistant"


def test_denoise_strips_system_reminders_and_truncates():
    txt = "real words <system-reminder>ignore me</system-reminder> tail"
    out = jl.denoise([_jl(message={"role": "user", "content": txt})], truncate=20)
    assert "ignore me" not in out[0]["x"]
    assert len(out[0]["x"]) <= 20


def test_render_slice():
    out = jl.denoise([_jl()])
    assert jl.render_slice(out) == "[2026-07-29T10:00:00Z] user: hello"


def test_denoise_strips_embedded_nul_bytes():
    # A raw NUL byte can legitimately survive json.loads() via a JSON
    # unicode escape in the source transcript. It must never reach render_slice()'s
    # output, since that text becomes a taskferry dispatch --prompt argv
    # value, and subprocess.run() raises ValueError: embedded null byte on
    # any argv string containing one.
    txt = "hello\x00world"
    out = jl.denoise([_jl(message={"role": "user", "content": txt})])
    assert "\x00" not in out[0]["x"]
    assert out[0]["x"] == "helloworld"
    assert "\x00" not in jl.render_slice(out)


def test_normalize_collapses_and_strips_provenance():
    a = jl.normalize("  Fix   Pinentry  TTY handling (jeeves: loose-end, hearth, 2026-07-29)")
    assert a == "fix pinentry tty handling"


def test_line_hash_stable_across_surface_variance():
    assert jl.line_hash("Fix the thing") == jl.line_hash("  fix   THE thing  ")


def test_seen_store_roundtrip_and_count(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path))
    s = jl.SeenStore.load()
    h = jl.line_hash("rework prediction indicator")
    assert s.check(h) is None
    rec = s.upsert(h, "rework prediction indicator")
    assert rec["count"] == 1 and rec["status"] == "open"
    rec = s.upsert(h, "rework prediction indicator")
    assert rec["count"] == 2
    s.save()
    s2 = jl.SeenStore.load()
    assert s2.check(h)["count"] == 2
    assert [r["line"] for r in s2.by_status("open")] == ["rework prediction indicator"]


def test_seen_store_status_transitions(tmp_path, monkeypatch):
    monkeypatch.setenv("JEEVES_STATE_DIR", str(tmp_path))
    s = jl.SeenStore.load()
    h = jl.line_hash("x")
    s.upsert(h, "x")
    s.upsert(h, "x", status="dismissed")
    assert s.check(h)["status"] == "dismissed"
    assert s.by_status("open") == []


SAMPLE_RESULT = '''taskId: oc_abc123_def456
status: done
message: "line one \\"quoted\\"\\nline two"
narrationTotalChars: 10
'''


def test_parse_axi_message_handles_escapes():
    msg = jl.parse_axi_message(SAMPLE_RESULT)
    assert msg == 'line one "quoted"\nline two'


def test_parse_axi_message_missing():
    assert jl.parse_axi_message("status: crashed") is None


def test_parse_fenced_json():
    txt = 'prose\n```json\n{"a": [1, 2]}\n```\ntrailing'
    assert jl.parse_fenced_json(txt) == {"a": [1, 2]}
    assert jl.parse_fenced_json("no fences here") is None
    assert jl.parse_fenced_json("```json\n{broken\n```") is None


def test_tf_reports_embedded_null_byte_as_a_failed_call_not_a_crash(monkeypatch):
    # Regression: subprocess.run() raises ValueError("embedded null byte")
    # synchronously, before spawning anything, when an argv string still
    # contains a raw NUL -- this must surface the same way a spawn failure
    # already does (rc=1, error in stderr), never propagate out of _tf()
    # and kill the whole collect.py run.
    def raising_run(args, **kw):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(jl.subprocess, "run", raising_run)
    rc, out, err = jl._tf(["dispatch", "--prompt", "x", "--model", "x"])
    assert rc == 1
    assert out == ""
    assert "embedded null byte" in err


def test_ferry_failure_paths(monkeypatch):
    def fake_run(args, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"
        return R()

    monkeypatch.setattr(jl.subprocess, "run", fake_run)
    out = jl.ferry("prompt", "model/x")
    assert out["ok"] is False
    assert "dispatch" in out["error"]


def test_ferry_happy_path(monkeypatch):
    def fake_run(args, **kw):
        class R:
            returncode = 0
            stderr = ""
            if args[:2] == ["taskferry", "dispatch"]:
                stdout = "queued oc_abc123_def456"
            elif args[:2] == ["taskferry", "wait"]:
                stdout = "status: done"
            else:
                stdout = 'message: "answer\\nStatus: DONE"'
        return R()

    monkeypatch.setattr(jl.subprocess, "run", fake_run)
    out = jl.ferry("prompt", "model/x")
    assert out["ok"] is True
    assert out["task_id"] == "oc_abc123_def456"
    assert "Status: DONE" in out["message"]
