#!/usr/bin/env bash
# Shared helpers for the auditing-worktrees scripts. Source, don't execute.
#
# Thresholds are env-overridable so this works on machines with different
# working rhythms; never hardcode them at a call site.

# Activity newer than this means someone may be at the keyboard right now.
# In-flight beats every other classification, including safe-to-clean: the
# risk isn't only losing commits, it's yanking a directory out from under a
# live session.
IN_FLIGHT_SECS="${WORKTREE_AUDIT_INFLIGHT_SECS:-7200}"        # 2 hours

# Older than this with no upstream and the context is gone for good. These
# are offered as a batch archive rather than a per-branch judgment call.
ARCHAEOLOGY_SECS="${WORKTREE_AUDIT_ARCHAEOLOGY_SECS:-7776000}" # 90 days

ARCHIVE_PREFIX="${WORKTREE_AUDIT_ARCHIVE_PREFIX:-archive}"

detect_base() {
  if git show-ref --verify --quiet refs/heads/main; then
    echo main
  elif git show-ref --verify --quiet refs/heads/master; then
    echo master
  else
    git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@refs/remotes/origin/@@' || echo ""
  fi
}

# Echoes one of: a start-time string, "missing", or "unknown".
#   missing - the process provably does not exist (procfs absence).
#   unknown - /proc/<pid>/stat could not be read for any other reason.
proc_start_ticks() {
  local pid="$1" stat rest
  # A missing /proc/<pid> only proves the process is gone when procfs itself
  # is mounted. In a container or chroot without /proc, EVERY pid looks absent,
  # which would turn every live lock into "stale" - i.e. into a deletion. Probe
  # procfs first and report "unknown" when it isn't there.
  if [ ! -d "/proc/self" ]; then echo "unknown"; return; fi
  if [ ! -d "/proc/$pid" ]; then echo "missing"; return; fi
  stat=$(cat "/proc/$pid/stat" 2>/dev/null) || { echo "unknown"; return; }
  rest="${stat##*) }"
  [ "$rest" != "$stat" ] || { echo "unknown"; return; }
  local -a f
  read -r -a f <<< "$rest"
  [ "${#f[@]}" -ge 20 ] || { echo "unknown"; return; }
  echo "${f[19]}"
}

# Resolves a worktree's actual metadata dir via git itself rather than
# reconstructing it from the path's basename. Git disambiguates colliding
# basenames (two worktrees named "same" in different parents become
# .git/worktrees/same and .git/worktrees/same1) - deriving the name by hand
# reads the wrong worktree's lock file in that case.
worktree_gitdir() {
  git -C "$1" rev-parse --absolute-git-dir 2>/dev/null
}

# Echoes one of: live | stale | unknown.
#   live    - lock reason has a pid+start pair matching a running process.
#   stale   - lock reason has a pid+start pair, process is gone.
#   unknown - no lock file, or a lock reason that isn't a pid+start record
#             (e.g. a manual `git worktree lock --reason "..."`). Must be
#             treated the same as live - we cannot prove it's safe.
lock_status() {
  local lockfile="$1"
  [ -f "$lockfile" ] || { echo "unknown"; return; }
  local reason pid="" start=""
  reason=$(cat "$lockfile" 2>/dev/null) || { echo "unknown"; return; }
  [[ "$reason" =~ pid\ ([0-9]+) ]] && pid="${BASH_REMATCH[1]}"
  [[ "$reason" =~ start\ ([0-9]+) ]] && start="${BASH_REMATCH[1]}"
  if [ -z "$pid" ] || [ -z "$start" ]; then echo "unknown"; return; fi
  local live_start
  live_start=$(proc_start_ticks "$pid") || { echo "unknown"; return; }
  if [ "$live_start" = "missing" ]; then echo "stale"; return; fi
  if [ "$live_start" = "unknown" ]; then echo "unknown"; return; fi
  if [ "$live_start" = "$start" ]; then echo "live"; else echo "stale"; fi
}

# Finds the worktree path for a branch by parsing porcelain line-by-line (not
# word-split), so paths containing spaces survive intact.
worktree_path_for_branch() {
  local want="refs/heads/$1" cur_path="" cur_branch=""
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) cur_path="${line#worktree }"; cur_branch="" ;;
      "branch "*) cur_branch="${line#branch }" ;;
      "")
        if [ "$cur_branch" = "$want" ]; then echo "$cur_path"; return; fi
        cur_path=""; cur_branch=""
        ;;
    esac
  done < <(git worktree list --porcelain; echo "")
}

# Number of changed (modified, staged, deleted, or untracked) paths.
worktree_dirty_count() {
  local wt="$1"
  [ -n "$wt" ] && [ -d "$wt" ] || { echo 0; return; }
  git -C "$wt" status --porcelain 2>/dev/null | wc -l
}

