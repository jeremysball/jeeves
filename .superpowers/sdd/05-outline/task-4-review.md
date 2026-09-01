"# Task 4 Review - Phase 4

## Scope and Method

Reviewed `phase-4-brief.md`, the supplied `15b586c..ae56c21` review diff once,
the five named reference scripts, the current five command implementations, and
the orient tests. No repository source files were modified and no subagents were
used.

Focused checks covered basic parity, inaccessible git directories, session-tail
argument and JSON edge cases, roots environment precedence and URL variants, a
real `/workspace` roots scan, session symlink resolution, and lint summary
counts.

## Verification

The first run of the declared command failed during dependency compilation with
`No space left on device` because the workspace overlay had only 316K free. The
same check was then rerun with build artifacts directed to the writable `/tmp`
filesystem:

`CARGO_TARGET_DIR=/tmp/opencode/jeeves-target CARGO_INCREMENTAL=0 cargo fmt --check && CARGO_TARGET_DIR=/tmp/opencode/jeeves-target CARGO_INCREMENTAL=0 cargo clippy --all-targets -- -D warnings && CARGO_TARGET_DIR=/tmp/opencode/jeeves-target CARGO_INCREMENTAL=0 cargo test`

The rerun passed: formatting clean, clippy clean with `-D warnings`, and 97
tests passed with no failures. The git worktree remained clean.

## Findings

### Important

1. **Roots environment migration is incorrect.** `src/orient/roots.rs:40-43`
   reads only `JEEVES_ROOTS_FILE`, so the legacy `ORIENT_ROOTS_FILE` alias is
   ignored. `src/orient/roots.rs:137-143` checks `ORIENT_ROOT_CANDIDATES`
   before `JEEVES_ROOT_CANDIDATES`, reversing the required canonical-first
   precedence. A focused run with both candidate variables selected the legacy
   directory; a run with only `ORIENT_ROOTS_FILE` wrote the canonical default
   instead of the requested legacy path. The help text at `:125-134` also
   advertises only the legacy names. Resolve canonical first, then alias, for
   both variables and document both names consistently.

2. **Roots traversal does not reproduce the reference scan behavior.**
   `src/orient/roots.rs:165-188` implements a custom `read_dir` walk and does
   not apply the ignore behavior of the reference `fd` invocation. On the
   required real `/workspace` check, the reference produced 101 roots while
   Rust produced 104. The three Rust-only roots were
   `/workspace/jeremysball-analysis-claude/repos/dotfiles`,
   `/workspace/programming-music/cmi-bench`, and
   `/workspace/programming-music/strudel`. This changes the persisted roots
   file and the model-facing count. Match the reference traversal semantics
   while retaining the required primary-checkout/worktree selection behavior.

3. **`git-state` misclassifies a failed directory change as a non-git case.**
   `src/orient/gitstate.rs:17-26` checks only `Path::is_dir()` and then treats
   a failed `current_dir` on the git subprocess as a failed git probe. For a
   directory with no execute permission, the reference returns rc 1 and the
   exact `error: cannot cd to ...` stdout, while Rust returns rc 0 with the
   non-git two-line response. Perform a real cd-equivalent validation before
   probing git, while preserving the existing missing-path message.

4. **`sessions` canonicalizes symlinks and changes the lookup key.**
   `src/orient/sessions.rs:36-43` uses `fs::canonicalize` for an existing
   directory. The reference uses shell `cd` followed by logical `pwd`, which
   preserves a supplied symlink path. In a focused fixture, the reference
   emitted no Claude session for the symlink argument, while Rust emitted the
   real-target session path. This can select a different project log or miss
   one entirely; preserve the reference directory spelling for slug lookup.

5. **`session-tail` is only a partial jq emulation.** `src/orient/tail.rs:47-93`
   does not reproduce jq's `// []` truthiness or join behavior. For a bullet
   record whose content is `false`, the reference omits the record while Rust
   prints `false`; for a text part whose value is an object, the reference
   skips the jq-erroring record while Rust serializes the object. A malformed
   JSON line also leaves the reference with a nonzero jq/pipeline status while
   Rust silently continues with rc 0. In addition, the reference prints
   `(jq unavailable; cannot parse session)` when jq is absent (`session-tail.sh`
   `:14`), while Rust always parses with serde_json. Either reproduce these
   observable cases or define and test a deliberate replacement contract.

