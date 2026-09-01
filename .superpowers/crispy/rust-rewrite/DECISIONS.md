# Decision log — autonomous CRISPY run (rust-rewrite)

Jeremy delegated the run on 2026-09-01: "lets re-write jeeves in rust actually
I want CRISPY run over this. I basically want it autonomously done though."
The CRISPY approval gates would normally stop for a human; under delegation
they are recorded here instead, each with the reasoning and what to read to
overturn it.

## D-A: Target language is Rust (user, explicit)
Supersedes the 2026-08-31 consolidation spec
(`.superpowers/specs/2026-08-31-jeeves-absorbs-orient-family.md`, whose
"absorb the .sh binaries verbatim" design is replaced by a port). Absorption
scope, consumer table, and rollback stance are carried over from it.

## D-B: PRD gate approved without a human (this session)
`03-prd.md`. Success measures are the machine-checkable list in it. Overturn
by editing the PRD; phases derive from it.

## D-C: TDD system gate approved without a human (this session)
`04-tdd.md` Part 1. Load-bearing calls made under delegation:
- Single binary in `~/.local/bin` (stable PATH dir; cron + hook already
  resolve there) — research Q3, Q4.
- All output contracts kept byte-compatible (TOON tables, verdict lines,
  hook JSON, ledger format) — research Q1, Q2, Q5. A rename of any
  model-facing string needs a consumer-repoint PR in dotclaude.
- No rusqlite: opencode/kilo stay CLI-only shells per the documented rule —
  research Q5 §4.
- Old env names kept as deprecated aliases; `AUDIT_WORKTREES_BIN` /
  `AUDIT_WORKTREES_LIB` deleted — session decisions #6/#9 from
  `ses_fa5c44a03ffe`.

## D-D: TDD program gate approved without a human (this session)
`04-tdd.md` Part 2. Crate layout and module→port-source map. Riskiest port
called out explicitly: `digest/collect.rs` (taskferry TOON parsing + staging
+ field checks) and `normalize()`/`line_hash()` byte-compat; both are pinned
by golden/vector tests before the Python tree is deleted.

## D-E: Sequencing keeps the worktree-budget spec first
The drainable-counting hook (dotfiles) lands against the *current*
auditing-worktrees `coverage-score` before deletion of that skill, per the
consolidation spec's sequencing. This rewrite does not block on it; the hook
repoints to `jeeves coverage` later.

## D-F: Scope of this PR = jeeves repo only
Machine repoints (crontab, settings.json) ship as a documented
`jeeves migrate` checklist + this repo's SKILL.md update; the dotclaude
skill-doc folding and the deletion of orient/orient-quick/auditing-worktrees
are follow-up PRs, gated on the migrated cron/hook running green (rollback
section of the TDD).