# Newest mtime among files the user has actually touched. A clean worktree's
# files carry checkout mtimes, which are not activity - so only changed files
# count. Echoes nothing when the worktree is clean or absent.
newest_change_mtime() {
  local wt="$1"
  [ -n "$wt" ] && [ -d "$wt" ] || return 0
  {
    git -C "$wt" ls-files -z --modified --others --exclude-standard 2>/dev/null
    git -C "$wt" diff --cached --name-only -z 2>/dev/null
  } | ( cd "$wt" 2>/dev/null && xargs -0 -r stat -c '%Y' -- 2>/dev/null ) \
    | sort -rn | head -1
}

# Seconds since ANY activity on this branch, committed or not:
# max(last commit time, newest touched-file mtime).
#
# Neither signal works alone, and both failures were observed live in this
# workspace. Last-commit-time called a clawmarks branch idle while its
# worktree was being edited 2 minutes prior. File mtime called an omnisearch
# worktree active when it had been dirty and untouched for 28 days. Taking the
# max of the two classifies both correctly.
activity_age_secs() {
  local branch="$1" wt="${2:-}"
  local now newest commit_ts m
  now=$(date +%s)
  commit_ts=$(git log -1 --format='%ct' "$branch" 2>/dev/null) || commit_ts=""
  [ -n "$commit_ts" ] || commit_ts=0
  newest="$commit_ts"
  m=$(newest_change_mtime "$wt")
  if [ -n "$m" ] && [ "$m" -gt "$newest" ] 2>/dev/null; then newest="$m"; fi
  echo $(( now - newest ))
}

human_age() {
  local s="$1"
  if   [ "$s" -lt 3600 ];   then echo "$(( s / 60 ))m"
  elif [ "$s" -lt 86400 ];  then echo "$(( s / 3600 ))h"
  else                           echo "$(( s / 86400 ))d"
  fi
}

# Echoes the singular merge-base of two refs, or nothing on error. Runs every
# git call with -C "$1" so the result never depends on the caller's cwd. In a
# criss-cross history there are MULTIPLE merge bases; singular `git merge-base`
# does not error — it picks one arbitrarily, which would score against a
# possibly-wrong base. Count with --all first and treat >1 base as unknown.
# Echoes the base OID on success.
merge_base() {
  local repo="$1" base="$2" branch="$3" n
  n=$(git -C "$repo" merge-base --all "$base" "$branch" 2>/dev/null | wc -l)
  # >1 base = criss-cross history; singular merge-base would pick one
  # arbitrarily and score against a possibly-wrong base. Treat as unknown.
  [ "$n" -eq 1 ] || return 1
  git -C "$repo" merge-base "$base" "$branch" 2>/dev/null
}

# Sums net text lines from numstat input (added-minus-deleted). Echoes a string
# on one line:
#   "N"            - the net line delta, when every row is a normal text row.
#   "UNSCORED <why>" - a `- -` (binary) or `0 0` (mode-only) row appeared.
# Reads each row TAB-aware: numstat emits `add<TAB>del<TAB>path`; a naive
# "${row##*$'\t'}" grabs the pathname, not the delete count.
numstat_net() {
  local total=0 row a d p
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    IFS=$'\t' read -r a d p <<< "$row"
    if [ "$a" = "-" ] && [ "$d" = "-" ]; then { echo "UNSCORED binary"; return; }; fi
    if [ "$a" = "0" ] && [ "$d" = "0" ]; then { echo "UNSCORED mode-only"; return; }; fi
    total=$(( total + a - d ))
  done
  echo "$total"
}

