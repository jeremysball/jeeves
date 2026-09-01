# Phase 3 — `jeeves audit`, `archive`, `clean`, `session-hook`

**Testable result:** audit bucket report byte-parity vs `ref/audit-worktrees.sh`
on fixture repos (in-flight hiding, silence contract, all buckets, dangling
registrations); archive/clean refusal-message parity incl. --strict and the
CAS-delete behavior, exercised on real temp repos; `jeeves session-hook`
stdin/stdout JSON parity vs `ref/session-hook.sh` incl. timeout mode.
**Files:** `src/worktrees/{audit,archive,clean,hook}.rs`, `tests/audit_golden.rs`,
`tests/safety_invariants.rs`.
**Checks:** parity tests + targeted destructive-safety tests (tag exists
before any branch delete; dirty strict refuses; `-d` not `-D`).

