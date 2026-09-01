"# Task 1 review — Phase 1: Rust scaffold and shared core

Base `fe4daad`, head `dbe7a93`. Diff regenerated locally (`git diff fe4daad..dbe7a93`, 2093 lines) because `.superpowers/sdd/05-outline/task-1-report.md` and the `.diff` file named in the review instructions do not exist in the repo (only `progress.md` is present under `.superpowers/sdd/`). The implementer's claims were therefore reviewed against the code itself.

## Verification (required)

`mise run check-rust` cannot execute in this sandbox: the root filesystem is mounted read-only, so mise fails before running anything — `mise ERROR failed to ln -sf ... /home/jeremy/.local/state/mise/trusted-configs/...: Read-only file system (os error 30)`, and with state/cache redirected it still fails on rustup's temp dir (`could not create temp file /home/jeremy/.rustup/tmp/...: Read-only file system`). This is an environment limitation, not a repo defect. The pinned toolchain (rust 1.91.1, matching `.mise.toml`) is already installed and on PATH, so I ran the three steps of `check-rust` directly:

- `cargo fmt --check` → exit 0
- `cargo clippy --all-targets -- -D warnings` → exit 0, no warnings
- `cargo test` → 47 passed, 0 failed

All constituent steps of the declared check pass. `./target/debug/jeeves --version` → `jeeves 0.1.0`, exit 0 (also `jeeves version` and bare `jeeves`).

## Spec compliance

- **toon_str byte-exact vs ref/scan-active.sh:97-100** — ✅ Verified empirically: compiled the Rust `toon_str` and diffed its output against the bash function run on the same 10 cases (plain, spaces, embedded quote, backslash, `\"` mix, unicode, tab, empty, trailing backslash, `\\"`). Byte-identical. The backslash-first-then-quote ordering matches the script's `s="${1//\\/\\\\}"` then `"${s//\"/\\\"}"`.
- **lock_status / human_age / activity_age_secs mirror ref/lib.sh** — ✅ `lock_status` (src/core/proc.rs:1701) reproduces lib.sh:64-77: missing file → Unknown, unparseable reason → Unknown, procfs probe (`/proc/self`) before trusting pid absence, missing `/proc/<pid>` → Stale, starttime match → Live, mismatch → Stale. `proc_start_ticks` (src/core/proc.rs:1685) matches lib.sh:32-47 including the last-`)` comm strip and field-22 = index-19 read. `human_age` (src/core/time.rs:1838) matches lib.sh:135-141 exactly. `activity_age_secs` (src/core/time.rs:1857) returns None on a clean worktree per the brief (deliberate deviation from lib.sh, which would fall back to commit time alone).
- **Config resolver flag > env > config > default, legacy aliases, silent on stdout** — ✅ src/core/config.rs:931; canonical env first, then legacy aliases in order, config key by canonical name, default last; nothing prints.
- **XDG paths with env overrides; 0700; no hardcoded /home paths** — ✅ src/core/paths.rs:1524-1547; `JEEVES_STATE_DIR` > `XDG_STATE_HOME/jeeves` > `~/.local/state/jeeves` with chmod 0700, data dir without chmod — mirrors bin/jeeves_lib.py:44-60. `rg '/home/' src/` → no matches.
- **Exit-code family 0/1/2 defined** — ✅ src/core/error.rs:1146-1174 (Refusal=1, Usage=2) plus `ExitCode::SUCCESS` in main.
- **Phase-1 scope only** — ✅ main.rs ships only the `version` subcommand; no later-phase subcommand present.
- **Porcelain parser survives spaces in paths** — ✅ src/core/git.rs:1320 parses `worktree list --porcelain` line-by-line; test `worktree_list_parses_spaces_in_paths` builds a real worktree at `with space` and passes.
- **TOON emitter "toon_str escaping + table shape"** — ❌ Only `toon_str` exists (src/core/toon.rs:75). The brief's testable result explicitly lists the table shape ("TOON emitter (`toon_str` escaping + table shape)"), and 04-tdd.md:129 pins `pub fn toon_table(rows:&[RepoRow]) -> String // exact scan-active shape`. No table emitter was implemented.
- **Unit-sep log parser faithful to the shell reference** — ❌ `log_units` (src/core/git.rs:1251) drops interior empty fields. Verified divergence: for a commit with an empty subject (`git commit --allow-empty-message`), `git log --format='%h%x1f%s%x1f%cr'` emits `542c484\x1f\x1f0 seconds ago`; the shell's `read -r sha subj age` (scan-active.sh:408) yields `sha=542c484 subj="" age="0 seconds ago"` (non-whitespace IFS does not collapse consecutive separators), while the Rust function yields `["542c484", "0 seconds ago"]` — the subject field is silently dropped. The doc comment's justification (a trailing separator producing an empty field) is factually wrong: `split('\x1f')` never produces a trailing empty field from the final newline, so the `continue` clause has no legitimate case. This will emit `sha,"age"` instead of `sha,"","age"` in scan-active's commitlines, breaking the byte-exact TOON contract for empty-subject commits.

