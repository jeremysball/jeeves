# Phase 4 — `jeeves git-state`, `roots`, `sessions`, `session-tail`, `checkin-lint`

**Testable result:** each subcommand byte-parity vs its ref/ script on
fixtures (git block, roots dedup by remote URL incl. worktree preference +
roots-file write and legacy fallback read, JSONL tail rendering via the
bundled sample transcript shapes, lint exit codes).
**Files:** `src/orient/{gitstate,roots,sessions,tail,lint}.rs`,
`tests/orient_small.rs`.
**Checks:** parity + a real `roots` run against this machine's /workspace
(dedup count asserted, not golden-text).

