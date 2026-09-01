# Phase 6 — scan content-merge pass + `install-cron` + `migrate`

**Testable result:** full scan (all three passes, closure rules, content:
N% details) parity vs ref with AUDIT_WORKTREES_LIB pointed at the *in-repo*
coverage (via the new `JEEVES_*` knobs, no legacy lib path);
`jeeves install-cron` writes the stable-dirs PATH line invoking the installed
binary; `jeeves migrate` prints the exact crontab/settings.json repoint
diff for the machine (does not apply it).
**Files:** `src/orient/scan.rs` (score pass), `src/digest/cron.rs`,
`src/migrate.rs`, tests.
**Checks:** scan parity incl. squash cases; cron line golden; migrate output
reviewed in the PR.

