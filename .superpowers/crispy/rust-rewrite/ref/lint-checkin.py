#!/usr/bin/env python3
"""Lint an orient-quick status card against its own scannability rules.

Reads markdown from stdin (or a file path argument) and checks every bullet
line against the concrete formatting rules the card is supposed to follow:

  - <= 120 chars per bullet (a bullet that long stops being a 3-second scan)
  - <= 2 commas per bullet (more nesting than that buries the payload)
  - <= 1 bold span per bullet (one bold phrase per line keeps the scan column
    meaningful; bolding everything cancels the contrast)

Exit 0 when every bullet passes, 1 when any bullet fails. Prints one line per
violation, prefixed with the line number, so a failing run is self-explanatory
and a passing run is silent.

This is a verification script, not part of the skill's teaching content: it
encodes the rules so an agent can check its own card before showing it, the
same way check_theme.py checks a theme's contrast ratios. The rules themselves
live in SKILL.md; this script is the mechanical gate over them.
"""

import re
import sys

MAX_CHARS = 120
MAX_COMMAS = 2
MAX_BOLD = 1

BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
BOLD_RE = re.compile(r"\*\*")


def lint_line(line: str) -> list[str]:
    problems = []
    text = line.rstrip("\n")
    if len(text) > MAX_CHARS:
        problems.append(f"too long ({len(text)} > {MAX_CHARS} chars)")
    if text.count(",") > MAX_COMMAS:
        problems.append(f"too many commas ({text.count(',')} > {MAX_COMMAS})")
    if len(BOLD_RE.findall(text)) // 2 > MAX_BOLD:
        problems.append("more than one bold span")
    return problems


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()

    failures = 0
    for i, line in enumerate(lines, start=1):
        m = BULLET_RE.match(line)
        if not m:
            continue
        for problem in lint_line(line):
            print(f"line {i}: {problem}: {line.rstrip()}")
            failures += 1

    if failures:
        print(f"{failures} violation(s) found.")
        return 1
    print("OK: all bullets pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
