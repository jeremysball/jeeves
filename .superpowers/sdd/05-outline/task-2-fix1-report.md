# Task 2 Fix Round 1 — linked-worktree objects dir (jeeves coverage)

Commit: `e5e062f fix(coverage): resolve objects dir via --git-path for linked worktrees`

## Fix summary

**Finding 1 (critical, `src/worktrees/coverage.rs:98-99`):** the alternate objects
dir passed to `merge-tree`/`diff` was resolved as
`git rev-parse --absolute-git-dir` + `"/objects"`. In a *linked worktree*,
`--absolute-git-dir` returns the per-worktree gitdir
(`<main>/.git/worktrees/<name>`), which contains no `objects/` directory —
only `commondir`, `gitdir`, `HEAD`, `index`, `logs`, `ORIG_HEAD`, `refs`.
`GIT_ALTERNATE_OBJECT_DIRECTORIES` then pointed at a nonexistent dir,
`merge-tree --write-tree` failed (no objects found), the rc landed in the
`>1` error band, and the tool printed `UNKNOWN merge-conflict` where the
reference prints a score.

**Fix:** mirror `ref/lib.sh:203-204` exactly. New helper
`git::objects_dir` (`src/core/git.rs`) runs
`git rev-parse --path-format=absolute --git-path objects`, which resolves the
real object store even from a linked worktree; a failed or empty read maps to
`UNKNOWN no-object-dir` (the temp dir is cleaned up first), keeping the
reference's `2>/dev/null` + `[ -n "$alt" ] || echo "UNKNOWN no-object-dir"`
path in Rust. The now-unused `--absolute-git-dir` call is gone from
coverage.rs. Module doc comment updated to document the new verdict.

**Finding 2 (tests/coverage_golden.rs):** added golden test
`linked_worktree_path_scores_like_main_repo`: main repo with a squashed-merged
branch plus an extra commit, `feat` branched from init with genuine work, then
`git worktree add -q <dir>/wt feat`. Asserts Rust verdict == reference verdict
(spawns `bash ref/coverage-score` with `AUDIT_WORKTREES_LIB`, exactly as the
existing golden tests) scored from **both** the main repo path and the linked
worktree path, plus a hard assertion of `SCORED 0` from the worktree path.

Nothing else changed.

## Controller repro — before

Repo `/tmp/covchk` rebuilt per spec: main with squash-merged branch + extra
commit, linked worktree at `/tmp/covchk/wt` on `feat`.

```
$ bash ref/coverage-score /tmp/covchk/wt main feat
SCORED 0
$ target/debug/jeeves coverage /tmp/covchk/wt main feat
UNKNOWN merge-conflict
```

Root cause visible: `/tmp/covchk/.git/worktrees/wt` has no `objects/`.

## Controller repro — after

```
$ target/debug/jeeves coverage /tmp/covchk/wt main feat
SCORED 0
$ bash ref/coverage-score /tmp/covchk/wt main feat
SCORED 0
```

Both print `SCORED 0`.

## Command outputs

`mise run check-rust` (the repo's declared check) — exit 0, all green:

```
[check-rust] $ mise run fmt-check && mise run lint && mise run test
[fmt-check] $ cargo fmt --check
[lint] $ cargo clippy --all-targets -- -D warnings
[test] $ cargo test
   ... unit tests ...
test result: ok. 53 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
   ... tests/coverage_golden.rs (9 tests incl. new linked-worktree test) ...
test result: ok. 9 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
```

Note: `mise run check-rust` needed writable rustup/cargo/mise dirs (the
container's `~/.rustup`, `~/.cargo/bin`, `~/.local/state/mise`, and
`~/.cache/mise` are read-only mounts). It was run with
`RUSTUP_HOME=/tmp/opencode/rustup CARGO_HOME=/tmp/opencode/cargo
MISE_STATE_DIR=/tmp/opencode/mise-data/state MISE_DATA_DIR=/tmp/opencode/mise-data
MISE_CACHE_DIR=/tmp/opencode/mise-cache` using the installed 1.91.1 toolchain.
Final exit code 0.
