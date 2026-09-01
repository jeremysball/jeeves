# Research questions — jeeves Rust rewrite

Source request: Jeremy, 2026-08-31 (kilo session `ses_fa5c44a03ffe`, continuation):
re-architecture jeeves; "a smattering of shell scripts is not the play" → rewrite
jeeves in Rust, absorbing the orient / orient-quick / auditing-worktrees machinery
(spec: `.superpowers/specs/2026-08-31-jeeves-absorbs-orient-family.md`, to be
superseded on the language choice). Reference copies of every script being absorbed
live in `.superpowers/crispy/rust-rewrite/ref/`.

Each question names the design choice it unblocks. Answers go to `02-research.md`
(one ferry per question, `researching-the-codebase`).

## Q1 — CLI surface and output contracts of every absorbed binary

For each script in `ref/` (scan-active.sh, git-state.sh, session-discover.sh,
session-tail.sh, discover-roots.sh, lint-checkin.py, coverage-score,
audit-worktrees.sh, archive-branch.sh, clean-safe.sh, lib.sh, session-hook.sh,
summary-parser.sh): exact invocations (flags, positional args), env vars read
(with defaults), exit-code semantics, and stdout format. Especially the
three-state contract `SCORED|UNSCORED|UNKNOWN` of coverage-score and the record
shape scan-active.sh emits.
**Unblocks:** the Rust CLI subcommand tree and whether output formats are
byte-compatible or consumers get repointed.

## Q2 — How `bin/collect.py` consumes scan-active.sh and discover-roots.sh

Parse shape (delimiters, fields), paths it invokes today, the refresh-roots call
added in #25, digest state dirs it writes and their format.
**Unblocks:** which of collect.py's responsibilities move into the Rust binary
versus stay Python; parsing contract to replicate.

## Q3 — Cron installation and PATH contract

What `bin/install-cron.py` writes (line, schedule, env), and which directories
the installed command relies on being on PATH (mise shims, `~/.local/bin`).
Cite the cron-PATH fix from jeeves#23 if visible in the file.
**Unblocks:** where the Rust binary must install so the hourly job keeps working
(mise `tools` + bin dir vs cargo-install vs hand-placed symlink).

## Q4 — session-hook.sh contract

Read `~/.claude/settings.json` (bind it in or cite the copy's expectations if
inaccessible) and ref/session-hook.sh: the exact command string, stdin payload
shape, and what the hook writes.
**Unblocks:** whether settings.json must change when the hook becomes a Rust
subcommand, and its I/O contract.

## Q5 — On-disk state and input files jeeves and the scripts touch

Enumerate: XDG state dirs (jeeves ledger/todos/digest), Claude session JSONLs,
opencode/kilo SQLite DBs and the CLI-only access rule, taskferry state dirs.
For each: producer, consumer, format, and whether the absorbed code reads or
writes it.
**Unblocks:** what the Rust implementation must parse natively (JSONL streaming,
SQLite access strategy) — the biggest feasibility risk in a Python→Rust port.

## Q6 — Threshold and override logic in todos.py and the absorbed code

`WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD` (where read, default), `AUDIT_WORKTREES_BIN`
and `AUDIT_WORKTREES_LIB` (who sets, who reads), coverage-score's own exit behavior
and error paths.
**Unblocks:** the config-knob surface of the Rust tool (flag > env > config-key
> default per the triplet rule) and confirms which overrides die in the rewrite.

## Q7 — Test harness patterns and external references to these binaries

In jeeves `tests/`: how `needs_real_cli` real-boundary tests are structured
(fixture vs spawned binary). Then grep the visible checkout for references to
`coverage-score`, `scan-active.sh`, `archive-branch.sh`, `session-hook.sh`
outside jeeves (SKILL.md files, addenda, the consolidation spec).
**Unblocks:** the Rust test strategy (must keep exercising the real binary) and
the full consumer list that a rename/repoint PR has to touch.