6. **`session-tail` does not preserve the reference max-argument contract.**
   `src/orient/tail.rs:19-25` rejects GNU `tail`'s `+N` form, and
   `src/orient/tail.rs:113-121` interprets a negative value as dropping the
   first N entries rather than keeping the last N entries. With six entries,
   `-2` returned rows 3-6 in Rust versus rows 5-6 in the reference; `+2`
   returned rows 2-6 in the reference but rc 1 and no output in Rust. The
   no-argument path at `:7-10` also prints usage on stdout, whereas the
   reference emits no stdout and reports its usage diagnostic on stderr.

7. **`checkin-lint` counts violations, not failed lines.**
   `src/orient/lint.rs:28-37` increments the summary counter inside the
   per-problem loop, so one bullet violating length, comma, and bold rules is
   reported as `3 violation(s) found.` The binding requirement says the summary
   must count lines, which would be 1 while retaining one diagnostic per rule.
   The supplied `lint-checkin.py:52-63` currently has the same problem-counting
   behavior, so the stated line-count rule and the supplied stdout reference
   conflict. The contract needs a ruling/reference update, and the Rust code
   must follow the ruling rather than silently inheriting the reference bug.

8. **The roots implementation still hardcodes `/workspace`.**
   `src/orient/roots.rs:126` embeds it in help output and `:142` embeds it in
   the fallback candidate list. This violates the explicit no-hardcoded
   `/home` or `/workspace` constraint and makes the default behavior host-
   specific. Derive the default from the configured environment or a portable
   runtime location and update the help contract accordingly.

### Minor

9. **The parity tests miss the binding edge cases and mask the roots bug.**
   `tests/orient_golden.rs:232-249` runs the reference with legacy environment
   variables but Rust with a canonical roots-file variable, then
   `:317-335` normalizes dynamic output; it cannot detect the missing legacy
   alias or canonical-first precedence. The sessions test disables the
   OpenCode path when the executable is present (`:461-471`), and there are no
   tests for symlink directories, permission-denied cd, jq absence, malformed
   JSON, `+N`/negative tail limits, or line-count summaries. The brief names
   `tests/orient_small.rs`, but the coverage is instead appended to
   `tests/orient_golden.rs`. The reference helper itself is correctly resolved
   from `CARGO_MANIFEST_DIR` at `:59-63`.

## Reference Checks That Passed

- Basic clean, dirty, non-git, and missing-path `git-state` cases matched.
- The standard roots deduplication fixture and pre-existing legacy mirror
  matched; canonical default persistence used `XDG_STATE_HOME/jeeves/roots.txt`.
- HTTPS, HTTP, SSH, and scp-style remote URL variants normalized identically to
  the reference.
- Covered `session-tail`, `sessions`, and `checkin-lint` happy-path fixtures
  matched byte-for-byte, including sessions' found-only `KEY=VALUE` output and
  rc 0 behavior.
- All current tests spawn the named reference scripts through
  `CARGO_MANIFEST_DIR` where applicable.

## Verdict

**Spec compliance:** fail. Happy-path parity, URL normalization, canonical
default persistence, pre-existing legacy mirroring, and the covered sessions
exit/output contract pass. Canonical/legacy roots resolution, portable roots
defaults, real-root traversal parity, git cd-failure handling, session path
normalization, session-tail edge semantics, and the stated lint line-count
contract remain open.

**Task quality:** Needs fixes

**Root cause:** `execution-error` + `contract-drift` + `test-gap` — shell
behavior was manually reimplemented in several places without preserving edge
semantics, migration precedence was not applied consistently, and the golden
fixtures exercise only the happy paths. There is also a
`spec-reference-conflict` for the lint summary count.

**Status:** Review complete; report written. Repository git worktree clean.
"
