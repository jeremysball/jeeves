---
title: jeeves in Rust — technical design
status: approved (autonomous run, see DECISIONS.md)
date: 2026-09-01
system_approved: true
program_approved: true
---

# TDD — jeeves in Rust

Inputs: `02-research.md` (facts), `03-prd.md` (shape). Reference
implementations in `ref/`. Approval was delegated ("I basically want it
autonomously done"); both gates recorded in `DECISIONS.md`, not by a human.

# Part 1 — System design

## Deployment artifact

One Rust binary, `jeeves`, installed to `~/.local/bin/jeeves` (already first
on the cron PATH; stable across tool upgrades). Built in-repo with cargo via
mise; `mise run install` copies the release binary to `~/.local/bin` (no
`cargo install` from a git URL — the repo checkout stays the source of truth
for cron). During transition the repo also carries the old `bin/*.py`; the
final phase deletes them.

## CLI surface (subcommand tree)

Every subcommand keeps its predecessor's argv and stdout contract where a
consumer exists (cron, settings.json, skills, the model reading TOON):

| jeeves subcommand | Replaces | Contract kept |
|---|---|---|
| `jeeves collect` | `bin/collect.py` | cron line shape changes only in its command word |
| `jeeves install-cron` | `bin/install-cron.py` | writes `13 * * * * PATH=… jeeves collect >> log 2>&1` |
| `jeeves todos <op>` | `bin/todos.py` | AXI/TOON output, ledger round-trip |
| `jeeves tail [dir] [--offset N]` | `bin/tail.py` | `[ts] role: text` lines |
| `jeeves scan-active <since> [root…]` | `orient/bin/scan-active.sh` | TOON report verbatim shape |
| `jeeves git-state [dir]` | `orient/bin/git-state.sh` | key: value block |
| `jeeves roots [root…]` | `orient/bin/discover-roots.sh` | TOON + roots-file side effect |
| `jeeves sessions [dir]` | `orient/bin/session-discover.sh` | `KEY=`VALUE lines |
| `jeeves session-tail <jsonl> [since] [max]` | `orient/bin/session-tail.sh` | one line per entry |
| `jeeves checkin-lint [file]` | `orient-quick/bin/lint-checkin.py` | bullet rules, exit 0/1 |
| `jeeves coverage <repo> <base> <branch>` | `auditing-worktrees/bin/coverage-score` | `SCORED/UNSCORED/UNKNOWN`, 0-only verdicts, exit 2 usage |
| `jeeves audit [--no-content] [root]` | `auditing-worktrees/bin/audit-worktrees.sh` | bucket report + silence contract |
| `jeeves archive <repo> <branch…>` / `--list` / `--strict` | `archive-branch.sh` | tag-before-destroy invariants, rc 1 on refusal |
| `jeeves clean <repo> <branch…>` | `clean-safe.sh` | re-verify-before-delete, `-d` not `-D` |
| `jeeves session-hook` | `session-hook.sh` | stdin `.cwd`, stdout hook-JSON or nothing, always rc 0 |

(`summary-parser.sh` is internal: folded into `audit`'s sweep mode. `lib.sh`
functions become a shared crate module.)

## Data contracts (unchanged formats, new producer)

- **State dir** `$JEEVES_STATE_HOME` > `JEEVES_STATE_DIR` >
  `$XDG_STATE_HOME/jeeves`: `offsets.tsv`, `seen.ndjson`, `imports.ndjson`,
  `pending.json`, `evidence_memo.json`, `tf-state.json`, `project-dirs.txt`,
  `git-state.md`, `digests/`, `summaries/`, `synthesis-raw/`, `staging/`,
  `last_wake`, `config`, `collect.log`, `collect.lock` — same file names and
  same formats (research Q5 table is the spec). Atomic `.tmp`+rename and
  non-blocking flock on `collect.lock` preserved.
- **Data dir**: `$JEEVES_DATA_DIR` > `$XDG_DATA_HOME/jeeves/todo.md` —
  hand-editable ledger, byte-compat round-trip mandatory.
- **Roots file**: new default `$XDG_STATE_HOME/jeeves/roots.txt`; readers
  fall back to the legacy `…/orient/roots.txt` when the jeeves file is absent
  (one release of tolerance, then the writer migrates it).
- **Hook contract**: stdin JSON, uses `.cwd` only; stdout
  `{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext}}` or
  nothing; settings.json repoints the SessionStart command to
  `jeeves session-hook` in the same PR.

## Config-knob surface (flag > env > config > default, per the triplet rule)

Single resolver in the core module. Every absorbed env knob gets a flag +
`JEEVES_*` env name + config-file key; the old names keep working as
deprecated aliases (external setters exist):
`WORKTREE_AUDIT_INFLIGHT_SECS`, `WORKTREE_AUDIT_ARCHAEOLOGY_SECS`,
`WORKTREE_AUDIT_ARCHIVE_PREFIX`, `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD`
(read by the *caller* of coverage, always — the comparison never moves into
the scorer), `WORKTREE_AUDIT_HOOK_TIMEOUT`, `ORIENT_ROOTS[_FILE]`,
`ORIENT_ROOT_CANDIDATES`, `ORIENT_COMMIT_LIMIT`, `ORIENT_CONTENT_SCORING`,
`ORIENT_OPENCODE_SCAN`, `SINCE`.
**Deleted for good** (per session decisions #6/#9): `AUDIT_WORKTREES_BIN`,
`AUDIT_WORKTREES_LIB` — no sibling dirs exist after absorption; CI drops the
pinned sibling fetch.

## External boundaries (shell-out, unchanged)

jeeves orchestrates and never reimplements: `git` (plumbing: merge-tree
`--write-tree`, numstat, for-each-ref, worktree porcelain), `fd` (repo
discovery), `taskferry` (digest synthesis; parse its TOON stdout exactly as
`jeeves_lib.py` does today), `gh-axi` (todos evidence), `opencode`/`kilo` CLI
(session discovery — the CLI-only rule stands; **no SQLite dependency**).

## Program design — crate layout

Single binary crate, lib layering under `src/`:

```
Cargo.toml            # jeeves 0.2.0; deps: clap(derive), serde, serde_json,
                      # dirs, fs2(flock), unicode-normalization+unicode-case-mapping,
                      # chrono(+timezone via iana), similar(golden diffs in tests only)
src/main.rs           # clap parse -> mod::run(args); exit-code mapper
src/core/             # config.rs (resolver), paths.rs (XDG dirs), toon.rs
                      # (toon_str, table emitter), git.rs (Command::new("git")
                      # wrappers: log/rev_list/merge_base/merge_tree_numstat),
                      # proc.rs (lock_status via /proc pid starttime), time.rs
                      # (activity_age_secs = max(commit, newest touched mtime), human_age)
src/worktrees/        # coverage.rs (three-state port of lib.sh:180-248),
                      # audit.rs (buckets + summary collapse), archive.rs,
                      # clean.rs, hook.rs
src/orient/           # scan.rs (three-pass classifier: ancestry, tip-tree
                      # match, content-score; transitive closure ×2, proved
                      # beats scored), gitstate.rs, roots.rs, sessions.rs,
                      # tail.rs, lint.rs
src/digest/           # ledger.rs (parse_ledger, normalize NFKC-casefold,
                      # line sha256, provenance tags), todos.rs (classify_evidence,
                      # pending/seen stores, caches), collect.rs (discover,
                      # read_delta offsets, denoise, staging, ferry orchestration,
                      # synthesis parse, digest render), cron.rs (install-cron)
```

Key signatures (representative):

```rust
pub enum Verdict { Scored(u8), Unscored(&'static str), Unknown(UnknownWhy) }
pub fn coverage(repo:&Path, base:&str, branch:&str) -> Result<Verdict>  // never errs on UNSCORED/UNKNOWN
pub fn classify_branch(repo:&Path, branch:&Branch) -> Classification    // merged|content-merged|potentially-outstanding + state string
pub struct Ledger; impl Ledger { pub fn normalize(s:&str)->String; pub fn line_hash(s:&str)->String }
pub fn toon_table(rows:&[RepoRow]) -> String   // exact scan-active shape
```

Error policy per CLI (matches today's exit families): 0 success (including
"declined to judge" verdicts and nothing-to-report hook runs), 1
refusal/verification failure (archive/clean/checkin-lint), 2 usage/flag error
(coverage/scan/roots). `anyhow` at the binary edge, `thiserror` inside
modules; fail-fast — a malformed env knob warns on stderr and falls back to
the default exactly where today's scripts do (never silently different).

## Test boundaries (the real-boundary rule survives the port)

- **Golden parity tests**: each Rust subcommand's output diffed against the
  reference script's output on fixture git repos built in `tempfile` — the
  fixture builders are ported from `tests/` (squash-onto-advanced-base shape,
  criss-cross, dirty worktrees, locks). Golden files committed under
  `tests/golden/`.
- **`CARGO_BIN_EXE_jeeves`** integration tests spawn the *real compiled
  binary* — the successor of `needs_real_cli`: it can never silently skip,
  since the binary is in-repo.
- **Ledger byte-compat**: `normalize`/`line_hash` pinned with a table of
  Python-generated vectors (sha256 of real `todo.md` lines produced by the
  current `bin/todos.py` before deletion) so dedupe semantics don't drift.
- CI: `cargo fmt --check`, `clippy -D warnings`, `cargo test`; the sibling
  fetch and `JEEVES_CI_PAT` usage deleted; Python legs kept only while
  `bin/*.py` still exist, removed with them in the last phase.

## Rollback

Same stance as the superseded consolidation spec, hardened by the language
break: old scripts stay installed and untouched until the new binary is
verified end-to-end (cron run, hook start, a manual digest). The settings.json
and crontab repoints are one-line reverts. Skill dirs are archived (tarball
or keep-until-green), never bare-rm'd. If the Rust port stalls mid-way, the
repo ships both trees and nothing on the machine has moved.

# Part 2 — Program design notes (module → port-source map)

| Rust module | Ports | Notes |
|---|---|---|
| `core/git.rs` | scattered `git` calls | unit-sep `%x1f` formats; porcelain parsers must survive spaces in paths |
| `worktrees/coverage.rs` | `ref/lib.sh:180-248` + `coverage-score` | replicate the edge fixes verbatim: empty merge-tree must not fail open; `absR` clamp; verdict strings exact |
| `worktrees/{archive,clean}.rs` | `ref/archive-branch.sh`, `clean-safe.sh` | tag-before-destroy, update-ref --stdin CAS (shell out: `git update-ref --stdin`), `-d` not `-D` |
| `orient/scan.rs` | `ref/scan-active.sh` | fd discovery, ORIENT_* knobs, alerts count, help[n] lines byte-exact (model-facing) |
| `digest/ledger.rs` | `todos.py:56-148` | normalize(): strip stacked provenance tags, NFKC casefold, bullet lstrip, ws collapse, sha256 |
| `digest/todos.rs` | `todos.py` remainder | classify_evidence three-source (commit/PR/issue), memo TTL 300s, caches |
| `digest/collect.rs` | `collect.py` (1045 lines) | the big one: offsets delta reads, denoise field set, staging dirs, taskferry list/dispatch/wait/result TOON parsing, synthesis field checks (`session` must equal sid), prune rules |
| `digest/cron.rs` | `install-cron.py` | stable-dirs PATH builder verbatim; replace `python3 collect.py` with the binary path |
| `main.rs` | argv glue | old script names NOT aliased; consumers repoint (this repo's SKILL.md, dotclaude addenda, settings.json, crontab) |
