# Research — jeeves Rust rewrite

Assembled 2026-09-01 from one ferry per question (`researching-the-codebase`), model `ollama/deepseek-v4-flash:0731 --variant max`. Reference copies under `ref/`; line cites against them unless a `bin/` path is given.

---

# Q1 — CLI surface and output contracts of every absorbed binary

**Confidence:** high (all claims file:line cited against reference copies)

# Q1 — CLI surface and output contracts of every absorbed binary

All files are scratch reference copies under `.superpowers/crispy/rust-rewrite/ref/` (per `README.md:1`: "Scratch reference copies of the absorbed scripts for research ferries. Deleted before the PR lands."). Line numbers below are relative to each script in that directory. "TOON" = the AXI token-on-outer-name report format used by the orient tooling.

---

## lib.sh — shared library (source, don't execute)

`lib.sh:1` `#!/usr/bin/env bash`, `lib.sh:2` "Shared helpers for the auditing-worktrees scripts. Source, don't execute."

Env vars read (all with defaults; see below for which scripts consume them):

- `WORKTREE_AUDIT_INFLIGHT_SECS` (default 7200 = 2h) — `lib.sh:11`
- `WORKTREE_AUDIT_ARCHAEOLOGY_SECS` (default 7776000 = 90 days) — `lib.sh:15`
- `WORKTREE_AUDIT_ARCHIVE_PREFIX` (default `archive`) — `lib.sh:17`

Functions (10), with the consumers that source lib.sh and use them:

| Function | Line | Behavior | Used by |
|---|---|---|---|
| `detect_base` | 19–27 | Echoes `main` if `refs/heads/main` exists, else `master`, else symbolic `refs/remotes/origin/HEAD` (stripped of `refs/remotes/origin/`), else empty string | audit-worktrees.sh:47, archive-branch.sh:46, clean-safe.sh:20 |
| `proc_start_ticks` | 32–47 | Echoes a start-time string, `missing` (procfs absence for that pid), or `unknown` (procfs unmounted or `/proc/<pid>/stat` unreadable). Probes `/proc/self` first — a missing procfs makes every pid look absent, which would turn live locks into deletions | lock_status |
| `worktree_gitdir` | 54–56 | Resolves a worktree's metadata dir via `git -C <wt> rev-parse --absolute-git-dir` (handles `.git/worktrees/<name>1` collisions that basename-derivation would misread) | audit-worktrees.sh:139, archive-branch.sh:79, clean-safe.sh:67 |
| `lock_status` | 64–77 | Echoes `live` \| `stale` \| `unknown`. Lock file must be a `pid N` + `start N` record (manual `git worktree lock --reason` = `unknown`, treated as live) | audit-worktrees.sh:141, archive-branch.sh:82, clean-safe.sh:70 |
| `worktree_path_for_branch` | 81–93 | Echoes worktree path for a branch from porcelain output, parsed line-by-line so spaces survive | archive-branch.sh:62, clean-safe.sh:42 |
| `worktree_dirty_count` | 96–100 | `git status --porcelain \| wc -l`; 0 for missing/absent worktree | audit-worktrees.sh:146, archive-branch.sh:91, clean-safe.sh:60 |
| `newest_change_mtime` | 105–113 | Newest mtime among modified/untracked/staged files only (checkout mtimes are not activity); echoes nothing for clean worktree | activity_age_secs |
| `activity_age_secs` | 123–133 | Seconds since max(last commit time, newest touched-file mtime). Both signals fail alone (observed live), max fixes both | audit-worktrees.sh:91, archive-branch.sh:72, clean-safe.sh:52 |
| `human_age` | 135–141 | `N`m / `N`h / `N`d by magnitude | audit-worktrees.sh (multiple), archive-branch.sh:74, clean-safe.sh:54 |
| `merge_base` | 149–156 | Singular merge base of two refs, or nothing; counts `--all` first and treats >1 base (criss-cross) as error | coverage_score |
| `numstat_net` | 164–174 | Sums net text lines from numstat; echoes `UNSCORED binary` for `- -` rows, `UNSCORED mode-only` for `0 0` rows, else the integer net | coverage_score |
| `coverage_score` | 180–248 | See three-state contract below | audit-worktrees.sh:111, scan-active.sh:308, coverage-score CLI |
| `validate_pct` | 254–268 | Echoes the integer or `invalid`; strict decimal (rejects leading zeros, "08" would be octal), rejects length >3 before arithmetic read (wraps mod 2^64), range 0–100 | audit-worktrees.sh:32, scan-active.sh:138 |

Shared-by breakdown: **audit-worktrees.sh** (sources at `audit-worktrees.sh:17`) uses detect_base, activity_age_secs, coverage_score, human_age, worktree_gitdir, lock_status, worktree_dirty_count, validate_pct. **archive-branch.sh** (`archive-branch.sh:26`) uses detect_base, worktree_path_for_branch, activity_age_secs, human_age, worktree_gitdir, lock_status, worktree_dirty_count. **clean-safe.sh** (`clean-safe.sh:11`) uses detect_base, worktree_path_for_branch, activity_age_secs, human_age, worktree_gitdir, lock_status, worktree_dirty_count. **coverage-score** (`coverage-score:24`) uses only coverage_score. **scan-active.sh** (`scan-active.sh:120`, best-effort) uses coverage_score + validate_pct.

`merge_base` preserves errexit state: it notes it must "Restore the caller's errexit rather than forcing it on" (`lib.sh:206-209`) — consumers run without `set -e`.

### The three-state coverage contract

`lib.sh:176-179` defines the exact verdict shapes:

```
# Echoes one of:
#   SCORED <0-100>   - the branch's net text lines that are already in base.
#   UNSCORED <why>   - binary/mode-only row, O==0, or empty patch.
#   UNKNOWN <why>    - criss-cross history, merge conflict, merge-tree error.
```

Concrete `UNSCORED <why>` strings: `UNSCORED binary`, `UNSCORED mode-only` (from `numstat_net`, `lib.sh:169-170`), `UNSCORED no-text-rows` (`lib.sh:195`). Concrete `UNKNOWN <why>` strings: `UNKNOWN no-merge-base` (`lib.sh:182-183`, both no base and merge_base rc≠0), `UNKNOWN branch-diff-failed` (`lib.sh:186`), `UNKNOWN no-temp-dir` (`lib.sh:202`), `UNKNOWN no-object-dir` (`lib.sh:204`), `UNKNOWN merge-conflict` (merge-tree rc 1, `lib.sh:230`), `UNKNOWN merge-tree-error` (merge-tree rc>1 or diff rc>1, `lib.sh:231`).

Contract subtleties, quoted:

- `lib.sh:188-191`: numstat_net's verdict is echoed verbatim — "Decorating it here (a suffix on one path, a second "UNSCORED " prefix on the other) produced three different strings for one verdict and broke the contract the coverage-score CLI publishes to its consumers."
- `lib.sh:216-221`: an empty merge-tree tree with rc 0 must not fall through — that "parses as R=0 and fails open to SCORED 100".
- `lib.sh:238-246`: R is a NET line count and goes negative when base lacks the branch's deletions; score uses `absR` and clamps to 0–100 ("a branch whose deletion never shipped scored SCORED 111 and cleared the 95% bar" before the fix).
- `lib.sh:244-247`: `num=$(( O - absR )); [ "$num" -lt 0 ] && num=0; pct=$(( num * 100 / O )); [ "$pct" -gt 100 ] && pct=100`.
- Threshold semantics: SCORED pct where pct ≥ threshold means content-merged (95 default). `UNSCORED`/`UNKNOWN` are "says nothing", not "outstanding" — `scan-active.sh:306-307` "UNSCORED/UNKNOWN verdicts fall through untouched: they mean "this says nothing", not "outstanding"".

---

## coverage-score (no extension, executable)

- **Usage**: `coverage-score <repo> <base> <branch>` — exactly 3 positional args (`coverage-score:9`, enforced at `coverage-score:56-59`). `<repo>` path to git repo; `<base>` ref name or commit; `<branch>` ref to score.
- **Flag handling**: `--help` prints usage, exit 0 (`coverage-score:43-46`). Any other arg starting with `-` → `error: unknown flag $arg` to stdout, exit 2 (`coverage-score:48-55`).
- **Errors**: wrong arg count → `error: usage: coverage-score <repo> <base> <branch>`, exit 2 (`coverage-score:56-59`). Non-directory repo → `error: not a directory: $1`, exit 2, "The cd's own error must not leak to stderr — this is a usage error reported on stdout" (`coverage-score:65-68`). `cd`s into the resolved absolute repo first (`coverage-score:69`).
- **Exit semantics** (`coverage-score:14-15`, `34-35`): "The verdict line is the output, not the exit code: an UNSCORED or UNKNOWN verdict is a successful run. Exit 2 on usage error." Exit 0 on every successful run including UNSCORED/UNKNOWN; only exit 2 for usage/flag errors.
- **Stdout format**: exactly one line, the three-state verdict (see contract above).
- **Env vars read**: none directly; inherits lib.sh's env defaults (`WORKTREE_AUDIT_*`).
- **Note**: sources lib.sh at `coverage-score:24`, so the whole lib must be present; `set -euo pipefail` (`coverage-score:21`).

---

## scan-active.sh

AXI-compliant report: TOON on stdout, structured errors on stdout, diagnostics on stderr (`scan-active.sh:3`). Runs with `set -uo pipefail` (no `-e`, `scan-active.sh:4`).

- **Usage**: `scan-active.sh <since> [root ...]` (`scan-active.sh:17`). `<since>` = any `git log --since` expression, e.g. "yesterday 00:00" (required). `[root]` = dirs to scan.
- **Flags**: only `--help` (exit 0). Any other `--*` → two-line error on stdout, exit 2: `error: unknown flag $arg for \`scan-active.sh\`` + `help: the only flag is --help; ...` (`scan-active.sh:49-58`).
- **Errors (all exit 2, on stdout)**: no args → `error: <since> is required` + help line (`scan-active.sh:60-64`). Missing `fd` on PATH → `error: fd not found on PATH` + help line, exit 2 (`scan-active.sh:71-75`); the preflight exists because without fd a failure "reads as '0 of 0 scanned repos' — indistinguishable from a genuinely quiet workspace, and wrong in exactly the direction that hides real work" (`scan-active.sh:66-70`).
- **Root resolution** (`scan-active.sh:80-94`): explicit `[root ...]` args win; else `$ORIENT_ROOTS_FILE` (default `${XDG_STATE_HOME:-$HOME/.local/state}/orient/roots.txt`) if non-empty, one path per line; else `$ORIENT_ROOTS` (space/colon separated); else `/workspace` (`scan-active.sh:88-92`).
- **Env vars** (usage text at `scan-active.sh:25-39`):
  - `ORIENT_ROOTS` — default root dirs when no roots file.
  - `ORIENT_ROOTS_FILE` — roots file path (default `${XDG_STATE_HOME:-$HOME/.local/state}/orient/roots.txt`).
  - `ORIENT_COMMIT_LIMIT` — commit rows per repo (default 15); "the true total is always reported" (`scan-active.sh:102`, `406`).
  - `AUDIT_WORKTREES_LIB` — path to auditing-worktrees' lib.sh (default `$HOME/.claude/skills/auditing-worktrees/bin/lib.sh`); absent lib → branches classified by ancestry/tree match alone (`scan-active.sh:110-120`). Set-but-unreadable → `warning: ...` on stderr, degrade, don't abort (`scan-active.sh:116-118`).
  - `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD` — coverage % for content-merged (default 95; must be integer 1-100, no leading zero). Set-but-invalid → stderr warning + fallback to 95 (`scan-active.sh:137-143`); unset → 95 without warning. "0 is rejected alongside the malformed values: it would score every branch as content-merged" (`scan-active.sh:135-137`).
  - `ORIENT_CONTENT_SCORING` — `0` skips the content-coverage pass entirely (default 1) (`scan-active.sh:38-39`, `129`).
