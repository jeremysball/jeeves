#!/usr/bin/env bash
# Report-only sweep for worktree/branch drift across every git repo one level
# under a root directory. See ../docs/design.md for the full design.
#
# Usage: audit-worktrees.sh [--no-content] [root-dir]
#   root-dir defaults to the current directory. Pass a single repo path to
#   audit just that repo (cheap enough for a SessionStart hook).
#   --no-content skips the content-merge scoring pass (the expensive one).
#     Accepted in any argument position.
#
# Output: a per-repo report bucketed by what you'd actually do about each
# branch. Never modifies anything.

set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# --no-content is accepted in any position: the sibling CLI (bin/coverage-score)
# already rejects misplaced flags, and a flag silently swallowed as the root dir
# runs the expensive pass a caller explicitly asked to skip.
NO_CONTENT=false
declare -a POSITIONAL=()
for arg in "$@"; do
  if [ "$arg" = "--no-content" ]; then NO_CONTENT=true; else POSITIONAL+=("$arg"); fi
done
ROOT="${POSITIONAL[0]:-$(pwd)}"

if [ -z "${WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD+x}" ]; then
  CONTENT_MERGE_THRESHOLD=95                     # unset -> default
else
  CONTENT_MERGE_THRESHOLD=$(validate_pct "$WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD")
  # 0 validates as a percentage but is not a legal threshold here: `pct >= 0`
  # is true for SCORED 0, i.e. for a branch whose work is provably NOT in base,
  # so a 0 threshold offers every open branch for batch archive.
  if [ "$CONTENT_MERGE_THRESHOLD" = "invalid" ] || [ "$CONTENT_MERGE_THRESHOLD" = "0" ]; then
    echo "  (skipped: WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD must be an integer 1-100 with no leading zero, got '$WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD')" >&2
    CONTENT_MERGE_THRESHOLD=95
  fi
fi

