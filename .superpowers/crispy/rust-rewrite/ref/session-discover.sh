#!/usr/bin/env bash
# Discover the latest session log(s) for a project directory, tool-agnostic.
# Usage: session-discover.sh [project-dir]   (defaults to $(pwd))
# Emits KEY=VALUE lines (only for sources found):
#   CLAUDE_JSONL=<path>       newest Claude Code session log for this dir
#   OPENCODE_SESSION=<id>     latest OpenCode session whose cwd == this dir
# OpenCode lookup is best-effort and bounded (newest $ORIENT_OPENCODE_SCAN
# sessions, default 12); it reads only each export's header prefix.
set -uo pipefail

dir="${1:-$(pwd)}"
dir="$(cd "$dir" 2>/dev/null && pwd || echo "$dir")"

# --- Claude Code: project slug = absolute path, non-alphanumerics -> '-' ---
slug="$(printf '%s' "$dir" | tr -c 'a-zA-Z0-9' '-')"
proj="$HOME/.claude/projects/$slug"
latest=""
if [ -d "$proj" ]; then
  latest="$(ls -t "$proj"/*.jsonl 2>/dev/null | head -1)"
fi
# Fallback: fuzzy-match a projects dir whose name ends with the path tail.
if [ -z "$latest" ]; then
  tail_slug="$(basename "$dir" | tr -c 'a-zA-Z0-9' '-')"
  cand="$(fd -t d -d 1 ".*${tail_slug}\$" "$HOME/.claude/projects" 2>/dev/null | head -1)"
  [ -n "$cand" ] && latest="$(ls -t "$cand"/*.jsonl 2>/dev/null | head -1)"
fi
[ -n "$latest" ] && echo "CLAUDE_JSONL=$latest"

# --- OpenCode: match export .info.directory to dir, newest-first, bounded ---
k="${ORIENT_OPENCODE_SCAN:-12}"
if [ "$k" -gt 0 ] 2>/dev/null && command -v opencode >/dev/null 2>&1; then
  ids="$(timeout 30 opencode session list 2>/dev/null | rg -o '^ses_[A-Za-z0-9]+' | head -n "$k")"
  for id in $ids; do
    d="$(timeout 20 opencode export "$id" 2>/dev/null | head -c 800 \
          | rg -o '"directory"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 \
          | rg -o '"[^"]*"$' | tr -d '"')"
    if [ "$d" = "$dir" ]; then
      echo "OPENCODE_SESSION=$id"
      break
    fi
  done
fi