- **Repo discovery**: `fd -t d -d 4 -H -E node_modules -E .cache '^\.git$' "$root"` per root (`scan-active.sh:420`). Dedup via `SEEN_REPO` so a repo under two roots is scanned once (`scan-active.sh:148-157`). Non-directory roots silently skipped (`scan-active.sh:154`).
- **Scan semantics** (`scan-active.sh:163-165`): `git log --branches --since="$since" --format='%h%x1f%s%x1f%cr'` — ALL local branches, not just HEAD ("that is deliberate (work hides on branches you are not standing on)", `scan-active.sh:161-162`). Repos with no commits in window are excluded from output entirely but counted in `repos_scanned`.
- **Exit codes**: 0 on every successful run including the zero-active case (`scan-active.sh:428-433`); 2 for usage/flag/fd-missing errors. No other nonzero exit path.
- **stdout format** — the record shape, exactly as emitted (`scan-active.sh:423-468`):

  ```
  bin: <self_display>            # $0 with $HOME collapsed to ~ (AXI §10, scan-active.sh:7-8)
  description: Git repos with local commits in a window, with per-branch push and merge state
  window: "<since>"              # toon_str-escaped
  ```

  Zero-active case (`scan-active.sh:429-432`):
  ```
  repos: 0 of N scanned repos have commits since "<since>"
  help[1]:
    Run `scan-active.sh "1 week ago"` to widen the window
  ```

  Otherwise (`scan-active.sh:435-463`):
  ```
  count: K of N scanned repos active
  <blank>
  repos[K]{path,branch,tree,alerts}:
    "<repo>","<branch>","<tree>",<alerts>          # per active repo
  <blank>
  repo: "<repo>"
    branches[N]{name,classification,detail}:
        "<name>","<classification>","<state>"      # per non-base branch
    commits_all_branches: C of T in window
    commits_all_branches[C]{sha,subject,age}:
        <sha>,"<subject>","<age>"                  # up to ORIENT_COMMIT_LIMIT rows
    help[1]:
      Run `ORIENT_COMMIT_LIMIT=T scan-active.sh` to see all T   # only if C < T
  <blank>
  help[3]:
    Read the branches table, not the branch field, before claiming a repo is pushed
    Treat content-merged as landed but scored, not proved: archive it with `archive-branch.sh --strict`, never `clean-safe.sh`
    Run `git -C <path> rev-list --left-right --count main...origin/main` to confirm a DIVERGED repo
  ```

  Field details:
  - Every string field is `toon_str`-escaped: quoted, backslashes doubled, embedded quotes escaped (`scan-active.sh:97-100`). `tree` and `alerts` are NOT quoted (bare `clean`/`dirty`, bare integer).
  - `branch` = current branch or `(detached)` (`scan-active.sh:167-168`).
  - `alerts` = count of non-base, non-merged branches ("potentially outstanding"); merged/content-merged branches are context, not alerts (`scan-active.sh:354-400`).
  - `classification` ∈ `merged` | `content-merged` | `potentially outstanding` (`scan-active.sh:366-374`). "content-merged" keeps its own word because the evidence is a score, not a fact, and its next move differs: `archive-branch.sh --strict`, never `clean-safe.sh` (`scan-active.sh:361-365`, restated in help[3]).
  - `state` strings: `DIVERGED from <up> (+a/-b); push rejected, merge first` / `unpushed: N` / `behind <up> by N` / `no upstream; exists only on this disk` (`scan-active.sh:381-390`), plus `; not in <base_ref> ancestry: N` (`scan-active.sh:392-397`). Merged branches: `ancestry of <ref>` | `squash: tree <oid> in <ref> history` | `content: N% of its lines already in <base>` | `ancestor of <b> (…)`.
  - `age` is `git log --format=%cr` output.
- **Content-scoring pass**: only branches still unmerged after ancestry + tree-match passes reach it (`scan-active.sh:229-250`, `301-319`); costs "a merge-tree plus two diffs per branch" (`scan-active.sh:301-303`). Transitive-closure passes run twice with proved-beats-scored precedence (`scan-active.sh:261-289`, `326-352`).
- **stderr**: diagnostics only — the `AUDIT_WORKTREES_LIB` warning (`scan-active.sh:117`) and the threshold warning (`scan-active.sh:140`); `fd` stderr is suppressed (`scan-active.sh:420`).

---

## git-state.sh

- **Usage**: `git-state.sh [dir]` — zero or one positional; defaults to `$(pwd)` (`git-state.sh:3`, `6`). No flags; any `--x` arg is treated as the dir and will fail the `cd`.
- **Env vars**: none.
- **Exit codes**: 0 on success including the non-git case (`git-state.sh:11-14`); 1 if `cd` fails — `error: cannot cd to $dir` on stdout (`git-state.sh:7`). `set -uo pipefail` (no `-e`, `git-state.sh:4`). No other failure paths.
- **stdout format** (git case, `git-state.sh:28-48`):

  ```
  repo: <toplevel absolute path>
  branch: <current>|(detached)
  tracking: ahead N / behind N (vs <upstream>)|no upstream
  last-commit-iso: <cI>|none
  last-commit-rel: <cr>|none
  recent-commits:
    %h %s (%cr)        # last 5
  dirty: yes|no
  status:              # only if dirty
    <git status --short, 2-space-indented, max 40 lines, color off>
  diffstat:            # only if dirty
    <git diff --stat, 2-space-indented, tail 20 lines>
  worktrees:
    <git worktree list, 2-space-indented>
  ```

  Non-git case (`git-state.sh:11-12`): `repo: none (not a git repository)` + `dir: <arg>`; exit 0.
  `tracking` uses `git rev-list --left-right --count "HEAD...$upstream"` with `0 0` fallback (`git-state.sh:22`); a missing upstream → `no upstream`. `cd "$root"` happens after `--show-toplevel` so all git calls run from the toplevel (`git-state.sh:15`).
- **Quirk**: `set --` inside the `if` overwrites the positional params (the script never reads `$@` again, so harmless).

---

## session-discover.sh

- **Usage**: `session-discover.sh [project-dir]` — zero or one positional, defaults to `$(pwd)` (`session-discover.sh:3`, `11`); dir resolved to absolute via `cd` (`session-discover.sh:12`).
- **Env vars**: `ORIENT_OPENCODE_SCAN` — max number of OpenCode sessions to scan, default 12 (`session-discover.sh:8`, `30`); `0` disables the OpenCode pass (`session-discover.sh:31`).
- **Exit codes**: always 0 in practice (`set -uo pipefail`, no failure paths; external commands are guarded).
- **stdout format**: KEY=VALUE lines, only for sources found (`session-discover.sh:4-6`):
  - `CLAUDE_JSONL=<path>` — newest `*.jsonl` in `$HOME/.claude/projects/<slug>` where slug = absolute dir path with non-alphanumerics → `-` (`session-discover.sh:14-20`); fallback: fuzzy-match a projects dir whose name ends with the path tail via `fd -t d -d 1 ".*${tail_slug}\$"` (`session-discover.sh:22-26`).
  - `OPENCODE_SESSION=<id>` — first (newest-first) `ses_[A-Za-z0-9]+` from `opencode session list` whose export's `.info.directory` equals the dir (`session-discover.sh:32-41`); reads only the first 800 bytes of each export (`session-discover.sh:34-36`); bounded by `timeout 30` / `timeout 20` per call (`session-discover.sh:32-34`).
- No stdout at all when neither source is found.

---

## session-tail.sh

- **Usage**: `session-tail.sh <jsonl> [since-iso] [max-entries]` (`session-tail.sh:4-6`). `<since-iso>` e.g. `2026-07-12T14:00:00Z`, `""` for no lower bound; `[max-entries]` default 40 (`session-tail.sh:11`).
- **Env vars**: none.
- **Exit codes**: missing arg → `usage: session-tail.sh <jsonl> [since-iso] [max]` to stderr (via `${1:?}`, `session-tail.sh:9`), exit 1. Nonexistent file → `error: no such file: $f`, exit 1 (`session-tail.sh:13`). No jq → `(jq unavailable; cannot parse session)` and exit 0 (`session-tail.sh:14`) — a quiet success, not an error. Otherwise exit = `tail`'s status; `set -uo pipefail` applies to the pipeline (`session-tail.sh:7`).
- **stdout format**: one line per entry (`session-tail.sh:16-26`):

  ```
  [<timestamp>] <role>: <text>
  ```

  - Filters: `.timestamp != null and .message.role != null`, entries at/after `$since` when given, `.x` (concatenated text-type content, or the stringified content) must be non-empty (`session-tail.sh:17-24`).
  - `text` is truncated to 800 chars: `.x[0:800]` (`session-tail.sh:25`).
  - Array content joins all `.text`-typed parts with spaces; non-array content is `tostring`'d (`session-tail.sh:20-23`).
  - Piped through `tail -n "$max"` so the output is the LAST max entries within the (possibly since-filtered) stream (`session-tail.sh:26`).
  - jq stderr suppressed with `2>/dev/null` (`session-tail.sh:26`).

---

## discover-roots.sh

- **Usage**: `discover-roots.sh [root ...]` — zero or more positional dirs (`discover-roots.sh:24`). None given → `$ORIENT_ROOT_CANDIDATES` (space/colon separated), else `/workspace $HOME/.claude $HOME/.dotfiles` (`discover-roots.sh:47-51`). Non-directory roots silently skipped (`discover-roots.sh:86`).
- **Flags**: `--help` → usage, exit 0; any other `--*` → `error: unknown flag $arg`, exit 2 (`discover-roots.sh:40-45`).
- **Env vars**: `ORIENT_ROOT_CANDIDATES` (default root dirs); `ORIENT_ROOTS_FILE` (persist path, default `${XDG_STATE_HOME:-$HOME/.local/state}/orient/roots.txt`) (`discover-roots.sh:33-37`, `53`).
- **Exit codes**: 0 on success (no other failure paths; `set -uo pipefail`, `discover-roots.sh:14`).
- **Side effect**: writes `ROOTS_FILE` — `mkdir -p $(dirname)` then `printf '%s\n' "${SEEN_URL[@]}" | sort > "$ROOTS_FILE"` (`discover-roots.sh:109-110`) — this is the file scan-active.sh reads by default. This is the only script in the set that persists state.
- **Dedup algorithm** (`discover-roots.sh:57-107`): normalize remote URL (strip `https://`/`http://`/`ssh://`/`git@`, `:` → `/`, strip `.git`), one canonical path per distinct origin; prefer primary checkout (`.git` is a directory, `is_primary` at `discover-roots.sh:72-74`) over worktrees (`.git` file), tie-break by newest commit timestamp (`discover-roots.sh:100-105`). Repos with no origin URL are skipped (`discover-roots.sh:89-90`).
- **stdout format** (`discover-roots.sh:112-118`):

  ```
  bin: <self_display>          # $0 with $HOME → ~ (discover-roots.sh:16-17)
  description: Canonical git repo roots, deduplicated by remote URL
  roots_file: <path>
  count: N distinct remotes
  <blank>
  roots[N]{path}:
    <path>                     # sorted, 2-space indented
  ```

---

## lint-checkin.py

- **Usage**: `lint-checkin.py [file]` — zero or one positional; with none, reads markdown from stdin (`lint-checkin.py:46-50`). No flag handling; any `-x` arg is treated as a filename and will fail the open.
- **Env vars**: none. Pure Python 3 (stdlib only: `re`, `sys`).
- **Exit codes**: 0 when every bullet passes, 1 when any bullet fails (`lint-checkin.py:12`, `61-63`).
- **Rules** (only bullet lines — `^\s*[-*]\s+` — are checked, `lint-checkin.py:29`, `53-56`):
  - ≤ 120 chars per bullet line (`MAX_CHARS`, `lint-checkin.py:25`)
  - ≤ 2 commas per bullet (`MAX_COMMAS`, `lint-checkin.py:26`)
  - ≤ 1 bold span (`**` pairs / 2, `MAX_BOLD`, `lint-checkin.py:27`, `40`)
- **stdout format**: one line per violation, then a summary:
  - `line <N>: <problem>: <full line>` (`lint-checkin.py:58`)
  - problems: `too long (<len> > 120 chars)` | `too many commas (<n> > 2)` | `more than one bold span` (`lint-checkin.py:37-41`)
  - final line: `N violation(s) found.` on failure; `OK: all bullets pass.` on success (`lint-checkin.py:61-64`)
- **Failure detail**: a single line can accumulate multiple problem messages, all printed; `failures` counts lines, not problems (`lint-checkin.py:57-59`).

---

## audit-worktrees.sh

