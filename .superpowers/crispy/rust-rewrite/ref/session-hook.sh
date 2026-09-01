#!/usr/bin/env bash
# SessionStart hook: surfaces worktree/branch drift without being asked.
#
# The skill it wraps only fired on "audit my worktrees" — it ran when you
# remembered to ask, and remembering is the thing that fails. This runs
# regardless.
#
# REPORT ONLY. It never archives, deletes, commits, or moves anything. A hook
# that mutated git state on session start would be a worse problem than the
# drift it reports.
#
# Two modes:
#   in a repo  -> full report for that repo (~60ms)
#   above one  -> one line per repo with counts (~1.3s for ~50 repos)
# The summary matters: dumping every stranded branch at every session start
# rebuilds the same wall of noise that made the original report ignorable.
#
# Exits silently (no output, rc 0) when there's nothing to say, when git or the
# audit script is unavailable, or on any error. A broken hook must never block
# a session from starting.

set -uo pipefail

BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIT="$BIN/audit-worktrees.sh"
[ -x "$AUDIT" ] || exit 0
# shellcheck source=summary-parser.sh
source "$BIN/summary-parser.sh"
command -v git >/dev/null 2>&1 || exit 0
command -v jq  >/dev/null 2>&1 || exit 0

# Hook payload arrives on stdin; prefer its cwd over the process's.
payload=$(cat 2>/dev/null) || payload=""
cwd=$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null) || cwd=""
[ -n "$cwd" ] && [ -d "$cwd" ] && cd "$cwd" 2>/dev/null

TIMEOUT="${WORKTREE_AUDIT_HOOK_TIMEOUT:-15}"

if root=$(git rev-parse --show-toplevel 2>/dev/null) && [ -n "$root" ]; then
  mode=repo
else
  root="$PWD"
  mode=sweep
fi

out=$(timeout "$TIMEOUT" "$AUDIT" --no-content "$root" 2>/dev/null)
rc=$?
if [ "$rc" -eq 124 ]; then
  jq -n --arg ctx "Worktree audit exceeded its ${TIMEOUT}s budget in $(basename "$root") and was skipped. Run the auditing-worktrees skill manually for the full report." \
    '{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:$ctx}}'
  exit 0
fi
[ "$rc" -ne 0 ] && exit 0
[ -n "$out" ] || exit 0

if [ "$mode" = repo ]; then
  body="Worktree drift in $(basename "$root") (from the auditing-worktrees SessionStart hook, report only):

$out
Nothing here has been changed. To act on it, use the auditing-worktrees skill."
else
  # Collapse to one line per repo. Anything longer trains the reader to skip it.
  summary=$(summary_parse "$out")
  [ -n "$summary" ] || exit 0
  body="Worktree drift across $root (from the auditing-worktrees SessionStart hook, report only):

$summary
Nothing here has been changed. Use the auditing-worktrees skill for detail or to act on it."
fi

jq -n --arg ctx "$body" \
  '{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:$ctx}}'
