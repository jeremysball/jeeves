#!/usr/bin/env python3
"""Idempotent crontab entry for jeeves' hourly collection run."""
import argparse
import os
import subprocess
from pathlib import Path

import jeeves_lib as jl

BEGIN, END = "# BEGIN jeeves", "# END jeeves"
COLLECT = Path(__file__).resolve().parent / "collect.py"


def _shims_dir() -> Path:
    """Where mise keeps its shim farm. Honors MISE_DATA_DIR and
    XDG_DATA_HOME, plus a JEEVES_MISE_SHIMS override for tests/odd layouts."""
    ovr = os.environ.get("JEEVES_MISE_SHIMS")
    if ovr:
        return Path(ovr)
    data = os.environ.get("MISE_DATA_DIR")
    if not data:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        data = str(Path(xdg) / "mise")
    return Path(data) / "shims"


def _cron_path() -> str:
    """PATH for the cron environment.

    Every entry here must be a *stable directory*: one that survives tool
    upgrades untouched. Resolving each tool through `shutil.which` at
    install time instead bakes the mise shim's resolved *versioned* install
    dir (e.g. `.../installs/fd/latest/fd-v10.4.2-.../`) into the crontab,
    and the next `mise upgrade` deletes that directory out from under cron.
    That is how the git-state scan silently reported `fd not found on PATH`
    for days after fd moved 10.4.2 → 10.5.0 (and gh's versioned dir sat
    stale in the same entry), unnoticed until a manual read of the
    snapshot's error line.

    The mise-native answer for a fixed `PATH` is the shims directory:
    `fd`, `gh`, and `python3` resolve through it to whatever version is
    current, forever, with no reinstall. ~/.local/bin carries the
    non-mise user binaries taskferry and gh-axi (and mise itself, the
    shims' symlink target); /usr/{local/,}bin and /bin are the fallback
    floor for git, bash, and the system python3. `fd` belongs on PATH as
    much as gh: scan-active.sh does all repo discovery through it, so a
    PATH without it reports every workspace as empty."""
    dirs = []
    for d in (str(Path.home() / ".local" / "bin"), str(_shims_dir()),
              "/usr/local/bin", "/usr/bin", "/bin"):
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


def _entry(collect_path: Path) -> str:
    logf = jl.state_dir() / "collect.log"
    return (f"13 * * * * PATH={_cron_path()} /usr/bin/env python3 "
            f"{collect_path} >> {logf} 2>&1")


def install(text: str, collect_path: Path = COLLECT) -> str:
    body = f"{BEGIN}\n{_entry(collect_path)}\n{END}\n"
    text = uninstall(text)
    return text.rstrip("\n") + "\n" + body if text.strip() else body


def uninstall(text: str) -> str:
    out, dropping = [], False
    for line in text.splitlines():
        if line.strip() == BEGIN:
            dropping = True
            continue
        if line.strip() == END:
            dropping = False
            continue
        if not dropping:
            out.append(line)
    return "\n".join(out) + ("\n" if out else "")


def has_jeeves(text: str) -> bool:
    return BEGIN in text


def _read(args) -> str:
    if args.crontab_file:
        p = Path(args.crontab_file)
        return p.read_text() if p.exists() else ""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def _write(args, text: str) -> None:
    if args.crontab_file:
        Path(args.crontab_file).write_text(text)
        return
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="install-cron.py")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    g.add_argument("--status", action="store_true")
    ap.add_argument("--crontab-file", help="operate on a file instead of the live crontab")
    a = ap.parse_args()
    text = _read(a)
    if a.status:
        print("jeeves cron: installed" if has_jeeves(text) else "jeeves cron: not installed")
        return
    if a.install:
        _write(a, install(text))
        jl.log("cron installed")
        print("jeeves cron installed (13 * * * *)")
    else:
        _write(a, uninstall(text))
        jl.log("cron uninstalled")
        print("jeeves cron removed")


if __name__ == "__main__":
    main()
