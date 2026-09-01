"# Task-Scoped Review: Phase 3

## Scope and Method

Reviewed the Phase 3 brief, the supplied `7ee2ad3..79364af` diff, the five
named reference scripts, and the current audit/core/test implementation. The
diff was read once. No repository files, commits, or staging state were
modified. Focused reference runs covered audit dangling registrations and
`--no-content` placement, archive output, clean usage, hook JSON/timeout
behavior, linked-worktree behavior, and hook state-directory behavior.

## Findings

### Critical

- None. The archive implementation creates the tag before worktree removal,
  uses conditional `git update-ref --stdin` deletion, and the clean path uses
  worktree removal before `git branch -d`; no destructive ordering deviation
  was found in the implementation.

### Important

- **Hook is not fail-closed and not read-only.** `hook.rs:50-52` resolves audit
  options outside an error boundary. That calls `audit.rs:35`,
  `config.rs:65-68`, and `paths.rs:17-21`, which creates/chmods the state
  directory and panics if it cannot be created. With
  `JEEVES_STATE_DIR=/dev/null/jeeves-hook-state`, the binary exited 101 rather
  than 0. With a valid absent state path and a non-repository `.cwd`, Rust
  created the state directory while the reference remained silent and did not
  create it. This violates the hook's always-zero and never-mutates contract.

- **Clean accepts a missing branch list as a successful no-op.** The Clap
  declaration at `src/main.rs:30-33` does not make the `Vec` required, and
  `clean_branches` at `src/worktrees/clean.rs:13-33` has no empty-input guard.
  `jeeves clean <repo>` returned 0 after pruning; the reference returns 1 and
  the exact usage message. This is an observable CLI/refusal contract gap.

- **Archive output is not reference-compatible.** `archive.rs:366-374`
  redirects `git update-ref --stdin` stdout to `/dev/null`. The reference does
  not redirect it, and on this Git version emits `start: ok`, `prepare: ok`,
  and `commit: ok` for each transaction. A successful strict archive therefore
  has seven output lines in the reference but only the final archived line in
  Rust; the non-strict path has the same missing delete-transaction lines.
  The existing safety test hardcodes the Rust-only output instead of checking
  the reference.

- **Not all Phase 3 knobs use the required canonical/legacy core resolver.**
  `audit.rs:55-60` resolves the content threshold using only
  `WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD`, so the canonical
  `JEEVES_AUDIT_CONTENT_MERGE_THRESHOLD` name is ignored. `hook.rs:96-104`
  manually scans timeout environment variables and never reads the config file
  through `core::config::resolve`. This violates the triplet/config rule even
  though the in-flight and archaeology settings do use the resolver.

- **Hook repo mode diverges on linked worktrees.** `hook.rs:42-46` selects
  `AuditMode::Repo`, and `hook.rs:114-116` calls `audit_repo` directly. The
  reference always invokes `audit-worktrees.sh` with the root; its discovery
  uses `fd -t d '^\\.git$'`, so a linked-worktree root whose `.git` is a file
  produces no report. On this linked checkout, the reference emitted nothing
  while Rust emitted a report. The existing hook tests only use ordinary
  repositories and do not expose this parity difference.

- **Required parity/safety cases are not covered by the tests.**
  `tests/audit_golden.rs:125-294` has five reference-spawned cases, but no
  actual all-bucket fixture, lock-status coverage, dangling-registration
  golden case, or `--no-content` position case. `tests/hook_contract.rs:43-98`
  never spawns `ref/session-hook.sh` and has no timeout-mode test. The archive
  and clean tests in `tests/safety_invariants.rs:80-221` do not compare the
  reference, test missing-branch rc preservation, observe tag-before-destroy
  ordering, or exercise the CAS-delete race. Passing final-state assertions
  cannot establish those required invariants.

### Minor

- **Hook JSON differs byte-for-byte from the reference.** `hook.rs:162-164`
  emits compact JSON via `serde_json::to_string`, while `ref/session-hook.sh`
  emits jq's pretty-printed JSON. The parsed object and fields are equivalent,
  but the brief calls for JSON parity and the test at
  `tests/hook_contract.rs:76-80` asserts the compact Rust format rather than
  comparing the reference output.

- **Zero timeout semantics differ.** `hook.rs:107-110` treats timeout `0` as
  an immediate skip. GNU `timeout 0`, used by the reference at
  `ref/session-hook.sh:46`, disables the timeout; a quick focused run produced
  the normal report from the reference and the timeout JSON from Rust. The
  behavior should be defined or matched.

- **A hard-coded absolute fixture path remains.**
  `tests/coverage_golden.rs:324` uses `/nonexistent`, contrary to the task-wide
  no-hardcoded-absolute-paths-in-tests constraint. It is outside the supplied
  five-commit diff but remains in this checkout.

## Reference Checks

- Audit dangling-registration output matched the reference byte-for-byte.
- Audit accepted `--no-content` after the root and ignored an extra positional,
  matching the reference.
- Clean unmerged-refusal stderr and rc matched the reference.
- Strict and non-strict archive safety behavior preserved the branch/tag and
  worktree rules, but successful archive stdout omitted the reference's
  `update-ref` transaction status lines.
- Normal-repository hook JSON had the correct object and context, but differed
  in whitespace from jq output.
- Hook timeout `0`, linked-worktree repo mode, and invalid state-directory
  handling showed the mismatches described above.

## Verification

The declared check command passed:

`cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test`

All five test suites passed: 62 unit tests, 5 audit golden tests, 9 coverage
golden tests, 2 hook tests, and 6 safety tests.

## Verdict

**Spec compliance:** fail. Destructive ordering is sound, and the covered
audit cases pass, but hook exit/read-only behavior, clean usage behavior,
archive output parity, resolver coverage, and required parity tests have gaps.

**Task quality:** Needs fixes.

**Root cause:** execution-error + `src/worktrees/hook.rs:50-52`,
`src/main.rs:30-33`, and incomplete reference-boundary tests at
`tests/hook_contract.rs:43-98` / `tests/safety_invariants.rs:80-221`.

**Status:** Review complete; report written. Worktree unchanged except for the
supplied untracked diff input.
"
