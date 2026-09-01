# PRD — jeeves in Rust

slug: rust-rewrite · 2026-09-01 · owner: Jeremy (autonomous run, gates logged in DECISIONS.md)

## Problem

The personal-ops machinery is smattered across ~3,000 lines in three repos and
two languages: five Python entry points in jeeves, thirteen shell scripts and
one Python lint in orient / orient-quick / auditing-worktrees, glued together
by path-of-the-moment (`~/.claude/skills/<name>/bin/...`), env overrides whose
only callers are dying, and a CI that clones a second private repo at a pinned
sha just to test one binary. The seams are where the bugs live: the stale-clone
scan pollution fixed in #25, the cron PATH break, four consumer tables the
consolidation spec needed to enumerate by hand. The user's standing judgment,
2026-08-31: "a smattering of shell scripts is not the play."

## Success measure

All checkable on one machine after the migration:

1. `command -v jeeves` resolves from a stable PATH dir (`~/.local/bin`), and
   `jeeves --version` exits 0. Nothing in the runtime path references
   `~/.claude/skills/{orient,orient-quick,auditing-worktrees}/bin/`.
2. The hourly cron job runs the full collect pipeline through the single
   binary; one digest lands in `$XDG_STATE_HOME/jeeves/digests/` with the same
   shape as today's, and `~/.local/state/jeeves/collect.log` shows no new
   failure lines over 24h.
3. `mise run check` in the jeeves repo passes lint + typecheck + the whole
   test suite with **no sibling-repo fetch** — the real-boundary coverage
   tests exercise the in-repo binary.
4. The SessionStart hook fires from the installed binary, emits the same JSON
   contract, and adds ≤1.5s to session start.
5. orient, orient-quick, and auditing-worktrees exist only as skill *docs*
   calling `jeeves` subcommands (or are deleted), with zero scripts.
6. The git scan (`jeeves scan-active`) over this machine's ~15-repo workspace
   stays interactive-fast; measured, not assumed.

## Solution (product-level shape)

One artifact, one language: **a single Rust binary `jeeves`** that owns every
machine-scanning, worktree-auditing, digest-collecting, and todo-ledger
capability the four skills have today, exposed as subcommands grouped by the
four intents:

- **digest** (`collect`, `install-cron`): the backward-looking daily roll-up
  pipeline, including the taskferry synthesis leg.
- **orient** (`git-state`, `scan-active`, `roots`, `sessions`, `session-tail`):
  forward reorientation facts.
- **status** (`checkin-lint`): the quick-card gate.
- **worktrees** (`coverage`, `audit`, `archive`, `clean`, `session-hook`,
  `drain`): the drift audit and its safety machinery.

Behavior is ported, not redesigned: every output contract that a model or a
config file reads today (TOON tables, the `SCORED/UNSCORED/UNKNOWN` verdict
line, the hook JSON, `todo.md` provenance tags, state-file formats) is
preserved as-is, because consumers treat them as ground truth. The skill dirs
lose their `bin/` and keep only prose pointing at the binary.

Explicit non-goals: no new capabilities in this change (the drainable-counting
worktree-budget hook change rides the worktree-budget spec, which lands on the
current tooling first); no database state (files stay the store); no touching
what taskferry/opencode CLIs do internally — jeeves orchestrates them by
shell-out exactly as today. The four-decision update from the 2026-08-31
session (relaxed "local reads only" speed contract, no CI pin, no
`AUDIT_WORKTREES_BIN`) is folded in: the binary may shell out on any path as
long as the measured budgets hold, and the dead overrides die with the port.
