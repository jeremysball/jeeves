#!/usr/bin/env python3
"""Idempotent crontab entry for jeeves' hourly collection run."""
import argparse
import shutil
import subprocess
from pathlib import Path

import jeeves_lib as jl

BEGIN, END = "# BEGIN jeeves", "# END jeeves"
COLLECT = Path(__file__).resolve().parent / "collect.py"


def _cron_path() -> str:
    """PATH for the cron environment. Cron defaults to /usr/bin:/bin, which
    lacks user-installed binaries the collector shells out to (taskferry,
    gh) — resolve their directories at install time rather than hardcoding
    any of them.

    `fd` matters as much as the rest: scan-active.sh does all repo discovery
    through it, so a cron PATH without it reports every workspace as empty."""
    dirs = []
    for tool in ("taskferry", "gh", "gh-axi", "fd", "git", "bash"):
        p = shutil.which(tool)
        if p:
            d = str(Path(p).parent)
            if d not in dirs:
                dirs.append(d)
    for d in ("/usr/local/bin", "/usr/bin", "/bin"):
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
