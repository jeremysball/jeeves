# SDD ledger — plan: .superpowers/crispy/rust-rewrite/05-outline.md

Preflight scan: Phases 2..6 share src/worktrees/coverage.rs and src/orient/scan.rs; sequencing fixed by outline order (coverage before scan content pass). Phase 4/6 roots-file default change is read-compatible (legacy fallback). Phase 7 normalize vectors must be generated from Python BEFORE Phase 9 deletion — ordering constraint respected. No task-internal contradictions found.
