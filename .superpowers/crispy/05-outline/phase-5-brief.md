# Phase 5 — `jeeves scan-active` ancestry+tree passes

**Testable result:** scan with `ORIENT_CONTENT_SCORING=0` matches
`ref/scan-active.sh` output on fixture repos (count line, repos table,
branch classification merged/potentially-outstanding, DIVERGED/unpushed/
behind/no-upstream state strings, alerts count, help lines, zero-active case).
**Files:** `src/orient/scan.rs`, `tests/scan_ancestry.rs`.
**Checks:** parity with content scoring disabled; fd-missing error path.

