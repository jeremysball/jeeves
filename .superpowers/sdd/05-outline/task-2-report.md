"# Task 2 Report — `jeeves coverage` (three-state scorer)

Commit: `4717836 feat(worktrees): add coverage scorer with golden parity vs reference`

## Fixture verdicts (rust vs reference, both quoted)

| # | Fixture | Rust verdict | Reference verdict | Match |
|---|---------|--------------|-------------------|-------|
| a | squash-merged-onto-advanced-base | `"SCORED 100"` | `"SCORED 100"` | yes |
| b | genuinely unmerged feature branch | `"SCORED 0"` | `"SCORED 0"` | yes |
| c | empty patch branch | `"UNSCORED no-text-rows"` | `"UNSCORED no-text-rows"` | yes |
| d | binary file added | `"UNSCORED binary"` | `"UNSCORED binary"` | yes |
| e | chmod-only | `"UNSCORED mode-only"` | `"UNSCORED mode-only"` | yes |
| f | unrelated history | `"UNKNOWN no-merge-base"` | `"UNKNOWN no-merge-base"` | yes |
| g | conflicting merge | `"UNKNOWN merge-conflict"` | `"UNKNOWN merge-conflict"` | yes |

Bonus fixtures from the brief (verified manually, not in the golden suite):
- criss-cross history: rust `"UNKNOWN no-merge-base"` == ref `"UNKNOWN no-merge-base"`
- negative-residual clamp: rust `"SCORED 88"` == ref `"SCORED 88"` (a branch whose deletion never shipped must not score >100)

Usage errors (stdout, exit 2), all matching ref/coverage-score wording:
- `error: unknown flag --bogus`
- `error: usage: coverage-score <repo> <base> <branch>` (legacy binary name in the usage string, as published)
- `error: not a directory: /nonexistent`

## Implementation notes

- `src/worktrees/coverage.rs` — port of `coverage_score` + `numstat_net` + merge-base use from ref/lib.sh:149-248. Verdict strings are exact; the empty-merge-tree-with-rc-0 guard (lib.sh:216-221) is replicated: an empty tree exits the subshell with rc 2, surfacing as `UNKNOWN merge-tree-error` instead of failing open to `SCORED 100`. R negative uses abs (lib.sh:244-246). Criss-cross (>1 merge base) maps to `UNKNOWN no-merge-base` exactly as the reference's `merge_base ||` does (lib.sh:182).
- `src/core/git.rs` — added `rev_parse` (mirrors lib.sh:205).
- `src/main.rs` — `jeeves coverage` parses its own args (clap's error rendering would not reproduce the published strings): `--help` -> usage exit 0, any `-*` -> `error: unknown flag <x>` exit 2, count != 3 -> usage error exit 2, non-directory -> `error: not a directory: <x>` exit 2. All verdicts exit 0.
- `tests/coverage_golden.rs` — 7 fixture repos built in tempfile (local user.email/name set), each asserting the Rust verdict string equals the reference stdout. The reference is spawned at test time: `bash .superpowers/crispy/rust-rewrite/ref/coverage-score <repo> <base> <branch>` with `AUDIT_WORKTREES_LIB` resolved from `CARGO_MANIFEST_DIR` (no hardcoded absolute path). Plus a usage-errors test (3 assertions).

## Command outputs

`mise run check-rust` (env overrides for the sandbox's read-only state dirs:
`RUSTUP_HOME=/tmp/opencode/rustup CARGO_HOME=/tmp/opencode/cargo MISE_STATE_DIR=/tmp/opencode/mise-state MISE_CACHE_DIR=/tmp/opencode/mise-cache`):

```
[lint] $ cargo clippy --all-targets -- -D warnings
test result: ok. 53 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.02s
test result: ok. 8 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.14s
rc=0
```

- `cargo fmt --check`: clean (one formatting fix applied via `cargo fmt` before the final run).
- `cargo clippy --all-targets -- -D warnings`: clean.
- `cargo test`: 53 unit + 8 golden = 61 passed, 0 failed.

Note: `mise run check-rust` fails out of the box in this sandbox because
`/home/jeremy/.local/state/mise` and `/home/jeremy/.rustup` are read-only
mounts; the env overrides above redirect mise's state/cache and the rustup
toolchain to `/tmp/opencode`, after which the declared check command runs
unmodified and passes.
"
