"# Task 2 Review — `jeeves coverage` (95bd97e)

## Verification run

`mise run check-rust` (env overrides: `RUSTUP_HOME=/tmp/opencode/rustup
CARGO_HOME=/tmp/opencode/cargo MISE_STATE_DIR=/tmp/opencode/mise-state
MISE_CACHE_DIR=/tmp/opencode/mise-cache`, required because the sandbox mounts
`/home/jeremy/.local/state/mise` and `/home/jeremy/.rustup` read-only):

- `cargo fmt --check`: clean
- `cargo clippy --all-targets -- -D warnings`: clean
- `cargo test`: 53 unit + 8 golden, all pass
- final `rc=0` — declared check command passes

## Focused checks (per task instructions)

### 1. Verdict strings byte-exact vs lib.sh / coverage-score
All verdict strings in `src/worktrees/coverage.rs` diffed against
lib.sh:180-248 and coverage-score:43-69 — byte-exact:
- `SCORED <n>` (coverage.rs:289, lib.sh:247)
- `UNSCORED binary` / `UNSCORED mode-only` (coverage.rs:194,197, lib.sh:169,170)
- `UNSCORED no-text-rows` (coverage.rs:252, lib.sh:195)
- `UNKNOWN no-merge-base` / `branch-diff-failed` / `merge-conflict` / `merge-tree-error` (coverage.rs:239,245,269,272 vs lib.sh:182,183,186,230,231)

### 2. Negative residual, clamping, empty-merge-tree guard
- Negative residual: `let abs_r = r.abs(); let num = o - abs_r; num.max(0)` then
  `pct = num*100/o` then `.min(100)` (coverage.rs:284-288) mirrors
  lib.sh:244-246 exactly; verified live with a deletion-heavy branch
  (`SCORED 0` on both ref and rust).
- Empty merge-tree with rc 0 must NOT score 100: coverage.rs:332-334 maps
  empty tree to rc 2 → `UNKNOWN merge-tree-error`, exactly the reference's
  `exit 2` in the subshell (lib.sh:216-221). Verified live: `main main`
  scores `UNSCORED no-text-rows` on both (O==0 short-circuits before merge-tree).

### 3. Usage errors: stdout, exit 2, exactly 3 positionals
- `--help` → usage text, exit 0; verified byte-equal vs ref `--help` output
- `-*` anywhere → `error: unknown flag <arg>` exit 2 (main.rs:108-113)
- `!=3` positionals → usage error exit 2 (main.rs:116-119)
- non-directory → `error: not a directory: <x>` exit 2 (main.rs:124-130)
- All verdicts exit 0 (main.rs:134-135)
- One divergence in edge behavior vs the reference: a *path-like* third
  positional is consumed by ref as `cd "$1"` (coverage-score:65) and errors
  `not a directory: coverage`, while rust treats a nonexistent second arg as
  a count mismatch (usage error). Same exit 2, stdout, and error family —
  cosmetic, not a contract violation.

### 4. Golden tests spawn the reference (no hardcoded expectations)
`tests/coverage_golden.rs` spawns `bash ref/coverage-score` with
`AUDIT_WORKTREES_LIB` resolved from `CARGO_MANIFEST_DIR`
(coverage_golden.rs:419-437); `assert_parity` compares the rust verdict string
to the live reference stdout (coverage_golden.rs:455-475). 7 fixtures +
3 usage-error assertions, all passing. Fixture builders set
`user.email`/`user.name` (coverage_golden.rs:406-407) — CI-safe.

### 5. FINDING (Critical): alternate objects-dir divergence in linked worktrees
Rust derives the alternate objects dir as `rev-parse --absolute-git-dir` +
`/objects` (`src/worktrees/coverage.rs:98-99`), but the reference uses
`rev-parse --path-format=absolute --git-path objects`
(`.superpowers/crispy/rust-rewrite/ref/lib.sh:203`). These agree in a normal
repo but **differ in a linked worktree**, where the gitdir is
`<mainrepo>/.git/worktrees/<name>` and `.git/worktrees/<name>/objects` does
not exist (objects live in `<mainrepo>/.git/objects`). Reproduced live:

```
$ AUDIT_WORKTREES_LIB=... bash ref/coverage-score /tmp/.../mainrepo/wt main feature
SCORED 100          (ref rc=0)
$ jeeves coverage /tmp/.../mainrepo/wt main feature
UNKNOWN merge-conflict   (rust rc=0)
```

Root cause: with the bogus `GIT_ALTERNATE_OBJECT_DIRECTORIES`, merge-tree
emits `error: object directory .../worktrees/wt/objects does not exist` and
fails with rc 1 — which Rust maps to `UNKNOWN merge-conflict` while the
reference, pointing at the real objects dir, succeeds. The scorer's name and
purpose is worktrees; this is a real parity break on the tool's own subject
matter. The golden suite does not cover it because every fixture passes the
main repo path.

## Verdict

- **Spec compliance:** fail
  - byte-exact verdict strings / exit codes: pass
  - negative-residual abs clamp, 0-100 clamp, no `>100`: pass
  - empty merge-tree + rc 0 not scoring 100: pass
  - usage errors stdout + exit 2, exactly 3 positionals, all verdicts exit 0: pass
  - golden tests vs reference spawn, fixture user identity: pass
  - linked-worktree parity (alternate objects dir resolution): **fail** —
    rust `UNKNOWN merge-conflict` vs ref `SCORED 100`
  - declared check command: pass (see verification run)

- **Task quality:** Needs fixes

- **Root cause:** execution-error — coverage.rs:98-99 resolves the objects
  dir via `rev-parse_abs_git_dir` + string concat `/objects`, diverging from
  lib.sh:203's `rev-parse --path-format=absolute --git-path objects`; the
  difference only shows in linked worktrees (gitdir
  `.git/worktrees/<name>` has no `objects/` child).

- **Critical:**
  - src/worktrees/coverage.rs:98-99 — use `git rev-parse
    --path-format=absolute --git-path objects` (mirror lib.sh:203) instead of
    `--absolute-git-dir` + `/objects`, so linked worktrees score identically
    to the reference.

- **Important:**
  - tests/coverage_golden.rs — add a fixture that runs the scorer from a
    linked worktree path (the tool's own domain); current 7 fixtures all pass
    the main repo path and cannot catch the divergence above.

- **Minor:**
  - src/main.rs:116-119 — for byte-identical edge behavior, ref's `cd "$1"`
    (coverage-score:65) makes a nonexistent first arg win over the count
    check; rust reports the count error first. Same exit 2 + error family;
    cosmetic.
"
