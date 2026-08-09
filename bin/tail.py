#!/usr/bin/env python3
"""Denoised delta slice of a Claude Code transcript to stdout.
Used by the in-session live-tail step and for ad hoc inspection."""
import argparse
import re
import sys
from pathlib import Path

import jeeves_lib as jl


def discover(project_dir: str) -> Path:
    slug = re.sub(r"[^a-zA-Z0-9]", "-", project_dir)
    d = jl.projects_root() / slug
    if d.is_dir():
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            return files[0]
    jl.die(f"no session transcript found for {project_dir} (slug {slug})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", nargs="?")
    ap.add_argument("--discover", metavar="DIR",
                    help="print the newest jsonl path for a project dir and exit")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--max", dest="maxn", type=int, default=40)
    a = ap.parse_args()
    if a.discover and not a.jsonl:
        print(discover(a.discover))
        return
    path = Path(a.jsonl) if a.jsonl else None
    if path is None:
        ap.error("give a jsonl path, or --discover DIR to print a path")
    if not path.exists():
        jl.die(f"no such file: {path}")
    lines, new_offset, status = jl.read_delta(path, a.offset)
    entries = jl.denoise(lines, jl.load_config()["truncate"])[-a.maxn:]
    print(jl.render_slice(entries))
    print(f"-- offset={new_offset} status={status}", file=sys.stderr)


if __name__ == "__main__":
    main()
