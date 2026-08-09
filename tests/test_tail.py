import json
import subprocess
import sys
from pathlib import Path

TAIL = str(Path(__file__).parent.parent / "bin" / "tail.py")


def _run(*args, env_extra=None):
    import os
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, TAIL, *args], capture_output=True, text=True, env=env)


def test_slice_output(tmp_path):
    p = tmp_path / "s.jsonl"
    e = {"timestamp": "2026-07-29T10:00:00Z",
         "message": {"role": "user", "content": "ship it"}}
    p.write_text(json.dumps(e) + "\n")
    r = _run(str(p), env_extra={"JEEVES_STATE_DIR": str(tmp_path / "state")})
    assert r.returncode == 0
    assert "user: ship it" in r.stdout


def test_offset_skips_seen(tmp_path):
    p = tmp_path / "s.jsonl"
    e = {"timestamp": "t", "message": {"role": "user", "content": "one"}}
    line = json.dumps(e) + "\n"
    p.write_text(line + json.dumps(dict(e, message={"role": "user", "content": "two"})) + "\n")
    r = _run(str(p), "--offset", str(len(line)),
             env_extra={"JEEVES_STATE_DIR": str(tmp_path / "state")})
    assert "one" not in r.stdout
    assert "two" in r.stdout


def test_discover_by_slug(tmp_path):
    proj = tmp_path / "projects"
    slug_dir = proj / "-home-jeremy-myproj"
    slug_dir.mkdir(parents=True)
    (slug_dir / "a.jsonl").write_text("{}\n")
    r = _run("--discover", "/home/jeremy/myproj",
             env_extra={"JEEVES_PROJECTS_ROOT": str(proj),
                        "JEEVES_STATE_DIR": str(tmp_path / "state")})
    assert r.stdout.strip() == str(slug_dir / "a.jsonl")


def test_discover_no_match_fails(tmp_path):
    r = _run("--discover", "/no/such",
             env_extra={"JEEVES_PROJECTS_ROOT": str(tmp_path),
                        "JEEVES_STATE_DIR": str(tmp_path / "state")})
    assert r.returncode != 0
    assert "no session" in r.stderr