- **Usage**: `audit-worktrees.sh [--no-content] [root-dir]` (`audit-worktrees.sh:5`). `--no-content` accepted in ANY argument position (`audit-worktrees.sh:19-26` — "a flag silently swallowed as the root dir runs the expensive pass a caller explicitly asked to skip"). `root-dir` defaults to `$(pwd)`; a single repo path audits just that repo ("cheap enough for a SessionStart hook", `audit-worktrees.sh:7`).
- **Env vars**: `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD` (default 95; invalid or `0` → stderr note `(skipped: ...)` and fallback to 95, `audit-worktrees.sh:29-40`). Also the lib.sh envs: `WORKTREE_AUDIT_INFLIGHT_SECS` (7200), `WORKTREE_AUDIT_ARCHAEOLOGY_SECS` (7776000), `WORKTREE_AUDIT_ARCHIVE_PREFIX` (`audit-worktrees.sh:17` sources lib).
- **Exit codes**: `set -euo pipefail` (`audit-worktrees.sh:14`); no explicit exit codes — 0 on a full sweep, nonzero only if an unexpected git/fd error kills the script. Unknown flags are NOT rejected (any non-`--no-content` arg is treated as the root dir).
- **Report buckets** (`audit-worktrees.sh:207-239`), per repo with drift, under `=== <repo> (base: <base>) ===`:
  - `  safe-to-clean (merged, clean):` — merged into base, no uncommitted files
  - `  needs-triage:` — unmerged (or merged-but-dirty), unscored/unknown coverage, below-threshold SCORED
  - `  archaeology (older than <age>, never pushed — batch archive):` — unmerged, idle ≥ ARCHAEOLOGY_SECS, no upstream
  - `  likely-content-merged (work already in base under a different hash — batch archive):` — SCORED ≥ threshold
  - `  hands-off (live or unrecognized lock — never touch):` — lock status live or unknown
  - `  couldn't determine merge state (unrelated history / bad ref):` — merge-base rc > 1
  - `  dangling worktree registrations (git worktree prune is safe):` — prunable entries
  - `  (N in flight, active within <age> — hidden)` — count line only
  - Each branch line: `<branch>  (worktree: <path>)  [idle <age>]` plus bucket-specific reasons: `— merged, but N uncommitted file(s) would be lost`, `— content-merged (work already in base, different hash)[, N uncommitted file(s)]`, `— <reason>` where reason ∈ `unmerged[, SCORED <pct>]` / `unmerged, stale lock (dead session)` / `, upstream deleted` / `, N uncommitted file(s)` / `, UNSCORED` / `, UNKNOWN` (from `audit-worktrees.sh:149-182`).
- **Silence contract**: a repo whose only branches are in flight, or with nothing actionable and nothing dangling, prints NOTHING — not even a header (`audit-worktrees.sh:196-205`). The script never modifies anything (`audit-worktrees.sh:12`).
- **Discovery**: `fd -H -t d '^\.git$' "$ROOT" --max-depth 2` — repos one level under the root (`audit-worktrees.sh:242-249`); each repo is audited in a subshell (`audit-worktrees.sh:249`); relative root paths are resolved to absolute first (the coverage_score `git -C` would re-resolve relative paths against the repo and fail "silently turned every branch UNKNOWN whenever the root was relative", `audit-worktrees.sh:244-248`).
- **Ordering of checks** per branch: in-flight (activity_age < IN_FLIGHT_SECS) wins over everything (`audit-worktrees.sh:87-95`); unknown merge-base (>1 rc) flagged; content scoring only for definitively-unmerged branches (`audit-worktrees.sh:109`); SCORED below threshold → `reason="$reason, SCORED $pct"` so triage distinguishes "genuinely outstanding" from "might have landed" (`audit-worktrees.sh:119-123`); UNSCORED/UNKNOWN → triage, NEVER archaeology (`audit-worktrees.sh:126-131`, `175-177`).

---

## archive-branch.sh

- **Usage** (`archive-branch.sh:5-10`):
  - `archive-branch.sh <repo-path> <branch> [<branch>...]`
  - `archive-branch.sh --list <repo-path>`
  - `archive-branch.sh --strict <repo-path> <branch> [<branch>...]`
- **Modes**: `--list` (must be $1) prints archived tags and exits 0 (`archive-branch.sh:28-34`): `git for-each-ref --sort=-creatordate --format='%(refname:short)  %(objectname:short)  %(creatordate:short)' "refs/tags/$ARCHIVE_PREFIX"` — i.e. `archive/<name>  <abbrev-oid>  <date>` lines. `--strict` (must be $1, shifted) refuses dirty worktrees and creates the tag via a verified `update-ref --stdin` transaction (`archive-branch.sh:9-10`, `113-124`).
- **Env vars**: lib.sh envs only — `WORKTREE_AUDIT_INFLIGHT_SECS`, `WORKTREE_AUDIT_ARCHIVE_PREFIX` (both via `archive-branch.sh:26` source).
- **Exit codes**: `set -euo pipefail` (`archive-branch.sh:23`); 0 when every branch archived; 1 when ANY branch was refused (`rc=1` accumulated per refusal, final `exit "$rc"` at `archive-branch.sh:162`). Usage errors (missing repo/branch args) exit 1 with usage on stderr (`archive-branch.sh:39-40`); `--list` without repo exits 1 via `${2:?}` (`archive-branch.sh:29`). The script always runs `git worktree prune` before exiting (`archive-branch.sh:161`).
- **Per-branch refusal reasons** (stderr, rc=1, loop continues): `refusing <b>: this is the base branch` (55-57); `skip <b>: no such local branch` (58-60, NOT a refusal — rc unchanged); `refusing <b>: checked out in the primary worktree (<path>)` (65-69); `refusing <b>: active <age> ago, under the <threshold> in-flight threshold` (72-76); `refusing <b>: worktree lock is <status>, not provably dead` (82-85); `refusing <b>: strict mode does not archive a dirty worktree (N uncommitted file(s) in <path>)` (93-96); `refusing <b>: couldn't commit its uncommitted changes — not deleting anything` (100-102); `refusing <b>: tag <tag> already exists` (107-109); `refusing <b>: branch moved during archive (atomic tag aborted) — not deleting` (121-124); `refusing <b>: tag <tag> doesn't resolve to <tip> — not deleting` (127-130); `refusing <b>: branch advanced after tagging (...)` (135-138); `refusing <b>: branch advanced during archive (conditional delete aborted) — tag <tag> kept, branch left in place` (152-156).
- **Non-strict mode**: commits dirty worktree first — `  <branch>: committing N uncommitted file(s) before archiving` (stderr/stdout mix; `echo` without `>&2` goes to stdout, `archive-branch.sh:97`) then `git add -A` + `commit -q -m "wip: archived with uncommitted changes"` (98-99). Non-strict tag created with plain `git tag` then verified to resolve to `tip` (126-131).
- **Success line**: `  archived <branch> -> <tag> (<abbrev-oid>)` — note: this is the only message NOT redirected to stderr (`archive-branch.sh:158`).
- **Safety invariants**: tag is created BEFORE any destruction, so commits are always reachable afterwards (rationale at `archive-branch.sh:12-21`); worktree removal happens before the conditional branch delete; the delete uses `update-ref --stdin` with expected old-value so a commit landing mid-archive leaves the tag and refuses (`archive-branch.sh:145-156`).

---

## clean-safe.sh

- **Usage**: `clean-safe.sh <repo-path> <branch-name> [<branch-name>...]` (`clean-safe.sh:6`). No flags; no `--list`/`--strict`.
- **Env vars**: lib.sh envs via `clean-safe.sh:11` — `WORKTREE_AUDIT_INFLIGHT_SECS` (7200) and `WORKTREE_AUDIT_ARCHIVE_PREFIX` is not used here; only INFLIGHT + lib internals. `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD` is NOT read (no content pass).
- **Exit codes**: `set -euo pipefail` (`clean-safe.sh:8`); 0 when all branches deleted; 1 when any refused (`rc` accumulated, `exit "$rc"` at `clean-safe.sh:93`); usage error (no branches) → usage on stderr, exit 1 (`clean-safe.sh:13-14`). Always `git worktree prune` at the end (`clean-safe.sh:92`).
- **Re-verification**: "Re-verifies every branch itself right before deleting; never trusts a stale report" (`clean-safe.sh:3-4`). Checks, in order, per branch:
  - base branch → `refusing <b>: this is the base branch` (27-29)
  - nonexistent → `skip <b>: no such local branch` (31-33, rc unchanged)
  - `git merge-base --is-ancestor <b> <BASE>` rc≠0 → `refusing <b>: not confirmed merged into <BASE> (re-check gave exit N) — do not delete` (35-40)
  - primary worktree checkout → `refusing <b>: checked out in the primary worktree (<path>)` (44-47)
  - idle < IN_FLIGHT_SECS → `refusing <b>: active <age> ago, under the <threshold> in-flight threshold` (52-56)
  - dirty worktree → `refusing <b>: N uncommitted file(s) in <path> — merged, but these are in no commit and would be lost` (60-64)
  - lock not provably stale → `refusing <b>: worktree lock is <status>, not provably dead — do not delete` (70-73); stale lock is unlocked first (74)
  - `git branch -d` decline → `refusing <b>: git branch -d declined (see message above) — worktree removed, branch ref left for manual review` (86-89). Uses `-d` not `-D` deliberately: refuses if the upstream tracking ref has commits not on this branch — "a real 'someone pushed more since you last pulled' signal, not a bug to force past" (79-85); the worktree is already removed at that point.
- **stdout/stderr**: all messages go to stderr except none — the script emits no stdout payload; per-branch `refusing`/`skip` lines are `>&2`. Deletion success has no echo.
- **Order**: worktree removal (76-77) precedes `git branch -d` (86).

---

## session-hook.sh

- **Purpose**: SessionStart hook. REPORT ONLY — "It never archives, deletes, commits, or moves anything. A hook that mutated git state on session start would be a worse problem than the drift it reports." (`session-hook.sh:8-10`).
- **Usage**: no arguments. Reads a JSON hook payload from stdin and prefers its `.cwd` over the process's (`session-hook.sh:33-35`). Requires `audit-worktrees.sh` next to itself (executable check at `session-hook.sh:24-26`), `git`, and `jq` on PATH (29-30).
- **Env vars**: `WORKTREE_AUDIT_HOOK_TIMEOUT` (seconds, default 15) (`session-hook.sh:37`). Otherwise the audit's envs apply.
- **Exit codes**: always 0 — "Exits silently (no output, rc 0) when there's nothing to say, when git or the audit script is unavailable, or on any error. A broken hook must never block a session from starting." (`session-hook.sh:18-20`, `48-54`).
- **Two modes** (`session-hook.sh:39-44`): in a repo → full report for that repo; above one → one line per repo with counts ("The summary matters: dumping every stranded branch at every session start rebuilds the same wall of noise", `session-hook.sh:15-16`).
- **stdout format**: single JSON object (via `jq -n`), `{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:<string>}}`.
  - Timeout (rc 124): `additionalContext` = "Worktree audit exceeded its ${T}s budget in <basename> and was skipped. Run the auditing-worktrees skill manually for the full report." (`session-hook.sh:48-52`).
  - Repo mode (`session-hook.sh:56-60`): "Worktree drift in <basename> (from the auditing-worktrees SessionStart hook, report only):\n\n<audit output>\nNothing here has been changed. To act on it, use the auditing-worktrees skill."
  - Sweep mode (`session-hook.sh:61-68`): "Worktree drift across <root> (from the auditing-worktrees SessionStart hook, report only):\n\n<summary>\nNothing here has been changed. Use the auditing-worktrees skill for detail or to act on it."
  - No output at all (not even the JSON) when the audit produces nothing or the summary is empty (`session-hook.sh:54`, `64`).
- The hook always invokes the audit with `--no-content` (`session-hook.sh:46`).

---

## summary-parser.sh

- **Purpose**: source-only helper (`summary-parser.sh:1-2` "Sources, don't execute"); single function `summary_parse "$report"` returns the collapsed report via command substitution. Used ONLY by session-hook.sh (`session-hook.sh:28` sources it, `63` calls it).
- **Usage**: `summary=$(summary_parse "$out")` (`summary-parser.sh:7`).
- **Input contract**: the stdout of audit-worktrees.sh (multi-repo `=== <repo> ... ===` sections with `  <bucket-name>:` headings and `    <detail>` lines).
- **Output format**: one line per repo with non-zero buckets, bucket order fixed: triage, content-merged, unknown-merge, archaeology, safe-to-clean, hands-off (`summary-parser.sh:27-32`):

  ```
    <repo>: <N> triage, <N> content-merged, <N> unknown-merge, <N> archaeology, <N> safe-to-clean, <N> hands-off
  ```

  Repos with no tallied buckets are emitted with no counts line... (actually: `emit()` prints nothing when `parts` is empty, so the repo is omitted entirely). Zero-count buckets are omitted from the list; trailing comma stripped (`summary-parser.sh:33`).
