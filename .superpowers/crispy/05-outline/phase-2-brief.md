# Phase 2 — `jeeves coverage` (three-state scorer)

**Testable result:** `jeeves coverage <repo> <base> <branch>` reproduces
`ref/coverage-score` verdict-for-verdict on fixture repos: squash-onto-
advanced-base (SCORED 100 where ancestry says no), empty patch, binary/mode
rows, criss-cross, merge conflict, no-merge-base, negative-residual clamp,
empty-merge-tree fail-open guard. Exit 2 usage errors match.
**Files:** `src/worktrees/coverage.rs`, `tests/coverage_golden.rs`,
fixture builder ported from `tests/test_spec_coverage.py`.
**Checks:** golden parity test generates expected output from
`ref/coverage-score` + `ref/lib.sh` at test time (AUDIT_WORKTREES_LIB
pointed at `ref/lib.sh`).

