#!/usr/bin/env bash
# Deletes branches already confirmed safe-to-clean by audit-worktrees.sh - merged
# into base, 0 commits ahead, clean, idle, unlocked or stale-locked. Re-verifies
# every branch itself right before deleting; never trusts a stale report.
#
# Usage: clean-safe.sh <repo-path> <branch-name> [<branch-name>...]

set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

REPO="${1:?usage: clean-safe.sh <repo-path> <branch> [<branch>...]}"; shift
[ "$#" -ge 1 ] || { echo "usage: clean-safe.sh <repo-path> <branch> [<branch>...]" >&2; exit 1; }

cd "$REPO"
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

BASE=$(detect_base)
[ -n "$BASE" ] || { echo "refusing: can't determine base branch for $REPO_ROOT" >&2; exit 1; }

PRIMARY=$(git worktree list --porcelain | head -1 | sed 's/^worktree //')

rc=0
for branch in "$@"; do
  if [ "$branch" = "$BASE" ]; then
    echo "refusing $branch: this is the base branch" >&2; rc=1; continue
  fi

  if ! git show-ref --verify --quiet "refs/heads/$branch"; then
    echo "skip $branch: no such local branch" >&2; continue
  fi

  merge_rc=0
  git merge-base --is-ancestor "$branch" "$BASE" 2>/dev/null || merge_rc=$?
  if [ "$merge_rc" -ne 0 ]; then
    echo "refusing $branch: not confirmed merged into $BASE (re-check gave exit $merge_rc) — do not delete" >&2
    rc=1; continue
  fi

  path=$(worktree_path_for_branch "$branch")

  if [ -n "$path" ] && [ "$path" = "$PRIMARY" ]; then
    echo "refusing $branch: checked out in the primary worktree ($path)" >&2
    rc=1; continue
  fi

  # Merged and idle are separate questions. A branch can be 0 commits ahead of
  # base and still have a session sitting in its worktree right now; removing
  # the directory under it loses no commits but breaks the session.
  age=$(activity_age_secs "$branch" "$path")
  if [ "$age" -lt "$IN_FLIGHT_SECS" ]; then
    echo "refusing $branch: active $(human_age "$age") ago, under the $(human_age "$IN_FLIGHT_SECS") in-flight threshold" >&2
    rc=1; continue
  fi

  # Merged says every *commit* is in base. It says nothing about uncommitted
  # files, which are in no commit and would be destroyed with the worktree.
  dirty=$(worktree_dirty_count "$path")
  if [ "$dirty" -gt 0 ]; then
    echo "refusing $branch: $dirty uncommitted file(s) in $path — merged, but these are in no commit and would be lost" >&2
    rc=1; continue
  fi

  if [ -n "$path" ]; then
    gitdir=$(worktree_gitdir "$path") || gitdir=""
    if [ -n "$gitdir" ] && [ -f "$gitdir/locked" ]; then
      status=$(lock_status "$gitdir/locked")
      if [ "$status" != "stale" ]; then
        echo "refusing $branch: worktree lock is $status, not provably dead — do not delete" >&2
        rc=1; continue
      fi
      git worktree unlock "$path"
    fi
    git worktree remove "$path"
  fi

  # -d (not -D): still refuses if the branch's own upstream tracking ref has
  # commits not on this branch, even though we already confirmed it's merged
  # into BASE — a real "someone pushed more since you last pulled" signal, not
  # a bug to force past. But that's a per-branch refusal, not fatal: without
  # this guard, `set -e` kills the whole loop and every later branch in the
  # batch silently never gets processed. The worktree (if any) is already gone
  # by this point; only the branch ref itself is left for manual follow-up.
  if ! git branch -d "$branch"; then
    echo "refusing $branch: git branch -d declined (see message above) — worktree removed, branch ref left for manual review" >&2
    rc=1; continue
  fi
done

git worktree prune
exit "$rc"