- **Matching rules**: bucket headings matched structurally — `^  <name>[^:]*:` — so a branch description mentioning a bucket name can never set the current bucket (`summary-parser.sh:3-5`, `12-17`). The dangling-worktree heading (and any other two-space heading) clears the current bucket without counting (`summary-parser.sh:18-22` — "an unmatched heading used to leave the previous bucket active, so its indented lines were tallied under whichever bucket happened to precede it"). The `  (N in flight...)` line also clears the bucket (`summary-parser.sh:23`). Only lines starting with 4 spaces are tallied (`summary-parser.sh:24`).
- **Env vars**: none. No exit-code contract of its own (function, `awk` rc).
- **Note**: `repo=$2` on the `=== ` line assumes the repo path has no spaces (`summary-parser.sh:11`) — paths with spaces would be truncated to the second field.

---

## Cross-cutting summary

- **Exit-code family**: `0` = success, `1` = refusal/verification-failure (archive-branch, clean-safe, lint-checkin, git-state cd failure, session-tail file missing), `2` = usage/flag error (coverage-score, scan-active, discover-roots). audit-worktrees and session-hook: 0-only-by-design (hook must never block a session). session-discover: always 0.
- **The three-state contract** (`SCORED <0-100>` / `UNSCORED <why>` / `UNKNOWN <why>`) is published by lib.sh:180-248, the coverage-score CLI, and consumed by audit-worktrees.sh:111-134 and scan-active.sh:308-317. Both consumers treat UNSCORED/UNKNOWN as "says nothing" — never "outstanding", never "safe".
- **The scan-active record shape** is `repos[K]{path,branch,tree,alerts}` + per-repo `branches[N]{name,classification,detail}` and `commits_all_branches[C]{sha,subject,age}` tables, with quoted/escaped string fields (`toon_str`), and `help[n]:` lines with exact remediation commands.
- **Shared lib functions**: detect_base, activity_age_secs, human_age, worktree_gitdir, lock_status, worktree_dirty_count, worktree_path_for_branch shared by audit-worktrees/archive-branch/clean-safe; coverage_score shared by audit-worktrees + scan-active + coverage-score CLI; validate_pct by audit-worktrees + scan-active.

---

# Q2 — How bin/collect.py consumes scan-active.sh and discover-roots.sh