audit_repo() {
  local repo="$1"
  cd "$repo"

  local base
  base=$(detect_base)
  if [ -z "$base" ]; then
    echo "  (skipped: no main/master/origin-HEAD found, can't determine base branch)"
    return
  fi

  # branch -> worktree path, from porcelain worktree list. Parsed line-by-line
  # (not word-split) so paths containing spaces survive intact.
  declare -A wt_path=()
  local cur_path="" cur_branch=""
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) cur_path="${line#worktree }"; cur_branch="" ;;
      "branch "*) cur_branch="${line#branch refs/heads/}" ;;
      "")
        if [ -n "$cur_branch" ]; then wt_path["$cur_branch"]="$cur_path"; fi
        cur_path=""; cur_branch=""
        ;;
    esac
  done < <(git worktree list --porcelain; echo "")

  # Upstream state via for-each-ref's own fields rather than grepping
  # human-oriented `branch -vv` text (which can false-match a commit subject
  # containing the literal string ": gone]").
  declare -A gone=() has_upstream=()
  while IFS=$'\t' read -r b up track; do
    [ -n "$up" ] && has_upstream["$b"]=1
    [[ "$track" == *"[gone]"* ]] && gone["$b"]=1
  done < <(git for-each-ref refs/heads \
    --format=$'%(refname:short)\t%(upstream:short)\t%(upstream:track)')

  local any=0 inflight_count=0
  local -a safe=() triage=() archaeology=() handsoff=() unknown_merge=() content_merged=()

  while IFS= read -r branch; do
    [ "$branch" = "$base" ] && continue
    any=1

    local path="${wt_path[$branch]:-}"

    # In-flight wins over every other bucket, including safe-to-clean: the
    # hazard isn't only losing commits, it's removing a worktree someone is
    # sitting in. Cheap enough to compute first and bail.
    local age
    age=$(activity_age_secs "$branch" "$path")
    if [ "$age" -lt "$IN_FLIGHT_SECS" ]; then
      inflight_count=$(( inflight_count + 1 ))
      continue
    fi

    local merge_rc=0
    git merge-base --is-ancestor "$branch" "$base" 2>/dev/null || merge_rc=$?
    # exit 1 = definitively not an ancestor (unmerged); >1 = real error
    # (unrelated histories, bad ref, etc.) - don't guess, flag it.
    if [ "$merge_rc" -gt 1 ]; then
      unknown_merge+=("$branch")
      continue
    fi
    local merged=true
    [ "$merge_rc" -eq 1 ] && merged=false

    local is_content_merged=false unscored=false reason="unmerged"
    if [ "$NO_CONTENT" = false ] && [ "$merged" = false ]; then
      local score
      score=$(coverage_score "$repo" "$base" "$branch")
      case "$score" in
        SCORED*)
          local pct="${score#SCORED }"
          if [ "$pct" -ge "$CONTENT_MERGE_THRESHOLD" ]; then
            is_content_merged=true
          else
            # Below threshold: the branch's work is provably NOT in base (e.g.
            # SCORED 0 = nothing landed). Surface the score so the triage line
            # distinguishes "genuinely outstanding" from "might have landed" —
            # dropping it here made every SCORED-0 branch read as a bare
            # "unmerged" with no signal, which is the case the tool exists for.
            reason="$reason, SCORED $pct"
          fi
          ;;
        UNSCORED*|UNKNOWN*)
          # Unscored / unknown -> cannot prove it shipped. It must go to
          # needs-triage, NEVER archaeology (never batch-archived) and never
          # clean-safe. Record the flag so the split below forces triage.
          unscored=true
          reason="$reason, ${score%% *}"
          ;;
      esac
    fi

    local status="none"
    if [ -n "$path" ]; then
      local gitdir
      gitdir=$(worktree_gitdir "$path") || gitdir=""
      if [ -n "$gitdir" ] && [ -f "$gitdir/locked" ]; then
        status=$(lock_status "$gitdir/locked")
      fi
    fi

    local dirty
    dirty=$(worktree_dirty_count "$path")

    local desc="$branch"
    [ -n "$path" ] && desc="$branch  (worktree: $path)"
    desc="$desc  [idle $(human_age "$age")]"

    if [ "$status" = "live" ] || [ "$status" = "unknown" ]; then
      handsoff+=("$desc  [lock: $status]")
    elif [ "$merged" = true ]; then
      # Merged means 0 commits ahead, so there is nothing to lose - but
      # uncommitted files in the worktree are NOT in base and would die with
      # it. Send those to triage instead of calling them safe.
      if [ "$dirty" -gt 0 ]; then
        triage+=("$desc — merged, but $dirty uncommitted file(s) would be lost")
      else
        safe+=("$desc")
      fi
    else
      [ "$status" = "stale" ] && reason="unmerged, stale lock (dead session)"
      [ -n "${gone[$branch]:-}" ] && reason="$reason, upstream deleted"
      [ "$dirty" -gt 0 ] && reason="$reason, $dirty uncommitted file(s)"

      if [ "$is_content_merged" = true ]; then
        # Carry the dirty-file detail through: this bucket routes to the strict
        # archive path, which refuses a dirty worktree. Dropping the detail here
        # left the user with a refusal and no stated reason.
        local cm_note=""
        [ "$dirty" -gt 0 ] && cm_note=", $dirty uncommitted file(s)"
        content_merged+=("$desc — content-merged (work already in base, different hash)$cm_note")
      elif [ "$unscored" = true ]; then
        # Unscored or unknown coverage: needs-triage, never archaeology.
        triage+=("$desc — $reason")
      elif [ "$age" -ge "$ARCHAEOLOGY_SECS" ] && [ -z "${has_upstream[$branch]:-}" ]; then
        archaeology+=("$desc — $reason, never pushed")
      else
        triage+=("$desc — $reason")
      fi
    fi
  done < <(git for-each-ref refs/heads --format='%(refname:short)')

  # Dangling worktree registrations (prunable): report, don't touch.
  # Porcelain emits "prunable <reason>", not a bare "prunable" line.
  local -a prunable=()
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) cur_path="${line#worktree }" ;;
      "prunable"*) prunable+=("$cur_path") ;;
    esac
  done < <(git worktree list --porcelain)

  if [ "$any" -eq 0 ] && [ ${#prunable[@]} -eq 0 ]; then
    return
  fi
  # Nothing actionable and nothing dangling: stay quiet rather than printing a
  # header for a repo whose only branches are in flight.
  if [ ${#safe[@]} -eq 0 ] && [ ${#triage[@]} -eq 0 ] && [ ${#archaeology[@]} -eq 0 ] \
     && [ ${#handsoff[@]} -eq 0 ] && [ ${#unknown_merge[@]} -eq 0 ] && [ ${#prunable[@]} -eq 0 ] \
     && [ ${#content_merged[@]} -eq 0 ]; then
    return
  fi

  echo "=== $repo (base: $base) ==="
  if [ ${#safe[@]} -gt 0 ]; then
    echo "  safe-to-clean (merged, clean):"
    printf '    %s\n' "${safe[@]}"
  fi
  if [ ${#triage[@]} -gt 0 ]; then
    echo "  needs-triage:"
    printf '    %s\n' "${triage[@]}"
  fi
  if [ ${#archaeology[@]} -gt 0 ]; then
    echo "  archaeology (older than $(human_age "$ARCHAEOLOGY_SECS"), never pushed — batch archive):"
    printf '    %s\n' "${archaeology[@]}"
  fi
  if [ ${#content_merged[@]} -gt 0 ]; then
    echo "  likely-content-merged (work already in base under a different hash — batch archive):"
    printf '    %s\n' "${content_merged[@]}"
  fi
  if [ ${#handsoff[@]} -gt 0 ]; then
    echo "  hands-off (live or unrecognized lock — never touch):"
    printf '    %s\n' "${handsoff[@]}"
  fi
  if [ ${#unknown_merge[@]} -gt 0 ]; then
    echo "  couldn't determine merge state (unrelated history / bad ref):"
    printf '    %s\n' "${unknown_merge[@]}"
  fi
  if [ ${#prunable[@]} -gt 0 ]; then
    echo "  dangling worktree registrations (git worktree prune is safe):"
    printf '    %s\n' "${prunable[@]}"
  fi
  if [ "$inflight_count" -gt 0 ]; then
    echo "  ($inflight_count in flight, active within $(human_age "$IN_FLIGHT_SECS") — hidden)"
  fi
  echo
}

while IFS= read -r gitdir; do
  repo=$(dirname "$gitdir")
  # Resolve to an absolute path before auditing. audit_repo cd's into the repo
  # and then hands the same path to coverage_score, whose `git -C "$repo"` calls
  # would re-resolve a relative path against the repo itself and fail - which
  # silently turned every branch UNKNOWN whenever the root was relative.
  repo=$(cd -- "$repo" 2>/dev/null && pwd) || continue
  ( audit_repo "$repo" )
done < <(fd -H -t d '^\.git$' "$ROOT" --max-depth 2)
