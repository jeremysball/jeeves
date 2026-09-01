#!/usr/bin/env bash
# Compact git state for one directory. Handles non-git dirs gracefully.
# Usage: git-state.sh [dir]   (defaults to $(pwd))
set -uo pipefail

dir="${1:-$(pwd)}"
cd "$dir" 2>/dev/null || { echo "error: cannot cd to $dir"; exit 1; }

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$root" ]; then
  echo "repo: none (not a git repository)"
  echo "dir: $dir"
  exit 0
fi
cd "$root"

branch="$(git branch --show-current 2>/dev/null)"
[ -z "$branch" ] && branch="(detached)"

upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
if [ -n "$upstream" ]; then
  set -- $(git rev-list --left-right --count "HEAD...$upstream" 2>/dev/null || echo "0 0")
  tracking="ahead ${1:-0} / behind ${2:-0} (vs $upstream)"
else
  tracking="no upstream"
fi

echo "repo: $root"
echo "branch: $branch"
echo "tracking: $tracking"
echo "last-commit-iso: $(git log -1 --format=%cI 2>/dev/null || echo none)"
echo "last-commit-rel: $(git log -1 --format=%cr 2>/dev/null || echo none)"

echo "recent-commits:"
git log -5 --format='  %h %s (%cr)' 2>/dev/null

if [ -n "$(git status --short 2>/dev/null)" ]; then
  echo "dirty: yes"
  echo "status:"
  git -c color.ui=false status --short 2>/dev/null | head -40 | sed 's/^/  /'
  echo "diffstat:"
  git diff --stat 2>/dev/null | tail -20 | sed 's/^/  /'
else
  echo "dirty: no"
fi

echo "worktrees:"
git worktree list 2>/dev/null | sed 's/^/  /'