**Confidence:** high (cited against live bin/*.py)

# Q2 — How `bin/collect.py` consumes scan-active.sh and discover-roots.sh

All consumption happens in one function, `git_state()` in `bin/collect.py:215-252`, called from `run_once()` at `bin/collect.py:928` only when the run extracted anything (`if counts["extracted"]:` at `bin/collect.py:925`). The result feeds the synthesis ferry as the `{git_state_block}` prompt slot (`bin/collect.py:495-509`).

## Exact invocation (argv)

Both scripts are invoked via `bash` explicitly, with `capture_output=True, text=True` and a 180 s timeout. Paths are hardcoded under the user's home:

```python
bin/collect.py:219:    scan = Path.home() / ".claude/skills/orient/bin/scan-active.sh"
bin/collect.py:220:    discover = Path.home() / ".claude/skills/orient/bin/discover-roots.sh"
bin/collect.py:221:    since = os.environ.get("SINCE", "yesterday 00:00")
```

- **discover-roots.sh** — no arguments:
```python
bin/collect.py:232:                subprocess.run(["bash", str(discover)],
bin/collect.py:233:                               capture_output=True, text=True, timeout=180)
```
- **scan-active.sh** — one positional arg, the `--since` window, from the `SINCE` env var defaulting to `"yesterday 00:00"`:
```python
bin/collect.py:237:            p = subprocess.run(["bash", str(scan), since],
bin/collect.py:238:                               capture_output=True, text=True, timeout=180)
```
So the exact argv is `bash ~/.claude/skills/orient/bin/scan-active.sh "yesterday 00:00"` (or whatever `SINCE` holds). The test pins this: `assert calls[1] == ["bash", str(scan), "yesterday 00:00"]` (`tests/test_collect.py:516`).

## How stdout is parsed

**discover-roots.sh's stdout is not parsed at all** — it is captured and discarded; only the side effect matters (it rewrites the roots file `$XDG_STATE_HOME/orient/roots.txt`, per `ref/discover-roots.sh:53,109-110`).

**scan-active.sh's stdout is not structurally parsed either** — it is treated as an opaque text blob, not split on delimiters or fields. The only "parse" is a success/failure probe on the first token:

```python
bin/collect.py:239:            out = (p.stdout.strip()
bin/collect.py:240:                    or f"(git scan exited {p.returncode} with no output)")
...
bin/collect.py:247:    if not out.startswith("(git scan"):
bin/collect.py:248:        stamp = jl.now_et().isoformat(timespec="seconds")
bin/collect.py:249:        out = (f"(git scan taken {stamp} ET; commit ages below are "
bin/collect.py:250:                f"relative to this moment)\n{out}")
```

If stdout is empty, the returncode is reported; if the output doesn't start with the `(git scan ...` failure sentinel, a timestamp header is prepended. The TOON-shaped body (the `repos[N]{path,branch,tree,alerts}:` table, per-repo `branches[N]{name,classification,detail}:` and `commits_all_branches[N]{sha,subject,age}:` rows, per `ref/scan-active.sh:435-463`) is passed through verbatim for the synthesis model to read — collect.py never tokenizes it. The digest treats it as ground truth for shipped work (`SKILL.md:190-201`).

## The roots-refresh call added recently (#25)

Commit `37d67c5` ("feat(collect): refresh discovered roots before the git scan (#25)") added the discover-roots.sh call *before* the scan, inside the `if scan.exists():` guard:

```python
bin/collect.py:230:        if discover.exists():
bin/collect.py:231:            try:
bin/collect.py:232:                subprocess.run(["bash", str(discover)],
bin/collect.py:233:                               capture_output=True, text=True, timeout=180)
bin/collect.py:234:            except (subprocess.TimeoutExpired, OSError) as e:
bin/collect.py:235:                jl.log(f"root discovery failed: {e}")
```

It is best-effort: a missing discover-roots.sh or a failed run leaves the previous roots file in place, and scan-active.sh falls back to `ORIENT_ROOTS`/`/workspace` when the file is absent or empty (comment at `bin/collect.py:224-229`; the fallback itself is `ref/scan-active.sh:88-93`). The point is that the scan then deduplicates clones of the same remote, so a stale clone doesn't report its own commits as shipped work. Ordering is asserted by `tests/test_collect.py:492-539` (`calls[0] == ["bash", str(discover)]`, `calls[1] == ["bash", str(scan), "yesterday 00:00"]`; with discover-roots.sh absent, only the scan runs).

## State dirs/files collect.py writes for the digest, and their format

`state_dir()` is `$JEEVES_STATE_DIR` or `$XDG_STATE_HOME/jeeves` (default `~/.local/state/jeeves`), created 0700 (`bin/jeeves_lib.py:44-51`). Files written by collect.py:

| File | Format | Where |
|---|---|---|
| `git-state.md` | timestamp header line + raw scan-active.sh output, `+ "\n"` | `bin/collect.py:251` |
| `digests/<ET-date>.md` | the digest markdown from the synthesis reply (fenced `# jeeves digest` block, `bin/collect.py:530-547`) | `bin/collect.py:974-976` |
| `summaries/<date>/<slug>--<sid>--<n>.md` | `json.dumps(payload, indent=1)` per session | `bin/collect.py:488-492` |
| `offsets.tsv` | TSV, one row per transcript: `path\t<offset>\t<size>`; atomic via `.tmp` + `replace` | `bin/collect.py:22-38` |
| `project-dirs.txt` | one project dir per line (union of session cwds) | `bin/collect.py:919-922` |
| `tf-state.json` | `{"lines": [<stripped taskferry lines>]}` for the taskferry diff | `bin/collect.py:206-211` |
| `synthesis-raw/<date>--<stamp>--<tag>.txt` | raw rejected synthesis replies (keeps last 20) | `bin/collect.py:559-572` |
| `staging/<date>--<HHMMSS>--<gi>/` | per-run staging: raw redacted slices `<slug>--<sid>.jsonl` + ferry-written `summary-<sid>.json`; deleted after the run | `bin/collect.py:395-404, 765-786` |
| `collect.lock` | flock file (non-blocking, skip if held) | `bin/collect.py:1032-1038` |

The digest write itself:

```python
bin/collect.py:974:                d = jl.state_dir() / "digests"
bin/collect.py:975:                d.mkdir(parents=True, exist_ok=True)
bin/collect.py:976:                (d / f"{date}.md").write_text(digest_md)
```

## How tail.py / todos.py / jeeves_lib.py read that state

- **tail.py** does not read the digest state; it re-reads transcripts directly via `jl.read_delta`/`denoise`/`render_slice` (`bin/tail.py:38-41`) and reads the config for `truncate` (`bin/tail.py:39`). The wake flow is told to reuse the offset recorded in `offsets.tsv` for continuity (`SKILL.md:91-93`), but tail.py itself takes `--offset` as an argv int (`bin/tail.py:27`).
- **todos.py** reads the state collect.py's mutation path writes: the ledger `todo.md` in `data_dir()` (`$JEEVES_DATA_DIR` or `$XDG_DATA_HOME/jeeves`, default `~/.local/share/jeeves/todo.md`, `bin/todos.py:64-68`), the pending queue `state_dir()/pending.json` (`bin/todos.py:613-625`), the `SeenStore` `state_dir()/seen.ndjson` (`bin/jeeves_lib.py:241`), the import-hash log `state_dir()/imports.ndjson` (`bin/todos.py:994`), the evidence memo `state_dir()/evidence_memo.json` (`bin/todos.py:506, 510-531`), and `state_dir()/last_wake` (`bin/todos.py:910-917`). All of these except `last_wake` are written by collect.py's `apply_mutations`/`reconcile`/`ingest_repo_todos` path (`bin/collect.py:923, 926, 989`).
- **jeeves_lib.py** defines the dirs and the shared readers: `state_dir()`/`data_dir()` (`bin/jeeves_lib.py:44-60`), `load_config()` reading `state_dir()/config` as `key = value` lines (`bin/jeeves_lib.py:67-77`), `log()` appending to `state_dir()/collect.log` (`bin/jeeves_lib.py:80-83`), and `SeenStore.load()` reading `state_dir()/seen.ndjson` — one JSON object per line, keyed by `hash` (`bin/jeeves_lib.py:229-269`).

## Notes for the Rust rewrite

- The scan output is a *blob* contract, not a record contract: collect.py only needs "non-empty, not starting with the failure sentinel" — the TOON tables are consumed by the model, not the code. A Rust port can keep the same blob semantics or parse the TOON rows, but nothing in collect.py depends on the delimiter/field shape.
- The only hard coupling to the scripts' internals is the roots-file handoff: discover-roots.sh writes `$XDG_STATE_HOME/orient/roots.txt` and scan-active.sh reads it (or `ORIENT_ROOTS`/`/workspace`) — collect.py never touches that file itself.
- `SINCE` env var is the only knob collect.py passes through to the scan.

---

# Q3 — Cron installation and PATH contract

**Confidence:** high for code; live crontab unreadable in sandbox (code-derived only)

# Q3 — Cron installation and PATH contract

## The cron line install-cron.py writes

The entry is built by `_entry` in bin/install-cron.py:56-59:

```
56: def _entry(collect_path: Path) -> str:
57:     logf = jl.state_dir() / "collect.log"
58:     return (f"13 * * * * PATH={_cron_path()} /usr/bin/env python3 "
59:             f"{collect_path} >> {logf} 2>&1")
```

- **Schedule**: `13 * * * *` — every hour at 13 minutes past the hour (a deliberate skew off the hour boundary; collect.py:521-527 notes the run takes a non-blocking `flock` and skips if the previous run is still going).
- **Command**: `PATH=<built path> /usr/bin/env python3 /workspace/jeeves-rust-q3/bin/collect.py >> <state_dir>/collect.log 2>&1`. The system `/usr/bin/env python3` is used as the interpreter (PATH env var is set **inline in the crontab line**, not in the command itself), and stdout/stderr are appended to `collect.log` under the state dir (`~/.local/state/jeeves/collect.log` via `state_dir()` at bin/jeeves_lib.py:44-51). The collect.py path is resolved as `Path(__file__).resolve().parent / "collect.py"` at bin/install-cron.py:11.
- The entry is wrapped in `# BEGIN jeeves` / `# END jeeves` markers (bin/install-cron.py:10, 63) and `install()` is idempotent: it strips any existing jeeves block first (bin/install-cron.py:62-65).

## PATH contract

`_cron_path` (bin/install-cron.py:27-53) builds the inline PATH:

```
49:     for d in (str(Path.home() / ".local" / "bin"), str(_shims_dir()),
50:               "/usr/local/bin", "/usr/bin", "/bin"):
```

Order: `~/.local/bin` → mise shims dir → `/usr/local/bin` → `/usr/bin` → `/bin`. The directories the installed command relies on being on PATH:

- **~/.local/bin** (first): carries non-mise user binaries — `taskferry`, `gh-axi`, and `mise` itself (the shims' symlink target). Docstring at bin/install-cron.py:42-44.
- **mise shims dir** (`_shims_dir`, bin/install-cron.py:14-24): honors `JEEVES_MISE_SHIMS` override, else `MISE_DATA_DIR` or `$XDG_DATA_HOME/mise/shims` (defaulting `XDG_DATA_HOME` to `~/.local/share`). `fd`, `gh`, and `python3` resolve through the shims to whatever version is current (bin/install-cron.py:40-42). `fd` matters because scan-active.sh does all repo discovery through it — a PATH without it reports every workspace as empty (bin/install-cron.py:46-47).
- **/usr/{local/,}bin and /bin**: fallback floor for git, bash, and system python3 (bin/install-cron.py:44-45).

## Stable-dirs fix

bin/install-cron.py:27-47 documents why only *stable directories* may be on the cron PATH: resolving each tool through `shutil.which` at install time would bake the mise shim's resolved **versioned** install dir (e.g. `.../installs/fd/latest/fd-v10.4.2-.../`) into the crontab, and the next `mise upgrade` deletes that directory out from under cron. That is how the git-state scan silently reported `fd not found on PATH` for days after fd moved 10.4.2 → 10.5.0, and gh's versioned dir sat stale in the same entry. The fix: list only stable directories that survive tool upgrades untouched — shims dir + `~/.local/bin` + system bin dirs — so resolution through the shims is version-agnostic forever (bin/install-cron.py:30-41).

## Live crontab

Not readable in this sandbox: `crontab -l` fails with "You (jeremy) are not allowed to access to (crontab) because of pam configuration." The above is code-derived only; the installed line would be:

`13 * * * * PATH=/home/jeremy/.local/bin:/home/jeremy/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin /usr/bin/env python3 /workspace/jeeves-rust-q3/bin/collect.py >> /home/jeremy/.local/state/jeeves/collect.log 2>&1`

---

# Q4 — The session-hook contract

**Confidence:** high for the script; settings.json verified against the bundled sample copy, not the live file

# Q4 — The session-hook contract

Sources: `.superpowers/crispy/rust-rewrite/ref/session-hook.sh` (the hook itself)
and `.superpowers/crispy/rust-rewrite/ref/settings.json.sample` (a copy of
`~/.claude/settings.json`; the real file is not present in this sandbox, so the
sample is the authority — Q4's question statement anticipated this case).

## Hook command string and registration

Registered under the **`SessionStart`** hook event, as the first entry of that
array (no `matcher`, so it fires for every session):

```
.superpowers/crispy/rust-rewrite/ref/settings.json.sample:91:    "SessionStart": [
.superpowers/crispy/rust-rewrite/ref/settings.json.sample:92:      {
.superpowers/crispy/rust-rewrite/ref/settings.json.sample:96:            "command": "\"$HOME/.claude/skills/auditing-worktrees/bin/session-hook.sh\"",
.superpowers/crispy/rust-rewrite/ref/settings.json.sample:97:            "timeout": 20,
.superpowers/crispy/rust-rewrite/ref/settings.json.sample:98:            "statusMessage": "Checking for worktree drift..."
```

Exact command string: `"$HOME/.claude/skills/auditing-worktrees/bin/session-hook.sh"`
with `timeout: 20` and `statusMessage: "Checking for worktree drift..."`. It is
not `async` (so Claude Code waits up to the timeout).

## stdin payload (fields actually used)

The hook slurps all of stdin and extracts exactly one field, `.cwd`:

```
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:33: payload=$(cat 2>/dev/null) || payload=""
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:34: cwd=$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null) || cwd=""
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:35: [ -n "$cwd" ] && [ -d "$cwd" ] && cd "$cwd" 2>/dev/null
```

It then re-derives the working directory: if `.cwd` is set, a directory, and the
`cd` succeeds, the hook runs there; otherwise it stays in the process's own cwd
(session-hook.sh:39-44 decides repo-vs-sweep mode from that cwd via
`git rev-parse --show-toplevel`). All other hook-payload fields are ignored.

## What the hook writes, and where

Nothing to disk, ever (the header comment states REPORT ONLY, session-hook.sh:8-10).
Its only output is a single JSON object on **stdout**:

- On success with something to report — a `{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: <body>}}` object where `<body>` is either a
  full per-repo report (repo mode) or a one-line-per-repo collapse from
  `summary_parse` (sweep mode):

```
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:71: jq -n --arg ctx "$body" \
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:72:   '{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:$ctx}}'
```

- On timeout (rc 124 from the audit) — the same `hookSpecificOutput` shape with a
  "exceeded its ${TIMEOUT}s budget... was skipped" message:

```
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:48: if [ "$rc" -eq 124 ]; then
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:49:   jq -n --arg ctx "Worktree audit exceeded its ${TIMEOUT}s budget in $(basename "$root") and was skipped. Run the auditing-worktrees skill manually for the full report." \
.superpowers/crispy/rust-rewrite/ref/session-hook.sh:50:     '{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:$ctx}}'
```

- Otherwise — **silent exit 0 with no stdout at all** (nothing to report, git or
  jq missing, audit script not executable, audit rc != 0, empty output; see
  session-hook.sh:26, 29-30, 53-54). A broken hook must never block a session.

The hook also reads env var `WORKTREE_AUDIT_HOOK_TIMEOUT` (default 15s, its own
per-run `timeout` budget, distinct from the 20s Claude Code `timeout` in
settings.json) and runs `audit-worktrees.sh --no-content <root>` via `timeout`
(session-hook.sh:37, 46).

## What must change in settings.json when this becomes a jeeves subcommand

Exactly one thing is mandatory: the `command` string at
`.superpowers/crispy/rust-rewrite/ref/settings.json.sample:96` must stop
pointing into `$HOME/.claude/skills/auditing-worktrees/bin/` and instead invoke
the single installed jeeves binary with a subcommand, e.g.
`"'$HOME/.local/bin/jeeves' session-hook"` (or bare `jeeves session-hook` if the
binary is on PATH). This is the same repoint pattern the other entries already
use — cf. the `'.../moshi-hook' claude-hook` and `bash '.../herdr-agent-state.sh' session`
entries at settings.json.sample:20, 107. The `timeout: 20` and `statusMessage`
(lines 97-98) can stay as-is; the hook event name `SessionStart` (line 91) and
array position stay as-is; the other SessionStart entries (matcher `"*"` herdr
hook at lines 102-111, moshi-hook at 112-120, non-claude-model-addendum at
121-129) are unrelated and untouched.

The Rust subcommand must reproduce the I/O contract verbatim: read the hook
payload JSON on stdin, use only `.cwd`, write the
`{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext:...}}`
object to stdout (or nothing, rc 0, when there's nothing to say), and must
complete well inside the 20s settings timeout — the internal audit budget
(currently the 15s `WORKTREE_AUDIT_HOOK_TIMEOUT`) is the mechanism that
guarantees that today.

---

# Q5 — On-disk state and input files

**Confidence:** high for code-cited claims; ~/.claude/projects absent in sandbox (from code, corroborated by live offsets.tsv)

# Q5 — On-disk state and input files (jeeves + absorbed scripts)

Scope: `bin/*.py` (collect.py, todos.py, tail.py, install-cron.py, jeeves_lib.py) and
`.superpowers/crispy/rust-rewrite/ref/*` (scan-active.sh, discover-roots.sh, git-state.sh,
session-discover.sh, session-tail.sh, session-hook.sh, summary-parser.sh, audit-worktrees.sh,
archive-branch.sh, clean-safe.sh, lib.sh, coverage-score, lint-checkin.py).

Sandbox note: `~/.claude/projects` does **not** exist in this sandbox (verified:
`ls ~/.claude/projects` → "No such file or directory"), so the transcript JSONLs themselves
are unreadable. Everything below about them is read from code, cross-checked against the
real `~/.local/state/jeeves/offsets.tsv` (readable here), which records paths of the shape
`~/.claude/projects/<slug>/<uuid>.jsonl`. The opencode/kilo SQLite DBs **are** readable in
this sandbox (`~/.local/share/opencode/opencode.db`, `~/.local/share/kilo/kilo.db`) and were
inspected with sqlite3.

---

## 1. jeeves state dir — `$JEEVES_STATE_DIR` or `$XDG_STATE_HOME/jeeves` (default `~/.local/state/jeeves`, mode 0700)

Defined in `bin/jeeves_lib.py:44-51` (`state_dir()`). Producer/consumer is jeeves itself
unless noted. All writes are atomic `.tmp` + `rename` unless noted.

| File | Format | Producer | Consumer / fields read | Cite |
|---|---|---|---|---|
| `config` | KEY=VALUE lines, `#` comments, `=`-split once | user/setup (not code) | `load_config()` reads `model`, `fallback_model`, `trivial_min`, `carry_max_h`, `batch_max`, `batch_under`, `truncate` (int coercion for known int keys) | jeeves_lib.py:67-77 |
| `collect.log` | append-only text, `ISO8601 msg\n` | `log()`; also cron stdout/stderr (`>> collect.log 2>&1`) | diagnostics only (no code reads it back) | jeeves_lib.py:80-83; install-cron.py:56-59 |
| `collect.lock` | empty file, flock'd | `main()` | non-blocking `flock(LOCK_EX\|LOCK_NB)`; second run skips | collect.py:1032-1038 |
| `offsets.tsv` | TSV, one row `path\toffset\tsize` | `offsets_save()` | `offsets_load()` → `{path: {offset, size}}`; `seed_offsets()`; SKILL.md invocation flow reads it for tail continuity | collect.py:22-38, 995-1021; SKILL.md:93 |
| `seen.ndjson` | NDJSON, one JSON object per line | `SeenStore.save()` | `SeenStore.load()` reads `hash` (dict key); fields `hash, line, first_seen, last_seen, count, status`; consumers: `apply_mutations` (dedupe adds), `reconcile` (register/dismiss), `prune_pending`, `delta_summary` (`by_status("open")`, `count`), `ingest_repo_todos` | jeeves_lib.py:229-269; todos.py:701-768, 886-907, 920-927, 990-1029 |
| `imports.ndjson` | NDJSON `{"path": ..., "hash": ...}` per line | `ingest_repo_todos()` | same fn reads `path`→`hash` map to skip unchanged repo TODO files | todos.py:994-1027 |
| `pending.json` | JSON array (indent=1) of mutation rows | `save_pending()` | `load_pending()`; fields read: `op`, `line`, `evidence`, `repo`, `reason`, `queued`, `seen`, `kind`, `source`; `_seen_of` tolerates junk `seen` | todos.py:613-625, 645-654, 678-698, 1123-1163 |
| `evidence_memo.json` | JSON object, key `repo\x1fevidence` → `{"verdict","t"}` | `_save_memo()` (atexit) | `_load_memo()`; TTL 300s; UNKNOWN never cached | todos.py:505-531, 575-605 |
| `tf-state.json` | JSON `{"lines": [...]}` | `tf_diff()` | same fn: `lines` set-diffed against `taskferry list` output | collect.py:197-212 |
| `project-dirs.txt` | one dir path per line | `run_once()` (union of transcript `cwd`s) | `_repo_origins()` → `git remote get-url origin` per dir | collect.py:99-127, 919-922 |
| `git-state.md` | markdown: timestamp header + scan-active.sh TOON output | `git_state()` | SKILL.md invocation path (ground truth for digest); not re-parsed by code | collect.py:215-252; SKILL.md:190-199 |
| `digests/<date>.md` | markdown digest | `run_once()` | SKILL.md invocation path (line 83); not re-parsed by code | collect.py:974-976 |
| `summaries/<date>/<slug>--<sid>--<n>.md` | JSON blob (indent=1) per session | `write_summary()` | `synthesis_prompt()` globs `*.md`, embeds raw text in fenced block; `n` = count of existing `*--<sid>--*.md` + 1 | collect.py:488-492, 495-509, 896-897 |
| `synthesis-raw/<date>--<stamp>--<tag>.txt` | raw ferry message text | `save_raw_synthesis()` | diagnostics only; keeps newest 20 | collect.py:559-572 |
| `staging/<date>--<HHMMSS>--<gi>/` (0700) | per-run dir: `<slug>--<sid>.jsonl` (raw redacted delta lines) + `summary-<sid>.json` (ferry-written) | `stage_slice()`; ferry (via `--directory --no-overlay`) | `read_staged_summaries()` reads `summary-<sid>.json`: fields `session` (must equal sid), `shipped, oversaw, loose_ends, tangents, overlooked, failures, shape` (list-typed check); symlink refused; pruned after 6h, deleted at run end | collect.py:268-287, 395-404, 439-485, 751-786 |
| `last_wake` | one ISO timestamp line | `wake()` | `last_wake()` | todos.py:910-917 |

## 2. jeeves data dir — `$JEEVES_DATA_DIR` or `$XDG_DATA_HOME/jeeves` (default `~/.local/share/jeeves`)

Defined in `bin/jeeves_lib.py:54-60` (`data_dir()`).

| File | Format | Producer | Consumer / fields read | Cite |
|---|---|---|---|---|
| `todo.md` | markdown: `# jeeves todo ledger`, `## open` / `## done` / `## dismissed` sections, `- [ ]`/`- [x]` bullets + `(jeeves: kind, source, date)` / `(dismissed date)` provenance tags | `_write()` (atomic .tmp+replace); created with `SKELETON` if missing | `parse_ledger()` (line-based section split); `find_match` via `normalize()` (NFKC casefold, provenance-strip, bullet-strip); consumers: `apply_add/check/dismiss`, `reconcile`, `prune_pending`, `delta_summary`, and `synthesis_prompt` (open lines fed to ferry) | todos.py:56-96, 99-148, 71-80; collect.py:502 |

This is the one **user-facing, hand-editable** format — a Rust port must round-trip it
byte-compatibly (SKILL.md:80-81 documents the path).

## 3. Claude Code session JSONLs — `~/.claude/projects/<slug>/*.jsonl`

(`JEEVES_PROJECTS_ROOT` override, `bin/jeeves_lib.py:63-64`; slug = project dir with
non-alphanumerics → `-`, per `bin/tail.py:13` and ref/session-discover.sh:15.)

- **Producer:** Claude Code. **Format:** JSONL, one JSON object per line.
- **Consumers:**
  - `collect.py discover_sessions()` globs `*/*.jsonl` (collect.py:41-42); `read_delta()`
    does byte-offset incremental reads, newline-boundary aligned, rotation-detected
    (jeeves_lib.py:96-110).
  - `denoise()` reads per line: `isSidechain` (skip if truthy), `attachment` (skip if key
    present), `message.role` (must be `"user"`/`"assistant"`), `message.content` (string, or
    list of `{type:"text", text}`), `timestamp` (jeeves_lib.py:122-145). Output `{t, r, x}`
    truncated to `truncate` (default 800) chars.
  - `collect_cwds()` reads `cwd` per line (collect.py:690-699) → feeds `project-dirs.txt`.
  - `bin/tail.py` uses the same `read_delta`+`denoise` (tail.py:38-41).
  - ref/session-tail.sh reads the same JSONL via jq: `.timestamp`, `.message.role`,
    `.message.content` array of `.type=="text"` `.text`, first 800 chars (session-tail.sh:16-25).
- **Sandbox status:** `~/.claude/projects` absent here; expectations above are from code
  only, corroborated by real `offsets.tsv` rows naming `~/.claude/projects/<slug>/<uuid>.jsonl`.

## 4. opencode / kilo SQLite session DBs + the CLI-only access rule

- **Files:** `~/.local/share/opencode/opencode.db` and `~/.local/share/kilo/kilo.db`
  (both WAL-mode; verified live). Tables (opencode): `session` (id, project_id, directory,
  title, time_created/updated, …), `message` (id, session_id, time_created, `data` JSON),
  `part` (id, message_id, session_id, `data` JSON), plus project/workspace/event tables.
  kilo.db has the same schema family.
- **Access rule (CLI-only):** orient's SKILL.md states it explicitly:
  > "OpenCode keeps sessions in a SQLite DB reached only through the `opencode` CLI
  > (`session list`, `export`). Never read the DB." — orient SKILL.md:150
  (worktree copy at `/workspace/claude-worktrees/review-level-ask/skills/orient/SKILL.md:150`).
- **Implementation in the absorbed code:** ref/session-discover.sh:31-41 shells
  `opencode session list` (ids `ses_...`), then `opencode export <id>` and reads only the
  first 800 bytes, extracting the `"directory"` field to match the project dir. Bounded by
  `ORIENT_OPENCODE_SCAN` (default 12). **No jeeves or ref/ code opens either DB.**
- **Related but distinct rule:** taskferry ADR 0003 ("Taskferry stays CLI-only") rejects
  native tool-spec exposure for taskferry itself — not about reading these DBs
  (`/workspace/taskferry/docs/adr/0003-reject-native-plugin-tool-exposure.md:35-45`).
- **Rust port impact:** if the CLI-only rule is kept, a Rust port needs **no SQLite
  access** — it must shell out to `opencode session list` / `opencode export` (export emits
  JSONL-ish session data). Reading the DBs directly (rusqlite) would violate the documented
  rule and race a live WAL writer. This is a decision point, not a requirement.

## 5. taskferry state dirs — `$TASKFERRY_STATE_DIR` or `$XDG_STATE_HOME/taskferry` (default `~/.local/state/taskferry`)

Producer: taskferry daemon (paths.js:22-24; initManagerPaths tasks.js:4676-4686).

| File | Format | jeeves consumer / fields read | Cite |
|---|---|---|---|
| `logs/<tid>.ndjson` | NDJSON of opencode event stream | `task_log_message()`: per line `type=="text"` → `part.text` concatenated; fallback when daemon summarizes a large result | jeeves_lib.py:412-429 |
| `outputs/<tid>/` | per-task scratch dir, exposed as `$TASKFERRY_OUTPUT_DIR` | not read by jeeves code (this task's own output dir is one) | taskferry src/output-dir.js:45-64 |
| `summaries/`, `prompts/`, `tasks.json`, `tasks.lock` | taskferry-internal | not read by jeeves | tasks.js:4676-4686 |

Sandbox note: `~/.local/state/taskferry/logs` does not exist here (only `outputs/`), so the
NDJSON shape above is from code + taskferry source (tasks.js:2063, 2914, 5872 confirm the
`type:"text"`/`part.text` event shape).

jeeves also consumes taskferry **CLI stdout** (not files): `taskferry list --all --limit 40`
lines containing `oc_` (collect.py:200-212); `dispatch/wait/result` stdout parsed for
`oc_[a-z0-9_]+` task id, `status:`, `failureDetail:`/`failureReason:`, `incomplete: true`,
`message:` (bare or quoted-escaped), `summaryOf:` (jeeves_lib.py:484-548, 275-312).

## 6. Files of the absorbed scripts (ref/)

| File | Format | Producer | Consumer / fields read | Cite |
|---|---|---|---|---|
| `$XDG_STATE_HOME/orient/roots.txt` (default `~/.local/state/orient/roots.txt`; `ORIENT_ROOTS_FILE` override) | one repo path per line, sorted | `discover-roots.sh` (mkdir -p + printf) | `scan-active.sh` `mapfile` → root set (fallback `ORIENT_ROOTS` else `/workspace`); collect.py refreshes it via discover-roots.sh before each scan | discover-roots.sh:53, 109-110; scan-active.sh:88-93; collect.py:219-246 |
| `~/.claude/skills/orient/bin/scan-active.sh`, `discover-roots.sh` | shell scripts | installed skill | invoked by collect.py `git_state()` (bash subprocess, 180s timeout) | collect.py:219-246 |
| `~/.claude/skills/auditing-worktrees/bin/lib.sh` (`AUDIT_WORKTREES_LIB` override) | shell lib | auditing-worktrees skill | sourced by scan-active.sh (coverage_score/validate_pct), archive-branch.sh, audit-worktrees.sh, clean-safe.sh, coverage-score | scan-active.sh:110-120; lib.sh:180-268 |
| `~/.claude/skills/auditing-worktrees/bin/coverage-score` (`AUDIT_WORKTREES_BIN` override) | CLI, stdout `SCORED <0-100>` / `UNSCORED <why>` / `UNKNOWN <why>` | auditing-worktrees | todos.py `_coverage_landed()` regex-parses `SCORED (\d+)`, 0-100 range check, threshold `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD` (default 95) | todos.py:279-361 |
| `<gitdir>/locked` (worktree lock) | text containing `pid <n>` and `start <n>` | git | `lock_status()` in lib.sh:64-77 (via archive/audit/clean-safe); `/proc/<pid>/stat` field 20 (starttime) cross-checked | lib.sh:32-47, 64-77 |
| repo `TODO*`/`todo*` files (`.md`/`.txt`/no suffix) | loose checklist markdown | user repos | `ingest_repo_todos()` `_open_items()`: `- [ ]`/`- [x]`/`- ` bullets, continuation lines, `(fixed ...)` marks, section headers; read-only | todos.py:930-987, 990-1029 |
| `~/.claude/settings.json` | JSON | user | SessionStart hook registration (ref/settings.json.sample:91-101); session-hook.sh itself reads **stdin** JSON payload `.cwd` and writes nothing (emits jq JSON to stdout) | session-hook.sh:33-35, 71-72 |
| stdin / argv[1] file (lint-checkin.py) | markdown | caller | bullet lines `^\s*[-*]\s+`, rules: ≤120 chars, ≤2 commas, ≤1 bold span | lint-checkin.py:25-65 |

git-state.sh, session-tail.sh, summary-parser.sh, audit-worktrees.sh, archive-branch.sh,
clean-safe.sh read/write **no state files** — git plumbing + stdin/stdout only.

## 7. What forces native parsing in a Rust port

1. **Native JSONL streaming is mandatory** (serde_json line-by-line):
   - Claude transcripts — `denoise` field selection (`isSidechain`, `attachment`,
     `message.role`, `message.content` str-or-list-of-`{type,text}`, `timestamp`) and
     `collect_cwds` (`cwd`); byte-offset incremental reads with rotation detection
     (jeeves_lib.py:96-145, 690-699).
   - `seen.ndjson` and `imports.ndjson` (jeeves_lib.py:229-269; todos.py:994-1027).
   - taskferry `logs/<tid>.ndjson` (`type`/`part.text`) — jeeves_lib.py:412-429.
   - Staged `<slug>--<sid>.jsonl` copies (collect.py:395-404).
2. **SQLite is NOT forced** — the CLI-only rule (orient SKILL.md:150) means opencode/kilo
   sessions are reached via `opencode session list`/`export` subprocesses, never the DB.
   Only if the port breaks that rule does it need rusqlite (WAL-mode DBs, read-only access).
3. **TSV** (`offsets.tsv`, 3 columns) — trivial, but the atomic `.tmp`+rename write pattern
   and the flock on `collect.lock` (fcntl → `fs2`/`libc::flock`) must be replicated.
4. **todo.md** is a hand-editable markdown format with provenance-tag normalization
   (`normalize()`: NFKC casefold, stacked `(jeeves: ...)`/`(dismissed ...)` stripping,
   bullet lstrip, whitespace collapse, sha256 line hashing) — the trickiest byte-compat
   surface; a Rust port must reproduce `normalize`/`line_hash` exactly or the ledger's
   unique-match dedupe drifts (jeeves_lib.py:152-174).
5. **TOON stdout parsing** of `taskferry result` (`message:` bare vs quoted-escaped,
   `status:`, `incomplete: true`, `summaryOf:`) and of scan-active.sh output is string
   parsing, not file parsing, but is part of the same port surface (jeeves_lib.py:275-312,
   484-548).

---

# Q6 — Thresholds and overrides

**Confidence:** high (file:line throughout)

# Q6 - Thresholds and overrides

## (a) WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD

**Readers (3 production + tests):**
- `bin/todos.py:298` — `_coverage_threshold()`
- `.superpowers/crispy/rust-rewrite/ref/audit-worktrees.sh:29`
- `.superpowers/crispy/rust-rewrite/ref/scan-active.sh:137`
- tests: `tests/test_spec_coverage.py:241`, `tests/conftest.py:47`

**Default: 95** everywhere:
- `bin/todos.py:301` — `return 95`
- `ref/audit-worktrees.sh:30` — `CONTENT_MERGE_THRESHOLD=95  # unset -> default`
- `ref/scan-active.sh:123` — `CONTENT_MERGE_THRESHOLD=95`

**Validation (malformed or 0 → fall back to 95, never silently different):**
- `bin/todos.py:299` — `if raw is not None and re.fullmatch(r"[1-9][0-9]?|100", raw):` (0 rejected with the rest; comment at 295-296: "0 is rejected with the rest, since it would call every commit landed")
- `ref/audit-worktrees.sh:32-38` — via `validate_pct`; 0 rejected (comment 33-35: `pct >= 0` is true for SCORED 0, so a 0 threshold offers every open branch for batch archive)
- `ref/scan-active.sh:138-141` — via `validate_pct`; warning on stderr, falls back to 95

**Where the comparison happens:**
- `bin/todos.py:359` — `landed = score >= _coverage_threshold()` (inside `_coverage_landed`, which returns True only when the score is at/above threshold; UNSCORED/UNKNOWN/missing CLI all return False = "declined to judge", see 330-334, 350-351)
- `ref/audit-worktrees.sh:115` — `if [ "$pct" -ge "$CONTENT_MERGE_THRESHOLD" ]; then` → `is_content_merged=true` (bucket: likely-content-merged → batch archive)
- `ref/scan-active.sh:312` — `if [ "$_pct" -ge "$CONTENT_MERGE_THRESHOLD" ]; then` → marks branch `_merged` as `content: N%`

## (b) AUDIT_WORKTREES_BIN and AUDIT_WORKTREES_LIB

**AUDIT_WORKTREES_BIN** (points at the *bin dir* containing `coverage-score`):
- Set by: `.github/workflows/check.yml:58` — `echo "AUDIT_WORKTREES_BIN=$PWD/.ci/auditing-worktrees/bin" >> "$GITHUB_ENV"` (CI pins the sibling repo at check.yml:44-46, ref `ea0915cded8eb899c324765b2430964eb78f288c`). Also set by tests (`tests/test_spec_coverage.py:160` etc.).
- Read by: `bin/todos.py:285` — `root = os.environ.get("AUDIT_WORKTREES_BIN") or str(Path.home() / ".claude" / "skills" / "auditing-worktrees" / "bin")` (read at call time, not import, per 282-283); `tests/test_spec_coverage.py:28`.
- When unset: defaults to `~/.claude/skills/auditing-worktrees/bin`; `_coverage_score_bin()` returns `Path(root) / "coverage-score"` (todos.py:287). If that CLI is missing, `subprocess.run` raises `FileNotFoundError`, caught at `bin/todos.py:350` → returns False → treated as "declined to judge", not outstanding (todos.py:333-334: "A missing CLI reads the same way: this closes a gap in the offline path, it does not make auditing-worktrees a dependency").

**AUDIT_WORKTREES_LIB** (points at auditing-worktrees' `lib.sh`):
- Set by: nobody in this repo — it is only read (no setter found anywhere in the repo; CI sets only `AUDIT_WORKTREES_BIN`).
- Read by: `ref/scan-active.sh:110` — `AUDIT_LIB="${AUDIT_WORKTREES_LIB:-$HOME/.claude/skills/auditing-worktrees/bin/lib.sh}"`; sourced at `ref/scan-active.sh:120` — `[ -r "$AUDIT_LIB" ] && . "$AUDIT_LIB"`.
- When unset: falls back to the default path; if the lib is absent, sourcing is skipped and the content-coverage pass is off — scan-active degrades to ancestry + tree-match classification (scan-active.sh:104-109: "Sourcing is best-effort — with the lib absent this script degrades to the ancestry + tree-match answer it gave before, never to an error").
- Set-but-unreadable is distinguished from unset: `ref/scan-active.sh:116-118` — `if [ -n "${AUDIT_WORKTREES_LIB:-}" ] && [ ! -r "$AUDIT_WORKTREES_LIB" ]; then echo "warning: ... not readable; content-coverage classification is off" >&2` (a typo'd override must not silently read as "skill not installed").

## (c) coverage-score's three exit states

All three verdicts are printed on stdout and **all exit 0**; only usage errors exit 2:
- `ref/coverage-score:14-15` — "The verdict line is the output, not the exit code: an UNSCORED or UNKNOWN verdict is a successful run. Exit 2 on usage error."
- `ref/coverage-score:34-35` — "Exit 0 on every successful run (including UNSCORED/UNKNOWN verdicts); exit 2 on usage error."
- Exit 2 sites: `ref/coverage-score:52` (unknown flag), `:58` (wrong arg count), `:67` (repo not a directory). `--help` exits 0 (`:45`).

**SCORED <0-100>** — the branch's net text lines already in base, as a percentage:
- `ref/lib.sh:247` — `echo "SCORED $pct"` (pct = `(O - |R|) * 100 / O`, clamped to 0-100; reached only when the merge-tree + residual diff succeed and both numstat passes return plain numbers)

**UNSCORED <why>** — scorer declines because there is no text to score:
- `ref/lib.sh:169` — `UNSCORED binary` (a `- -` numstat row)
- `ref/lib.sh:170` — `UNSCORED mode-only` (a `0 0` row)
- `ref/lib.sh:195` — `UNSCORED no-text-rows` (O == 0)
- passthrough of numstat_net's verdicts at `ref/lib.sh:193` and `:235`

**UNKNOWN <why>** — scorer cannot determine the answer:
- `ref/lib.sh:182-183` — `UNKNOWN no-merge-base` (merge_base fails: criss-cross history, `:151-154`, or no base)
- `ref/lib.sh:186` — `UNKNOWN branch-diff-failed`
- `ref/lib.sh:202` — `UNKNOWN no-temp-dir`; `:204` — `UNKNOWN no-object-dir`
- `ref/lib.sh:230` — `UNKNOWN merge-conflict` (merge-tree rc 1)
- `ref/lib.sh:231` — `UNKNOWN merge-tree-error` (rc > 1)

Consumers treat UNSCORED/UNKNOWN as "declined to judge", never as outstanding:
- `bin/todos.py:330-334` — "UNSCORED (a binary or mode-only row) and UNKNOWN (criss-cross history, a merge conflict) mean the scorer declined to judge, not that the work is outstanding, so the caller keeps asking."
- `ref/audit-worktrees.sh:126-131` — UNSCORED/UNKNOWN → `unscored=true` → forced to needs-triage, "NEVER archaeology (never batch-archived) and never clean-safe"
- `ref/scan-active.sh:305-307` — "UNSCORED/UNKNOWN verdicts fall through untouched: they mean 'this says nothing', not 'outstanding'"

## (d) Every other env-var knob

**scan-active.sh:**
- `ORIENT_ROOTS` — `ref/scan-active.sh:92` — `IFS=': ' read -r -a roots <<< "${ORIENT_ROOTS:-/workspace}"` (used only when no [root] args and no roots file); documented at `:26`
- `ORIENT_ROOTS_FILE` — `ref/scan-active.sh:88` — `ROOTS_FILE="${ORIENT_ROOTS_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/orient/roots.txt}"`; if the file exists and is non-empty it wins over ORIENT_ROOTS (`:89-93`)
- `ORIENT_COMMIT_LIMIT` — `ref/scan-active.sh:102` — `COMMIT_LIMIT="${ORIENT_COMMIT_LIMIT:-15}"` (rows per repo; true total always reported, `:461`)
- `ORIENT_CONTENT_SCORING` — `ref/scan-active.sh:129` — `if [ "${ORIENT_CONTENT_SCORING:-1}" != 0 ]` (set to 0 to skip the content-coverage pass entirely even when the lib is present)

**discover-roots.sh:**
- `ORIENT_ROOT_CANDIDATES` — `ref/discover-roots.sh:50` — `IFS=': ' read -r -a roots <<< "${ORIENT_ROOT_CANDIDATES:-/workspace $HOME/.claude $HOME/.dotfiles}"`
- `ORIENT_ROOTS_FILE` — `ref/discover-roots.sh:53` — same default `$XDG_STATE_HOME/orient/roots.txt` (shared with scan-active.sh)

**session-discover.sh:**
- `ORIENT_OPENCODE_SCAN` — `ref/session-discover.sh:30` — `k="${ORIENT_OPENCODE_SCAN:-12}"` (newest N OpenCode sessions to look at)

**lib.sh (auditing-worktrees, sourced by audit-worktrees.sh and scan-active.sh):**
- `WORKTREE_AUDIT_INFLIGHT_SECS` — `ref/lib.sh:11` — default 7200 (2h; activity newer than this is in-flight, beats every other classification)
- `WORKTREE_AUDIT_ARCHAEOLOGY_SECS` — `ref/lib.sh:15` — default 7776000 (90 days; older + never pushed → archaeology/batch-archive)
- `WORKTREE_AUDIT_ARCHIVE_PREFIX` — `ref/lib.sh:17` — default `archive`

**session-hook.sh:**
- `WORKTREE_AUDIT_HOOK_TIMEOUT` — `ref/session-hook.sh:37` — `TIMEOUT="${WORKTREE_AUDIT_HOOK_TIMEOUT:-15}"` (seconds budget for the audit subprocess; rc 124 → skipped message, `:48-49`)

Note: `bin/todos.py` reads only `AUDIT_WORKTREES_BIN` (todos.py:285) and `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD` (todos.py:298) — no other env knobs.

---

# Q7 — Test harness patterns and external references

**Confidence:** pending check

# Q7 — Test harness patterns and external references

## (a) Test structure and the `needs_real_cli` real-boundary tests

**Suite-wide isolation fixture** — `tests/conftest.py:9-53` is an autouse fixture
(`_isolate_jeeves_state`) that points every jeeves directory at a tmp path and
clears todos.py's memo caches for the whole suite:

```python
tests/conftest.py:36-46
    base = tmp_path_factory.mktemp("jeeves-isolated")
    monkeypatch.setenv("JEEVES_STATE_DIR", str(base / "state"))
    monkeypatch.setenv("JEEVES_DATA_DIR", str(base / "data"))
    monkeypatch.setenv("JEEVES_PROJECTS_ROOT", str(base / "projects"))
    ...
    monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(base / "no-such-bin"))
```

The docstring (lines 10-35) explains why: without it, `jl.log()` writes into the
live `~/.local/state/jeeves/collect.log` and todos.py's `_MEMO` singleton leaks
verdicts across tests (a real 32KB leak into the machine's actual
`evidence_memo.json` happened). `AUDIT_WORKTREES_BIN` is pointed at a
nonexistent bin by default so commit-evidence tests take the same path on dev
boxes and CI; tests that mean to exercise the pass set it themselves and win
(their `monkeypatch.setenv` runs after the autouse one). `conftest.py:47-53`
also deletes `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD` and resets
`_MEMO`, `_PR_CACHE`, `_ISSUE_CACHE`, `_PULLS_CACHE`, `_DEFAULT_BRANCH_CACHE`,
`_COVERAGE_CACHE`.

**Per-test patterns** (no pytest fixtures beyond `tmp_path`/`monkeypatch`; the
only declared fixture in the suite is the autouse one):
- Real git repos built in `tmp_path` with `subprocess.run(["git", ...])` and
  per-repo `user.email`/`user.name` config (e.g. `test_spec_coverage.py:53-65`
  `_init`, `test_spec_evidence.py` `_commit`/`_head`). The `_init` docstring
  notes identity must be set locally because `git merge --squash` needs a
  committer identity and CI runners have no global git config.
- Monkeypatching: `monkeypatch.setattr(td, "_capture", fake_capture)` stubs the
  `gh-axi` subprocess while letting real git calls through
  (`test_spec_coverage.py:118-130` `_stub_gh`); `monkeypatch.setattr(cc.subprocess,
  "run", fake_run)` records/forks subprocess calls (`test_collect.py:500-504`);
  `monkeypatch.setattr(cc.Path, "home", lambda: tmp_path)` relocates
  `~/.claude/skills/...` lookups (`test_collect.py:505`); `monkeypatch.setattr(td,
  "_COVERAGE_TIMEOUT", 0.2)` shrinks timeouts (`test_spec_coverage.py:332`).
- CLI-level tests spawn the real entry point: `test_spec_cli.py:9` runs
  `[sys.executable, TODOS_PY, ...]`; `test_tail.py:9-15` `_run()` runs
  `tail.py` with `JEEVES_STATE_DIR` in the env.
- Stub binaries: `test_spec_coverage.py:133-146` `_stub_coverage` writes a
  `#!/bin/sh\necho "<verdict>"` script named `coverage-score` into a tmp bin dir
  and points `AUDIT_WORKTREES_BIN` at it — the verdict strings are the CLI's
  published contract, so integration is testable without auditing-worktrees
  installed.

**The `needs_real_cli` marker** — `tests/test_spec_coverage.py:25-41`:

```python
tests/test_spec_coverage.py:28-30
_real_bin_root = os.environ.get("AUDIT_WORKTREES_BIN") or str(
    Path.home() / ".claude" / "skills" / "auditing-worktrees" / "bin")
REAL_COVERAGE_BIN = Path(_real_bin_root)

tests/test_spec_coverage.py:37-41
needs_real_cli = pytest.mark.skipif(
    not (REAL_COVERAGE_BIN / "coverage-score").is_file() and os.environ.get("CI") != "true",
    reason="auditing-worktrees is not installed; the stub-backed tests below still "
           "cover the integration, this one covers the real boundary",
)
```

So: skip locally when the real binary is absent, but **fail in CI** when it is
missing — the skip condition is `not is_file() and CI != "true"`, so on CI the
tests always run and turn red if the binary isn't there. Four tests carry the
marker (`test_spec_coverage.py:149, 166, 180, 191`): each builds a real git repo
in `tmp_path` in the squash-onto-advanced-base shape, sets
`monkeypatch.setenv("AUDIT_WORKTREES_BIN", str(REAL_COVERAGE_BIN))`, stubs
`gh-axi` via `_stub_gh`, and asserts `td.classify_evidence(...)` verdicts
(LANDED/OUTSTANDING) plus which sources were consulted. The stub-backed tests
(lines 201-334) cover jeeves' behavior with a fake scorer; the real-boundary
tests prove the real scorer produces the answers the contract claims.

**What CI does to make the real binary present** — `.github/workflows/check.yml`:
- `check.yml:41-53` — a second `actions/checkout@v4` fetches the sibling repo
  `jeremysball/auditing-worktrees` pinned to commit
  `ea0915cded8eb899c324765b2430964eb78f288c` into `.ci/auditing-worktrees`,
  using `secrets.JEEVES_CI_PAT || github.token` (a plain GITHUB_TOKEN 404s on
  the private sibling; the PAT is scoped to that repo with Contents: Read).
- `check.yml:54-58` — asserts the binary exists and exports it:

```yaml
      - name: Point coverage at the pinned sibling
        run: |
          test -x ".ci/auditing-worktrees/bin/coverage-score" \
            || { echo "coverage-score missing from pinned sibling"; exit 1; }
          echo "AUDIT_WORKTREES_BIN=$PWD/.ci/auditing-worktrees/bin" >> "$GITHUB_ENV"
```

- `check.yml:60-67` — then `uv run ruff check bin tests`, `uv run mypy bin`,
  `uv run pytest -q`. The comment at lines 32-40 states the intent: the
  real-boundary tests "FAIL in CI when it is absent — never silently skip, so
  contract drift cannot ship green", and the pin is deliberate so the contract
  is "a deliberate, reviewable fact rather than 'whatever main is today'".

## (b) Consumer list — every reference to the five external names

`rg` over the whole checkout (excluding `uv.lock` and `.git`), grouped by name.
"Expects" = what the referencing code assumes about the external tool.

### coverage-score
| file:line | expects |
|---|---|
| `bin/todos.py:280,287` | `_coverage_score_bin()` = `$AUDIT_WORKTREES_BIN/coverage-score`, else `~/.claude/skills/auditing-worktrees/bin/coverage-score`; read at call time |
| `bin/todos.py:305` | comment: each call re-spawns a coverage-score subprocess (merge-tree + two diffs), hence `_COVERAGE_CACHE` |
| `bin/todos.py:322` | `_coverage_landed()` spawns it with `_COVERAGE_TIMEOUT=60`; parses `SCORED <n>` verdicts, treats `UNSCORED`/`UNKNOWN`/out-of-range/missing as "declined to judge" |
| `.github/workflows/check.yml:33,56,57` | CI fetches pinned sibling, asserts `bin/coverage-score` is executable, exports `AUDIT_WORKTREES_BIN` |
| `tests/test_spec_coverage.py:11,25,34,38,134,143,266,269,298-299,317,328` | real-boundary tests + stub binary named `coverage-score`; `_coverage_score_bin()` path assertions; spawn-count assertions |
| `tests/conftest.py:40` | comment: todos.py shells out to it; fixture points `AUDIT_WORKTREES_BIN` at nothing by default |
| `SKILL.md:124` | "via `auditing-worktrees`' `coverage-score`, when it is installed; point `AUDIT_WORKTREES_BIN` elsewhere to move it" — the middle of classify_evidence's three sources |
| `.superpowers/crispy/rust-rewrite/01-questions.md:16,20,60,69` | questions: its `SCORED\|UNSCORED\|UNKNOWN` contract, exit behavior, `AUDIT_WORKTREES_LIB` |
| `.superpowers/crispy/rust-rewrite/ref/coverage-score` | the reference implementation itself (usage: `coverage-score <repo> <base> <branch>`) |
| `.superpowers/crispy/rust-rewrite/ref/audit-worktrees.sh:19` | "the sibling CLI (bin/coverage-score)" — `--no-content` flag parity |
| `.superpowers/crispy/rust-rewrite/ref/lib.sh:191` | comment: verdict strings must stay verbatim or "broke the contract the coverage-score CLI publishes to its consumers" |

### scan-active.sh
| file:line | expects |
|---|---|
| `bin/collect.py:216,219,222,228` | `git_state()` runs `bash ~/.claude/skills/orient/bin/scan-active.sh <since>` (default `yesterday 00:00`, `SINCE` env override), 180s timeout, output persisted to `state/git-state.md`; missing script → honest `"(git scan unavailable: ...)"` string |
| `bin/install-cron.py:46` | comment: "scan-active.sh does all repo discovery through [fd], so a PATH without it reports every workspace as empty" — fd must be on the cron PATH |
| `tests/test_collect.py:494,506,531` | fakes the script at `~/.claude/skills/orient/bin/scan-active.sh`; asserts `discover-roots.sh` runs first, then `["bash", scan, "yesterday 00:00"]`; missing discover tolerated |
| `SKILL.md:14,191` | "cross-repo git state (via orient's `scan-active.sh`)"; `git-state.md` is "a read-only `scan-active.sh` rollup" |
| `.superpowers/crispy/rust-rewrite/01-questions.md:15,21,25` | Q2: how `bin/collect.py` consumes it; the record shape it emits |
| `.superpowers/crispy/rust-rewrite/ref/scan-active.sh` | the reference implementation itself |
| `.superpowers/crispy/rust-rewrite/ref/discover-roots.sh:5,7` | "Persists to a roots file for scan-active.sh to read"; "scan-active.sh used to hardcode /workspace" |

### archive-branch.sh
| file:line | expects |
|---|---|
| `.superpowers/crispy/rust-rewrite/ref/scan-active.sh:364` | "content-merged branches go to `archive-branch.sh --strict`, never to the literal-ancestry deletion path" |
| `.superpowers/crispy/rust-rewrite/ref/archive-branch.sh` | the reference implementation itself (`--list`, `--strict` modes) |
| `.superpowers/crispy/rust-rewrite/01-questions.md:17,69` | listed among scripts to absorb / repoint |

No references in jeeves `bin/`, `tests/`, `SKILL.md`, `prompts/`, or `.github/`.

### session-hook.sh
| file:line | expects |
|---|---|
| `.superpowers/crispy/rust-rewrite/ref/settings.json.sample:96` | Claude Code settings sample wires it as a SessionStart hook: `"command": "\"$HOME/.claude/skills/auditing-worktrees/bin/session-hook.sh\""`, timeout 20, "Checking for worktree drift..." |
| `.superpowers/crispy/rust-rewrite/ref/session-hook.sh` | the reference implementation itself (report-only; calls `$BIN/audit-worktrees.sh`; silent exit on any error) |
| `.superpowers/crispy/rust-rewrite/01-questions.md:17,40,43` | Q4: its exact command string, stdin payload, exit behavior |

No references in jeeves `bin/`, `tests/`, `SKILL.md`, `prompts/`, or `.github/`.

### audit-worktrees (the sibling repo/skill name)
| file:line | expects |
|---|---|
| `bin/todos.py:280` | "auditing-worktrees' coverage-score CLI" — default install root `~/.claude/skills/auditing-worktrees/bin` |
| `tests/conftest.py:40` | comment: todos.py shells out to "auditing-worktrees' coverage-score" |
| `tests/test_spec_coverage.py:11,25,269` | docstring + default fallback path `~/.claude/skills/auditing-worktrees/bin/coverage-score` |
| `.github/workflows/check.yml:33,44,56` | fetches `jeremysball/auditing-worktrees` @ `ea0915cded8eb899c324765b2430964eb78f288c` into `.ci/auditing-worktrees`; asserts its `bin/coverage-score` |
| `SKILL.md:124` | "`auditing-worktrees`' `coverage-score`, when it is installed" |
| `.superpowers/crispy/rust-rewrite/01-questions.md:5,17` | "absorbing the orient / orient-quick / auditing-worktrees machinery"; `audit-worktrees.sh` listed |
| `.superpowers/crispy/rust-rewrite/ref/audit-worktrees.sh` | the reference implementation itself |
| `.superpowers/crispy/rust-rewrite/ref/clean-safe.sh:2` | "Deletes branches already confirmed safe-to-clean by audit-worktrees.sh" |
| `.superpowers/crispy/rust-rewrite/ref/session-hook.sh:25` | `AUDIT="$BIN/audit-worktrees.sh"` |
| `.superpowers/crispy/rust-rewrite/ref/settings.json.sample:96` | path `$HOME/.claude/skills/auditing-worktrees/bin/session-hook.sh` |

### orient-quick
| file:line | expects |
|---|---|
| `SKILL.md:3` | description: "NOT for ... 'reorient me' belong to orient/orient-quick" |
| `SKILL.md:36` | "a single project's done-vs-remaining card (orient-quick)" — NOT jeeves' job |
| `SKILL.md:164` | "Same scan rules as orient-quick: plain Unicode glyphs (no color emoji), one bold payload per bullet..." — jeeves' card format inherits orient-quick's rules |
| `.superpowers/crispy/rust-rewrite/ref/lint-checkin.py:2` | "Lint an orient-quick status card against its own scannability rules" |
| `.superpowers/crispy/rust-rewrite/01-questions.md:5` | "absorbing the orient / orient-quick / auditing-worktrees machinery" |

### prompts/ and .githooks/
No references to any of the five names in `prompts/` (extract-rubric.md,
extract-tools.md, synthesis.md) or `.githooks/pre-commit`.

## (c) What `bin/jeeves_lib.py` provides to the shared entry points

`bin/jeeves_lib.py` is the stdlib-only shared module; all four entry points
import it as `jl` (`bin/collect.py:16`, `bin/todos.py:15`, `bin/tail.py:9`,
`bin/install-cron.py:8`). Shared surface:

- **State paths** (the env-var contract the conftest fixture and CI rely on):
  - `state_dir()` (`jeeves_lib.py:44-51`) — `JEEVES_STATE_DIR` or
    `$XDG_STATE_HOME/jeeves` (default `~/.local/state/jeeves`), mkdir 0700.
    Holds `collect.log`, `config`, `seen.ndjson`, `offsets.tsv`,
    `project-dirs.txt`, `git-state.md`, `staging/`, `summaries/`,
    `synthesis-raw/`, `pending.json`, `last_wake`, `imports.ndjson`,
    `evidence_memo.json`.
  - `data_dir()` (`jeeves_lib.py:54-60`) — `JEEVES_DATA_DIR` or
    `$XDG_DATA_HOME/jeeves`; holds `todo.md`.
  - `projects_root()` (`jeeves_lib.py:63-64`) — `JEEVES_PROJECTS_ROOT` or
    `~/.claude/projects`; session transcripts live under it.
- **Config + time**: `load_config()` (`jeeves_lib.py:67-77`, `CONFIG_DEFAULTS`
  at 18-33 merged with `state_dir()/config` key=value lines), `now_et()`/
  `today_et()` (America/New_York).
- **Logging/fail-fast**: `log()` appends timestamped lines to
  `state_dir()/collect.log` (`jeeves_lib.py:80-83`); `die()` logs FATAL and
  exits 1 (`jeeves_lib.py:86-90`).
- **Formatting/parsing helpers**: `denoise()` (structural transcript field
  selection, NUL stripping), `render_slice()`, `normalize()` (provenance
  stripping + NFKC casefold), `line_hash()` (sha256 of normalized line),
  `parse_github_slug()` (host-anchored owner/name), `read_delta()` (offset
  tracking with rotation detection), `parse_axi_message()` and
  `parse_fenced_json()` (model-output parsing).
- **State store**: `SeenStore` (`jeeves_lib.py:229-269`) — ndjson dedup ledger
  (`seen.ndjson`) with atomic tmp+replace save.
- **Dispatch**: `ferry()`/`_ferry_once()` (`jeeves_lib.py:437-548`) — taskferry
  dispatch with fallback-model circuit breaker, `--no-overlay` staging-dir
  mode, daemon-summarized-result rebuild via `task_log_message()`.

`bin/tail.py` uses only the read path (`projects_root`, `read_delta`,
`denoise`, `render_slice`, `load_config`, `die`); `bin/install-cron.py` uses
`state_dir` + `log`; `bin/collect.py` and `bin/todos.py` use nearly all of it.