## Findings

### Important

1. **src/core/git.rs:1261** — `log_units` drops empty interior fields (empty commit subject), diverging from the shell `read` reference it claims to mirror. Verified with a real empty-subject commit: shell → `["542c484","","0 seconds ago"]`, Rust → `["542c484","0 seconds ago"]`. Will corrupt scan-active commitlines column shape (byte-exactness contract) in phase 4. Fix: remove the `field.is_empty() && !current.is_empty()` skip; it is never needed (the trailing newline never yields an empty split field) and only ever eats real data.
2. **src/core/toon.rs (whole file)** — `toon_table` (exact scan-active table shape) missing from the brief's testable result "TOON emitter (toon_str escaping + table shape)"; only `toon_str` was delivered.

### Minor

3. **src/core/config.rs:947** — `resolve` indexes `env[0]` unconditionally; an empty `env` slice panics. All current callers pass non-empty lists, but the function is `pub` and the panic is unguarded.
4. **src/core/paths.rs:1528-1531, 1542-1545** — `state_dir`/`data_dir` `panic!` on create failure instead of returning an error; and `home_join` (src/core/paths.rs:1562) yields a relative path when `HOME` is unset, where Python's `expanduser` falls back to the pwd entry.
5. **Cargo.toml:899-910** — 10 of 11 dependencies (chrono, dirs, fs2, iana-time-zone, serde, serde_json, sha2, thiserror, unicode-case-mapping, unicode-normalization) are unused in phase 1; only clap is referenced. Forward-looking bloat; could be added in the phases that need them.
6. **.taskferry.toml:1** — `check = "mise run check-rust"` covers only the Rust tree; the brief's Files section says "check = mise run check covering both trees". The Python tree remains covered by CI (check.yml) only.
7. **src/main.rs:2087-2091** — bare `jeeves` invocation prints the version rather than clap's default help/usage. Harmless and undocumented; fine to keep, but it is a behavior choice worth a comment.
8. **Tests are inline `#[cfg(test)]` modules** rather than the brief's `tests/core_*.rs` files. Functionally equivalent unit coverage; a naming/layout deviation only.

## Strengths

- `toon_str` is byte-identical to the bash reference across adversarial cases (backslash-then-quote, trailing backslash, unicode, tab) — verified by direct diff, not just by the in-repo tests.
- `lock_status`/`proc_start_ticks` faithfully carry over lib.sh's hard-won edge semantics (procfs probe guard, malformed → Unknown, starttime match), and the git wrappers are tested against real scratch repos including a space-containing worktree path.

## Verdict

**Spec compliance:** ❌ — gaps: (1) `log_units` drops empty interior fields, diverging from the shell `read` reference and threatening the byte-exact TOON contract for empty-subject commits; (2) `toon_table` (table shape) missing from the TOON emitter deliverable. All other items pass, including byte-exact `toon_str`, lock/age semantics, config precedence, XDG/0700 paths, exit-code family, phase-1 scope, and space-safe porcelain parsing.

**Task quality:** Needs fixes

**Root cause:** execution-error — `log_units`'s empty-field skip was added on a wrong assumption about `split` behavior (the trailing newline never creates an empty field), silently eating real empty subject fields; and the table-shape emitter was scoped out of the TOON module despite being in the brief's testable result.
"
