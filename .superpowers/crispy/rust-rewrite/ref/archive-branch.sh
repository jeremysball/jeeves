#!/usr/bin/env bash
# Archives unmerged branches: preserves their commits under a permanent tag,
# then removes the branch and its worktree.
#
# Usage: archive-branch.sh <repo-path> <branch> [<branch>...]
#        archive-branch.sh --list <repo-path>
#        archive-branch.sh --strict <repo-path> <branch> [<branch>...]
#
# --strict refuses a dirty worktree (no WIP commit) and creates the tag via a
# verified `update-ref --stdin` transaction instead of `git tag`.
#
# Why a tag rather than just `git branch -D`: deleting a branch only drops the
# ref. The commits linger as unreachable objects until gc collects them -
# reflog entries for unreachable commits expire at 30 days (default), and gc
# prunes unreachable objects older than 2 weeks (default). gc runs on its own
# via gc.auto, so that deletion is silent and unrecoverable once it lands. A
# tag is a real ref, and refs make commits reachable; reachable objects are
# never pruned. So the tag turns "gone in two weeks, recoverable only by
# reflog forensics" into "kept forever, recoverable by name".
#
# Recover one with:  git branch <name> archive/<name>

set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "${1:-}" = "--list" ]; then
  cd "${2:?usage: archive-branch.sh --list <repo-path>}"
  git for-each-ref --sort=-creatordate \
    --format='%(refname:short)  %(objectname:short)  %(creatordate:short)' \
    "refs/tags/$ARCHIVE_PREFIX" 2>/dev/null
  exit 0
fi

STRICT=false
if [ "${1:-}" = "--strict" ]; then STRICT=true; shift; fi

REPO="${1:?usage: archive-branch.sh <repo-path> <branch> [<branch>...]}"; shift
[ "$#" -ge 1 ] || { echo "usage: archive-branch.sh <repo-path> <branch> [<branch>...]" >&2; exit 1; }

cd "$REPO"
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

BASE=$(detect_base)
[ -n "$BASE" ] || { echo "refusing: can't determine base branch for $REPO_ROOT" >&2; exit 1; }

# The primary worktree is the first entry git lists. Its checkout can't be
# removed and its branch can't be deleted while checked out.
PRIMARY=$(git worktree list --porcelain | head -1 | sed 's/^worktree //')

rc=0
for branch in "$@"; do
  if [ "$branch" = "$BASE" ]; then
    echo "refusing $branch: this is the base branch" >&2; rc=1; continue
  fi
  if ! git show-ref --verify --quiet "refs/heads/$branch"; then
    echo "skip $branch: no such local branch" >&2; continue
  fi

  path=$(worktree_path_for_branch "$branch")

  # Never switch someone's main checkout out from under them.
  if [ -n "$path" ] && [ "$path" = "$PRIMARY" ]; then
    echo "refusing $branch: checked out in the primary worktree ($path)." >&2
    echo "    Archiving it would have to switch that checkout to $BASE. Do it by hand if that's what you want." >&2
    rc=1; continue
  fi

  # Re-check liveness rather than trusting a report that may be minutes old.
  age=$(activity_age_secs "$branch" "$path")
  if [ "$age" -lt "$IN_FLIGHT_SECS" ]; then
    echo "refusing $branch: active $(human_age "$age") ago, under the $(human_age "$IN_FLIGHT_SECS") in-flight threshold" >&2
    rc=1; continue
  fi

  if [ -n "$path" ]; then
    gitdir=$(worktree_gitdir "$path") || gitdir=""
    if [ -n "$gitdir" ] && [ -f "$gitdir/locked" ]; then
      status=$(lock_status "$gitdir/locked")
      if [ "$status" != "stale" ]; then
        echo "refusing $branch: worktree lock is $status, not provably dead" >&2
        rc=1; continue
      fi
    fi

    # A tag only captures commits. Uncommitted files are in no commit, so the
    # tag would not save them and removing the worktree would destroy them
    # outright - not even reflog-recoverable. Commit first or don't touch it.
    dirty=$(worktree_dirty_count "$path")
    if [ "$dirty" -gt 0 ]; then
      if [ "$STRICT" = true ]; then
        echo "refusing $branch: strict mode does not archive a dirty worktree ($dirty uncommitted file(s) in $path)" >&2
        rc=1; continue
      fi
      echo "  $branch: committing $dirty uncommitted file(s) before archiving"
      if ! git -C "$path" add -A || \
         ! git -C "$path" commit -q -m "wip: archived with uncommitted changes"; then
        echo "refusing $branch: couldn't commit its uncommitted changes — not deleting anything" >&2
        rc=1; continue
      fi
    fi
  fi

  tag="$ARCHIVE_PREFIX/$branch"
  if git show-ref --verify --quiet "refs/tags/$tag"; then
    echo "refusing $branch: tag $tag already exists" >&2; rc=1; continue
  fi

  tip=$(git rev-parse "refs/heads/$branch")

  if [ "$STRICT" = true ]; then
    # Atomic: verify the branch still points at `tip`, then create the tag, all
    # in one transaction. `create` takes ONE OID (ref + new-value); `verify`
    # takes the expected old-value. Empirically verified: the whole transaction
    # aborts (rc 128) if the verify fails, leaving the branch and the would-be
    # tag untouched. A concurrent commit that moved the branch therefore blocks
    # the tag entirely — no stale tag is created.
    if ! printf 'start\nverify refs/heads/%s %s\ncreate refs/tags/%s %s\nprepare\ncommit\n' \
        "$branch" "$tip" "$tag" "$tip" | git update-ref --stdin 2>/dev/null; then
      echo "refusing $branch: branch moved during archive (atomic tag aborted) — not deleting" >&2
      rc=1; continue
    fi
  else
    git tag "$tag" "refs/heads/$branch"
    if [ "$(git rev-parse "refs/tags/$tag^{commit}")" != "$tip" ]; then
      echo "refusing $branch: tag $tag doesn't resolve to $tip — not deleting" >&2
      rc=1; continue
    fi
  fi

  # Fail early and loudly if the branch already moved, so we don't tear down a
  # worktree we're then going to refuse to finish deleting.
  if [ "$(git rev-parse "refs/heads/$branch" 2>/dev/null)" != "$tip" ]; then
    echo "refusing $branch: branch advanced after tagging (now $(git rev-parse --short refs/heads/$branch), expected $tip) — worktree/branch left in place" >&2
    rc=1; continue
  fi

  if [ -n "$path" ]; then
    [ -f "${gitdir:-}/locked" ] && git worktree unlock "$path"
    git worktree remove "$path"
  fi

  # Delete the ref CONDITIONALLY on it still pointing at `tip`. `git branch -D`
  # is an unconditional force-delete, so a commit landing between the re-check
  # above and the delete would take the new tip with it while the tag kept the
  # old one, stranding the new commit unreachable for gc to prune. `update-ref
  # --stdin` takes an expected old-value on `delete` and refuses the whole
  # transaction when the tip has moved, which closes that window instead of
  # merely narrowing it.
  if ! printf 'start\ndelete refs/heads/%s %s\nprepare\ncommit\n' "$branch" "$tip" \
      | git update-ref --stdin 2>/dev/null; then
    echo "refusing $branch: branch advanced during archive (conditional delete aborted) — tag $tag kept, branch left in place" >&2
    rc=1; continue
  fi

  echo "  archived $branch -> $tag ($(git rev-parse --short "$tip"))"
done

git worktree prune
exit "$rc"
