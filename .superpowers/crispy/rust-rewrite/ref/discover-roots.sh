#!/usr/bin/env bash
# Discover canonical git repo roots by deduplicating on remote URL.
# Emits one path per distinct origin, preferring the primary checkout
# (.git is a directory) over worktrees (.git is a file), tie-breaking
# by newest commit. Persists to a roots file for scan-active.sh to read.
#
# Why this exists: scan-active.sh used to hardcode /workspace and scan every
# git repo under it, including stale clones of the same remote. A clone left
# over from a migration (e.g. /workspace/dotfiles-stow, 129 commits behind
# origin/main) reported its own commits as "shipped" work and its own stale
# branches as loose ends, polluting every scan. Deduplicating on remote URL
# collapses those clones to one canonical path per repo, and preferring the
# primary checkout means the live tree wins over any worktree or stale clone.
set -uo pipefail

SELF="${BASH_SOURCE[0]}"
self_display="${SELF/#$HOME/\~}"

usage() {
  cat <<EOF
bin: $self_display
description: Canonical git repo roots, deduplicated by remote URL

usage: discover-roots.sh [root ...]

arguments:
  [root]   dirs to scan for git repos; defaults to \$ORIENT_ROOT_CANDIDATES
           (space/colon separated), else /workspace \$HOME/.claude \$HOME/.dotfiles

flags:
  --help   show this reference

environment:
  ORIENT_ROOT_CANDIDATES   dirs to scan when no [root] is given
  ORIENT_ROOTS_FILE        where to persist the discovered roots
                           (default \$XDG_STATE_HOME/orient/roots.txt)
EOF
}

for arg in "$@"; do
  case "$arg" in
    --help) usage; exit 0 ;;
    --*) echo "error: unknown flag $arg"; exit 2 ;;
  esac
done

if [ "$#" -gt 0 ]; then
  roots=("$@")
else
  IFS=': ' read -r -a roots <<< "${ORIENT_ROOT_CANDIDATES:-/workspace $HOME/.claude $HOME/.dotfiles}"
fi

ROOTS_FILE="${ORIENT_ROOTS_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/orient/roots.txt}"

# Normalize a remote URL to a canonical "host/path" form so https:// and
# git@ and .git-suffix variants of the same repo collapse to one key.
normalize_url() {
  local u="$1"
  u="${u#https://}"; u="${u#http://}"; u="${u#ssh://}"
  u="${u#git@}"
  u="${u/://}"          # git@host:path -> host/path
  u="${u%.git}"         # strip .git suffix
  printf '%s' "$u"
}

# Resolve origin URL for a repo dir, empty if none.
origin_of() {
  git -C "$1" remote get-url origin 2>/dev/null || true
}

# Is this a primary checkout (.git is a directory) vs a worktree (.git file)?
is_primary() {
  [ -d "$1/.git" ]
}

# Newest commit timestamp (unix) for a repo, 0 if none.
newest_ts() {
  git -C "$1" log -1 --format='%ct' 2>/dev/null || echo 0
}

declare -A SEEN_URL   # normalized url -> chosen path
declare -A SEEN_TS    # normalized url -> chosen newest ts
declare -A SEEN_PRIM  # normalized url -> 1 if chosen is primary

for root in "${roots[@]}"; do
  [ -d "$root" ] || continue
  while read -r gitdir; do
    repo="$(dirname "$gitdir")"
    url="$(origin_of "$repo")"
    [ -z "$url" ] && continue
    key="$(normalize_url "$url")"
    [ -z "$key" ] && continue
    prim=0; is_primary "$repo" && prim=1
    ts="$(newest_ts "$repo")"

    if [ -z "${SEEN_URL[$key]+x}" ]; then
      SEEN_URL[$key]="$repo"; SEEN_TS[$key]="$ts"; SEEN_PRIM[$key]="$prim"
      continue
    fi
    # Prefer primary over worktree; tie-break by newest commit.
    if [ "$prim" -gt "${SEEN_PRIM[$key]}" ]; then
      SEEN_URL[$key]="$repo"; SEEN_TS[$key]="$ts"; SEEN_PRIM[$key]="$prim"
    elif [ "$prim" -eq "${SEEN_PRIM[$key]}" ] && [ "$ts" -gt "${SEEN_TS[$key]}" ]; then
      SEEN_URL[$key]="$repo"; SEEN_TS[$key]="$ts"
    fi
  done < <(fd -t d -d 4 -H -E node_modules -E .cache '^\.git$' "$root" 2>/dev/null)
done

mkdir -p "$(dirname "$ROOTS_FILE")"
printf '%s\n' "${SEEN_URL[@]}" | sort > "$ROOTS_FILE"

echo "bin: $self_display"
echo "description: Canonical git repo roots, deduplicated by remote URL"
echo "roots_file: $ROOTS_FILE"
echo "count: ${#SEEN_URL[@]} distinct remotes"
echo
echo "roots[${#SEEN_URL[@]}]{path}:"
printf '%s\n' "${SEEN_URL[@]}" | sort | sed 's/^/  /'
