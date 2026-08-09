import importlib.util
import subprocess
import sys
from pathlib import Path


def _load(name, fname):
    p = Path(__file__).parent.parent / "bin" / fname
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ic = _load("install_cron", "install-cron.py")

COLLECT = Path("/home/x/.claude/skills/jeeves/bin/collect.py")
EXISTING = "0 * * * * /usr/bin/other-job\n"


def test_install_into_empty():
    out = ic.install("", COLLECT)
    assert "# BEGIN jeeves" in out and "# END jeeves" in out
    assert "13 * * * *" in out
    assert str(COLLECT) in out


def test_install_preserves_existing_and_idempotent():
    once = ic.install(EXISTING, COLLECT)
    twice = ic.install(once, COLLECT)
    assert once == twice
    assert "other-job" in twice
    assert twice.count("# BEGIN jeeves") == 1


def test_uninstall_clean():
    installed = ic.install(EXISTING, COLLECT)
    removed = ic.uninstall(installed)
    assert "jeeves" not in removed
    assert "other-job" in removed


def test_status():
    assert ic.has_jeeves(ic.install("", COLLECT)) is True
    assert ic.has_jeeves(EXISTING) is False


def test_cli_roundtrip_file_mode(tmp_path):
    ct = tmp_path / "crontab"
    ct.write_text(EXISTING)
    script = str(Path(__file__).parent.parent / "bin" / "install-cron.py")
    env = dict(__import__("os").environ, JEEVES_STATE_DIR=str(tmp_path / "state"))
    r = subprocess.run([sys.executable, script, "--install", "--crontab-file", str(ct)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    assert "# BEGIN jeeves" in ct.read_text()
    r = subprocess.run([sys.executable, script, "--status", "--crontab-file", str(ct)],
                       capture_output=True, text=True, env=env)
    assert "installed" in r.stdout
    r = subprocess.run([sys.executable, script, "--uninstall", "--crontab-file", str(ct)],
                       capture_output=True, text=True, env=env)
    assert "jeeves" not in ct.read_text()
