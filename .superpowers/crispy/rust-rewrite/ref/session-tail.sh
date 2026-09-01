#!/usr/bin/env bash
# Extract a compact, human-readable tail of a Claude Code JSONL session,
# dropping tool-call noise. Optionally only entries at/after an ISO timestamp.
# Usage: session-tail.sh <jsonl> [since-iso] [max-entries]
#   <since-iso>   e.g. 2026-07-12T14:00:00Z; "" for no lower bound
#   [max-entries] default 40
set -uo pipefail

f="${1:?usage: session-tail.sh <jsonl> [since-iso] [max]}"
since="${2:-}"
max="${3:-40}"

[ -f "$f" ] || { echo "error: no such file: $f"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "(jq unavailable; cannot parse session)"; exit 0; }

jq -rc --arg since "$since" '
  select(.timestamp != null and .message.role != null)
  | select($since == "" or (.timestamp >= $since))
  | { t: .timestamp, r: .message.role,
      x: ( (.message.content // [])
           | if type == "array"
             then (map(select(.type == "text") | .text) | join(" "))
             else tostring end ) }
  | select((.x | length) > 0)
  | "[\(.t)] \(.r): \(.x[0:800])"
' "$f" 2>/dev/null | tail -n "$max"
