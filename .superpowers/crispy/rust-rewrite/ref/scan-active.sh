#!/usr/bin/env bash
# List git repos under one or more roots that have local commits in a window.
# AXI-compliant: TOON on stdout, structured errors on stdout, diagnostics on stderr.
set -uo pipefail

SELF="${BASH_SOURCE[0]}"
# Collapse $HOME to ~ for the bin: line (AXI §10). Never print a hardcoded home path.
self_display="${SELF/#$HOME/\~}"

usage() {
  cat <<EOF
bin: $self_display
description: Git repos with local commits in a window, with per-branch push and merge state

usage: scan-active.sh <since> [root ...]

arguments:
  <since>   any \`git log --since\` expression, e.g. "yesterday 00:00", "1 day ago"  (required)
  [root]    dirs to scan; defaults to the discovered roots file (see below), else
            \$ORIENT_ROOTS (space/colon separated), else /workspace

flags:
  --help    show this reference

environment:
  ORIENT_ROOTS                             dirs to scan when no [root] is given and no roots file exists
  ORIENT_ROOTS_FILE                        path to the discovered-roots file (default
                                           \$XDG_STATE_HOME/orient/roots.txt). Written by
                                           discover-roots.sh, which deduplicates clones of the same
                                           remote to one canonical path per repo. When this file
                                           exists and is non-empty, it is the default root set.
  ORIENT_COMMIT_LIMIT                      commit rows per repo (default 15); the true total is always reported
  AUDIT_WORKTREES_LIB                      path to auditing-worktrees' lib.sh, which supplies the
                                           content-coverage pass (default ~/.claude/skills/auditing-worktrees/bin/lib.sh).
                                           Absent, branches are classified by ancestry and tree match alone.
  WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD   coverage % at or above which a non-ancestor branch is
                                           classified content-merged (default 95; 1-100)
  ORIENT_CONTENT_SCORING                   set to 0 to skip the content-coverage pass entirely, even
                                           when the auditing-worktrees lib is present (default 1)

examples:
  scan-active.sh "yesterday 00:00"
  scan-active.sh "1 week ago" /workspace /srv/code
  ORIENT_ROOTS=/workspace scan-active.sh "3 days ago"
EOF
}

# AXI §6: reject unknown flags by name before doing any work; --help always passes.
for arg in "$@"; do
  case "$arg" in
    --help) usage; exit 0 ;;
    --*)
      echo "error: unknown flag $arg for \`scan-active.sh\`"
      echo "help: the only flag is --help; positional args are <since> [root ...]"
      exit 2
      ;;
  esac
done

if [ "$#" -eq 0 ]; then
  echo "error: <since> is required"
  echo 'help: scan-active.sh "yesterday 00:00" [root ...]'
  exit 2
fi

# Repo discovery is entirely `fd`, and that call redirects stderr to /dev/null
# to stay quiet about unreadable dirs. Without this preflight a missing `fd`
# reads as "0 of 0 scanned repos" — indistinguishable from a genuinely quiet
# workspace, and wrong in exactly the direction that hides real work. Bites
# under cron, where a mise-shimmed `fd` is off PATH.
if ! command -v fd >/dev/null 2>&1; then
  echo "error: fd not found on PATH"
  echo "help: repo discovery needs \`fd\`; install it or add its dir to PATH (mise installs are not on a bare cron PATH)"
  exit 2
fi

since="$1"
shift

if [ "$#" -gt 0 ]; then
  roots=("$@")
else
  # Default root set is the discovered-roots file when it exists and is
  # non-empty, else ORIENT_ROOTS, else /workspace. The roots file is written
  # by discover-roots.sh (deduplicated by remote URL, primary checkout
  # preferred), so a stale clone of a repo no longer reports its own commits
  # as shipped work. Explicit [root ...] args always win over the file.
  ROOTS_FILE="${ORIENT_ROOTS_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/orient/roots.txt}"
  if [ -s "$ROOTS_FILE" ]; then
    mapfile -t roots < "$ROOTS_FILE"
  else
    IFS=': ' read -r -a roots <<< "${ORIENT_ROOTS:-/workspace}"
  fi
fi

# TOON string escaping: quote the field, escape embedded quotes and backslashes.
toon_str() {
  local s="${1//\\/\\\\}"
  printf '"%s"' "${s//\"/\\\"}"
}

COMMIT_LIMIT="${ORIENT_COMMIT_LIMIT:-15}"   # rows per repo; the true total is always reported

# Content-coverage detection is borrowed from auditing-worktrees rather than
# reimplemented here: a fourth independent copy of "did this branch land" is
# exactly how the existing three drifted apart. Sourcing is best-effort — with
# the lib absent this script degrades to the ancestry + tree-match answer it
# gave before, never to an error, because nothing here should hard-depend on an
# unrelated skill being installed.
AUDIT_LIB="${AUDIT_WORKTREES_LIB:-$HOME/.claude/skills/auditing-worktrees/bin/lib.sh}"
# An explicitly set override is a different case from an absent skill, and
# silence is only right for the second. A typo'd AUDIT_WORKTREES_LIB otherwise
# produces output byte-identical to not having the skill at all, so the
# operator's override is discarded with nothing to notice — say so, then carry
# on degraded rather than aborting a scan over an optional pass.
if [ -n "${AUDIT_WORKTREES_LIB:-}" ] && [ ! -r "$AUDIT_WORKTREES_LIB" ]; then
  echo "warning: AUDIT_WORKTREES_LIB is set to '$AUDIT_WORKTREES_LIB' but is not readable; content-coverage classification is off" >&2
fi
# shellcheck source=/dev/null
[ -r "$AUDIT_LIB" ] && . "$AUDIT_LIB"

content_scoring=no
CONTENT_MERGE_THRESHOLD=95
# The pass costs seven git invocations per branch it examines (merge-base,
# two numstat diffs, a merge-tree, and the rev-parses around them), so it needs
# a real off switch and not just "uninstall the other skill" — a wide scan over
# many repos with many stale branches is where it is most useful and most
# expensive at the same time.
if [ "${ORIENT_CONTENT_SCORING:-1}" != 0 ] \
   && declare -F coverage_score >/dev/null && declare -F validate_pct >/dev/null; then
  content_scoring=yes
  # Distinguish unset (the common case, take the default) from set-but-invalid
  # (a defined hard error on stderr plus the default), so a typo is never read
  # as a silently different threshold. 0 is rejected alongside the malformed
  # values: it would score every branch as content-merged, which is the one
  # setting that turns this pass into a blanket "everything landed".
  if [ -n "${WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD+x}" ]; then
    CONTENT_MERGE_THRESHOLD="$(validate_pct "$WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD")"
    if [ "$CONTENT_MERGE_THRESHOLD" = invalid ] || [ "$CONTENT_MERGE_THRESHOLD" = 0 ]; then
      echo "warning: WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD must be an integer 1-100 with no leading zero, got '$WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD'; using 95" >&2
      CONTENT_MERGE_THRESHOLD=95
    fi
  fi
fi

repos_scanned=0
active=()          # repo paths with commits in the window
declare -A R_BRANCH R_TREE R_ALERTS R_BRANCHLINES R_COMMITS R_TOTAL
declare -A SEEN_REPO   # repo path -> 1, so a repo reachable from two roots
                       # (e.g. a submodule listed both as its own root and as
                       # a nested repo under a parent root) is scanned once.

for root in "${roots[@]}"; do
  [ -d "$root" ] || continue
  while read -r gitdir; do
    repo="$(dirname "$gitdir")"
    [ -n "${SEEN_REPO[$repo]+x}" ] && continue
    SEEN_REPO[$repo]=1
    repos_scanned=$((repos_scanned + 1))

    # --branches spans EVERY local branch, not just HEAD. That is deliberate (work hides on
    # branches you are not standing on), and it is why the commits table is labeled all-branches.
    subjects="$(git -C "$repo" log --branches --since="$since" \
      --format='%h%x1f%s%x1f%cr' 2>/dev/null)"
    [ -z "$subjects" ] && continue

    branch="$(git -C "$repo" branch --show-current 2>/dev/null)"
    [ -z "$branch" ] && branch="(detached)"
    tree="clean"
    [ -n "$(git -C "$repo" status --short 2>/dev/null)" ] && tree="dirty"

    # --- Improved branch topology: detect squash merges, not just ancestry ---
    # Build list of branches, their trees, and the combined tree set of local + remote base.
    # base_branch is the short name the report speaks in; base_ref is a ref that
    # actually resolves in this repo. They differ for a clone whose only base is
    # a remote one — no local main, just origin/main — which used to leave
    # base_branch empty and silently disable every pass, so each of that repo's
    # branches reported "potentially outstanding" no matter what had landed.
    base_branch=""
    base_ref=""
    for cand in main master; do
      if git -C "$repo" show-ref -q --verify "refs/heads/$cand" 2>/dev/null; then
        base_branch="$cand"; base_ref="$cand"
        break
      fi
    done
    if [ -z "$base_branch" ]; then
      for cand in main master; do
        if git -C "$repo" show-ref -q --verify "refs/remotes/origin/$cand" 2>/dev/null; then
          base_branch="$cand"; base_ref="origin/$cand"
          break
        fi
      done
    fi
    if [ -z "$base_branch" ]; then
      # Last resort, and the only one that finds a base not called main/master.
      _oh="$(git -C "$repo" symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null)"
      if [ -n "$_oh" ]; then
        base_branch="${_oh#refs/remotes/origin/}"
        base_ref="origin/$base_branch"
      fi
    fi
    # Collect branch names and trees
    unset _btree_map _base_tree_set _merged _content
    declare -A _btree_map
    _branch_list=()
    while read -r _br; do
      [ -z "$_br" ] && continue
      _branch_list+=("$_br")
      _btree_map["$_br"]="$(git -C "$repo" rev-parse "$_br^{tree}" 2>/dev/null || echo "")"
    done < <(git -C "$repo" for-each-ref --format='%(refname:short)' refs/heads)

    # Build combined tree set from local base and its remote counterpart(s).
    # base_branch is now populated even for a clone whose only base is remote,
    # so refs/remotes/origin/$base_branch is a real ref in that case and this
    # set is not empty.
    declare -A _base_tree_set
    if [ -n "$base_branch" ]; then
      for _bref in "refs/heads/$base_branch" "refs/remotes/origin/$base_branch" "refs/remotes/origin/HEAD"; do
        if git -C "$repo" show-ref -q --verify "$_bref" 2>/dev/null; then
          # git log --format=%T for each ref, deduplicated via associative array
          while read -r _t; do
            [ -n "$_t" ] && _base_tree_set["$_t"]=1
          done < <(git -C "$repo" log "$_bref" --format=%T 2>/dev/null)
        fi
      done
    fi

    # First pass: direct merge detection (ancestry or tree match), using the
    # resolved base_ref so a clone whose only base is a remote one — no local
    # main, just origin/main — still detects branches that landed on it.
    declare -A _merged
    # Branches in _merged whose evidence is a coverage score rather than an
    # exact ancestry/tree fact. Tracked separately so the report can keep the
    # two apart instead of laundering a 97% guess into the same word as a
    # byte-identical tree match.
    declare -A _content
    for _b in "${_branch_list[@]}"; do
      [ "$_b" = "$base_branch" ] && continue
      # Ancestry: branch is an ancestor of the resolved base
      if [ -n "$base_ref" ] && git -C "$repo" merge-base --is-ancestor "$_b" "$base_ref" 2>/dev/null; then
        _merged["$_b"]="ancestry of $base_ref"
        continue
      fi
      # Tree match: branch tip tree exists in combined base history (squash merge)
      _t="${_btree_map[$_b]}"
      if [ -n "$_t" ] && [ -n "${_base_tree_set[$_t]-}" ]; then
        _merged["$_b"]="squash: tree $_t in $base_ref history"
      fi
    done

    # Transitive closure: if b is an ancestor of an already-merged c, b is
    # proved merged by one merge-base call. Running it BEFORE the content pass
    # means an ancestor of a squash-merged branch — which this closure catches
    # for free — never pays the pass's merge-tree plus two diffs. Confidence is
    # inherited, not upgraded: a branch proved merged only because its
    # descendant scored 96% is itself a scored guess. When a branch is an
    # ancestor of BOTH a proved branch and a scored one, the proved proof wins —
    # bash associative-array iteration is hash order, so "first match wins"
    # would otherwise let key hashes, not evidence quality, decide the label.
    _changed=1
    _iter=0
    while [ $_changed -eq 1 ] && [ $_iter -lt 20 ]; do
      _changed=0
      _iter=$((_iter + 1))
      for _b in "${_branch_list[@]}"; do
        [ "$_b" = "$base_branch" ] && continue
        [ -n "${_merged[$_b]-}" ] && continue
        _scored_ancestor=""
        for _c in "${!_merged[@]}"; do
          if git -C "$repo" merge-base --is-ancestor "$_b" "$_c" 2>/dev/null; then
            if [ -n "${_content[$_c]-}" ]; then
              # A scored ancestor is only a fallback; keep scanning for a proved one.
              [ -z "$_scored_ancestor" ] && _scored_ancestor="$_c"
            else
              # First proved ancestor wins — proved beats scored regardless of order.
              _merged["$_b"]="ancestor of $_c (${_merged[$_c]-})"
              _changed=1
              break
            fi
          fi
        done
        if [ -z "${_merged[$_b]-}" ] && [ -n "$_scored_ancestor" ]; then
          _merged["$_b"]="ancestor of $_scored_ancestor (${_merged[$_scored_ancestor]-})"
          _content["$_b"]=1
          _changed=1
        fi
      done
    done

    # Second pass: content coverage, for the squash shape neither pass above
    # can see. Once `main` advances between the branch point and the squash,
    # the squash commit's tree is `new-main + branch changes` while the branch
    # tip tree is `old-main + branch changes` — never equal, so the tree match
    # fails and ancestry never held in the first place. Scoring residual
    # content instead of tree identity is unaffected by that drift.
    #
    # The two passes above stay first: they are exact and nearly free, and this
    # one costs a merge-tree plus two diffs per branch, so only branches still
    # unmerged after both ever reach it.
    if [ "$content_scoring" = yes ] && [ -n "$base_ref" ]; then
      for _b in "${_branch_list[@]}"; do
        [ "$_b" = "$base_branch" ] && continue
        [ -n "${_merged[$_b]-}" ] && continue
        # UNSCORED/UNKNOWN verdicts fall through untouched: they mean "this
        # says nothing", not "outstanding", and the branch keeps whatever the
        # passes above concluded.
        _cs="$(coverage_score "$repo" "$base_ref" "$_b" 2>/dev/null)"
        case "$_cs" in
          "SCORED "[0-9]*)
            _pct="${_cs#SCORED }"
            if [ "$_pct" -ge "$CONTENT_MERGE_THRESHOLD" ]; then
              _merged["$_b"]="content: $_pct% of its lines already in $base_branch"
              _content["$_b"]=1
            fi
            ;;
        esac
      done
    fi

    # Transitive closure again, so a branch whose descendant the content pass
    # just scored is also caught, at the same one-merge-base cost per branch.
    # Same proved-beats-scored rule as the first closure: a branch that is an
    # ancestor of both a tree/ancestry-proved branch and a content-scored one is
    # proved, not guessed, regardless of associative-array iteration order.
    _changed=1
    _iter=0
    while [ $_changed -eq 1 ] && [ $_iter -lt 20 ]; do
      _changed=0
      _iter=$((_iter + 1))
      for _b in "${_branch_list[@]}"; do
        [ "$_b" = "$base_branch" ] && continue
        [ -n "${_merged[$_b]-}" ] && continue
        _scored_ancestor=""
        for _c in "${!_merged[@]}"; do
          if git -C "$repo" merge-base --is-ancestor "$_b" "$_c" 2>/dev/null; then
            if [ -n "${_content[$_c]-}" ]; then
              [ -z "$_scored_ancestor" ] && _scored_ancestor="$_c"
            else
              _merged["$_b"]="ancestor of $_c (${_merged[$_c]-})"
              _changed=1
              break
            fi
          fi
        done
        if [ -z "${_merged[$_b]-}" ] && [ -n "$_scored_ancestor" ]; then
          _merged["$_b"]="ancestor of $_scored_ancestor (${_merged[$_scored_ancestor]-})"
          _content["$_b"]=1
          _changed=1
        fi
      done
    done

    alerts=0
    branchlines=""
    while read -r b; do
      [ -z "$b" ] && continue
      [ "$b" = "$base_branch" ] && continue
      classification=""
      # Show every branch. A proved merge is context, not an alert, and neither
      # is a content-merged one — reporting it as outstanding is the exact
      # false positive this pass exists to remove. It keeps its own word
      # because the evidence is a score, not a fact, and because the reader's
      # next move differs: content-merged branches go to `archive-branch.sh
      # --strict`, never to the literal-ancestry deletion path.
      if [ -n "${_merged[$b]-}" ]; then
        if [ -n "${_content[$b]-}" ]; then
          classification="content-merged"
        else
          classification="merged"
        fi
        state="${_merged[$b]}"
      else
        classification="potentially outstanding"
        state=""
        up="$(git -C "$repo" for-each-ref --format='%(upstream:short)' "refs/heads/$b")"
        if [ -n "$up" ]; then
          counts="$(git -C "$repo" rev-list --left-right --count "$b...$up" 2>/dev/null || printf '0\t0')"
          ahead="$(printf '%s' "$counts" | cut -f1)"
          behind="$(printf '%s' "$counts" | cut -f2)"
          if [ "${ahead:-0}" -gt 0 ] && [ "${behind:-0}" -gt 0 ]; then
            state="DIVERGED from $up (+$ahead/-$behind); push rejected, merge first"
          elif [ "${ahead:-0}" -gt 0 ]; then
            state="unpushed: $ahead"
          elif [ "${behind:-0}" -gt 0 ]; then
            state="behind $up by $behind"
          fi
        else
          state="no upstream; exists only on this disk"
        fi

        if [ -n "$base_ref" ]; then
          nm="$(git -C "$repo" rev-list --count "$base_ref..$b" 2>/dev/null || echo 0)"
          if [ "${nm:-0}" -gt 0 ]; then
            state="${state:+$state; }not in $base_ref ancestry: $nm"
          fi
        fi

        alerts=$((alerts + 1))
      fi
      branchlines+="    $(toon_str "$b"),$(toon_str "$classification"),$(toon_str "$state")"$'\n'
    done < <(git -C "$repo" for-each-ref --format='%(refname:short)' refs/heads)

    # AXI §2/§9: cap the rows, keep the true total, and tell the caller how to see the rest.
    # An uncapped log of a busy repo is hundreds of rows of pure token cost.
    total_commits="$(printf '%s\n' "$subjects" | grep -c . || true)"
    commitlines=""
    while IFS=$'\x1f' read -r sha subj age; do
      [ -z "$sha" ] && continue
      commitlines+="    $sha,$(toon_str "$subj"),$(toon_str "$age")"$'\n'
    done < <(printf '%s\n' "$subjects" | head -n "$COMMIT_LIMIT")

    active+=("$repo")
    R_BRANCH["$repo"]="$branch"
    R_TREE["$repo"]="$tree"
    R_ALERTS["$repo"]="$alerts"
    R_BRANCHLINES["$repo"]="$branchlines"
    R_COMMITS["$repo"]="$commitlines"
    R_TOTAL["$repo"]="$total_commits"
  done < <(fd -t d -d 4 -H -E node_modules -E .cache '^\.git$' "$root" 2>/dev/null)
done

echo "bin: $self_display"
echo "description: Git repos with local commits in a window, with per-branch push and merge state"
echo "window: $(toon_str "$since")"

# AXI §5: state the zero with context, so the caller never re-runs to check.
if [ "${#active[@]}" -eq 0 ]; then
  echo "repos: 0 of $repos_scanned scanned repos have commits since $(toon_str "$since")"
  echo "help[1]:"
  echo "  Run \`scan-active.sh \"1 week ago\"\` to widen the window"
  exit 0
fi

echo "count: ${#active[@]} of $repos_scanned scanned repos active"
echo
echo "repos[${#active[@]}]{path,branch,tree,alerts}:"
for repo in "${active[@]}"; do
  echo "  $(toon_str "$repo"),$(toon_str "${R_BRANCH[$repo]}"),${R_TREE[$repo]},${R_ALERTS[$repo]}"
done

for repo in "${active[@]}"; do
  echo
  echo "repo: $(toon_str "$repo")"

  n_b="$(printf '%s' "${R_BRANCHLINES[$repo]}" | grep -c . || true)"
  if [ "${n_b:-0}" -eq 0 ]; then
    echo "  branches: 0 non-base branches"
  else
    echo "  branches[$n_b]{name,classification,detail}:"
    printf '%s' "${R_BRANCHLINES[$repo]}"
  fi

  n_c="$(printf '%s' "${R_COMMITS[$repo]}" | grep -c . || true)"
  # Named all-branches on purpose: attributing these to the checked-out branch is the mistake.
  echo "  commits_all_branches: $n_c of ${R_TOTAL[$repo]} in window"
  echo "  commits_all_branches[$n_c]{sha,subject,age}:"
  printf '%s' "${R_COMMITS[$repo]}"
  if [ "${R_TOTAL[$repo]}" -gt "$n_c" ]; then
    echo "  help[1]:"
    echo "    Run \`ORIENT_COMMIT_LIMIT=${R_TOTAL[$repo]} scan-active.sh\` to see all ${R_TOTAL[$repo]}"
  fi
done

echo
echo "help[3]:"
echo "  Read the branches table, not the branch field, before claiming a repo is pushed"
echo "  Treat content-merged as landed but scored, not proved: archive it with \`archive-branch.sh --strict\`, never \`clean-safe.sh\`"
echo "  Run \`git -C <path> rev-list --left-right --count main...origin/main\` to confirm a DIVERGED repo"