# Echoes one of:
#   SCORED <0-100>   - the branch's net text lines that are already in base.
#   UNSCORED <why>   - binary/mode-only row, O==0, or empty patch.
#   UNKNOWN <why>    - criss-cross history, merge conflict, merge-tree error.
coverage_score() {
  local repo="$1" base="$2" branch="$3" mb out O R num
  mb=$(merge_base "$repo" "$base" "$branch") || { echo "UNKNOWN no-merge-base"; return; }
  [ -n "$mb" ] || { echo "UNKNOWN no-merge-base"; return; }

  # O: net text lines on the branch from its merge base.
  out=$(git -C "$repo" diff --no-ext-diff --no-textconv --numstat --no-renames "$mb" "$branch" 2>/dev/null) || { echo "UNKNOWN branch-diff-failed"; return; }
  O=$(numstat_net <<< "$out")
  # numstat_net already returns the documented "UNSCORED <why>" shape - echo it
  # verbatim. Decorating it here (a suffix on one path, a second "UNSCORED "
  # prefix on the other) produced three different strings for one verdict and
  # broke the contract the coverage-score CLI publishes to its consumers.
  case "$O" in
    UNSCORED*) { echo "$O"; return; } ;;
  esac
  [ "$O" -gt 0 ] || { echo "UNSCORED no-text-rows"; return; }

  # Simulated merge + residual diff in ONE subprocess so the synthesized tree
  # stays reachable. Capture merge-tree's rc separately from its output, then
  # run the diff only on success — all inside a subshell that keeps the object
  # dir live. merge-tree rc 1 = conflict, >1 = error -> UNKNOWN.
  local obj alt base_oid mtrc tree residual had_errexit=0
  obj=$(mktemp -d) || { echo "UNKNOWN no-temp-dir"; return; }
  alt=$(git -C "$repo" rev-parse --path-format=absolute --git-path objects 2>/dev/null)
  [ -n "$alt" ] || { rm -rf "$obj"; echo "UNKNOWN no-object-dir"; return; }
  base_oid=$(git -C "$repo" rev-parse "$base" 2>/dev/null)
  # Restore the caller's errexit rather than forcing it on: lib.sh is sourced
  # by consumers that deliberately run without `set -e`, and leaving errexit
  # armed behind us turns their next tolerated failure into an abort.
  case "$-" in *e*) had_errexit=1 ;; esac
  set +e
  residual=$(
    tree=$(GIT_OBJECT_DIRECTORY="$obj" GIT_ALTERNATE_OBJECT_DIRECTORIES="$alt" \
      git -C "$repo" merge-tree --write-tree "$base" "$branch" 2>/dev/null)
    mtrc=$?
    echo "RC=$mtrc"
    # An empty tree with rc 0 must NOT fall through: exiting 0 here would leave
    # an empty residual, which parses as R=0 and fails open to SCORED 100.
    if [ "$mtrc" -ne 0 ] || [ -z "$tree" ]; then
      [ "$mtrc" -ne 0 ] && exit "$mtrc"
      exit 2
    fi
    GIT_OBJECT_DIRECTORY="$obj" GIT_ALTERNATE_OBJECT_DIRECTORIES="$alt" \
      git -C "$repo" diff --no-ext-diff --no-textconv --numstat --no-renames "$base_oid" "$tree" 2>/dev/null
  )
  rc=$?
  [ "$had_errexit" -eq 1 ] && set -e
  rm -rf "$obj"
  mtrc=$(printf '%s\n' "$residual" | grep -o '^RC=[0-9]*' | head -1 | cut -d= -f2)
  residual=$(printf '%s\n' "$residual" | grep -v '^RC=[0-9]*' || true)
  if [ "${mtrc:-0}" -eq 1 ]; then { echo "UNKNOWN merge-conflict"; return; }; fi
  if [ "${mtrc:-0}" -gt 1 ] || [ "$rc" -gt 1 ]; then { echo "UNKNOWN merge-tree-error"; return; }; fi

  R=$(numstat_net <<< "$residual")
  case "$R" in
    UNSCORED*) { echo "$R"; return; } ;;
  esac

  # R is a NET line count, so it goes negative when the branch's own deletions
  # are missing from base (base ends up with more lines than the merged tree).
  # Subtracting a negative R inflated the score past O - a branch whose deletion
  # never shipped scored SCORED 111 and cleared the 95% bar. Residual work is
  # residual work whichever direction it points, so score against its magnitude
  # and clamp to the documented 0-100 range.
  local absR="${R#-}"
  num=$(( O - absR )); [ "$num" -lt 0 ] && num=0
  local pct=$(( num * 100 / O )); [ "$pct" -gt 100 ] && pct=100
  echo "SCORED $pct"
}

# Echoes the base-10 integer value, or "invalid". Strict decimal: rejects
# leading zeros ("08" is invalid, not 8 — a bare Bash arithmetic read would
# treat it as octal and silently change its meaning). Never emits a non-numeric
# string that a caller could feed to $(( )).
validate_pct() {
  local val="$1"
  case "$val" in
    ''|*[!0-9]*) echo invalid; return ;;
    # a leading zero with more than one digit is octal-looking -> invalid
    '0'*) [ "$val" = "0" ] || { echo invalid; return; } ;;
  esac
  # Reject anything too long to be a percentage BEFORE the arithmetic read:
  # $(( )) wraps modulo 2^64, so 18446744073709551616 would evaluate to 0 and
  # validate as a legal threshold - turning a nonsense value into "archive
  # everything" rather than into the intended rejection.
  [ "${#val}" -le 3 ] || { echo invalid; return; }
  local n
  n=$((val))            # safe: matched ^[0-9]+$ with no leading zero
  if [ "$n" -ge 0 ] && [ "$n" -le 100 ]; then echo "$n"; else echo invalid; fi
}
