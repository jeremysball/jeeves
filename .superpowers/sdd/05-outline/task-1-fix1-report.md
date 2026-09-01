"# Task 1 fix report

Commit: `f33aa60` — `fix(core): keep interior empty fields in log_units; add toon_table emitter`

## Diff summary

Two files touched, nothing else:

### src/core/git.rs — `log_units` (fix 1)

The old parser split the raw stdout on `\x1f` and *dropped* empty fields whenever a record was in progress, so an interior empty field (e.g. an empty subject in `%h%x1f%s%x1f%cr`) collapsed the record from 3 fields to 2 and misaligned every subsequent record.

New behavior (mirrors the shell reference `IFS=$'\x1f' read`):
1. Split raw stdout on `'\n'` into records first; drop only the trailing empty record produced by the final newline.
2. Split each record on `'\x1f'`, keeping ALL fields (interior empties preserved).

Regression test `log_units_keeps_interior_empty_fields`:
- `git commit --allow-empty --allow-empty-message -m ""` in a scratch repo
- parse `--format=%h%x1f%s%x1f%cr`
- assert 2 records (initial commit + empty commit), newest record has 3 fields with `field[1] == ""`.

### src/core/toon.rs — `toon_table` (fix 2)

New public API mirroring the exact shape ref/scan-active.sh prints (lines ~428-468):

- `pub enum Cell { Str(String), Bare(String) }` — `Str` cells are quoted via `toon_str` (backslash-doubling then quote-escaping, per scan-active.sh:97-100); `Bare` cells (integers, clean states, ...) are emitted verbatim, matching mixed usage like `echo "  $(toon_str "$repo"),$(toon_str "${R_BRANCH[$repo]}"),${R_TREE[$repo]},${R_ALERTS[$repo]}"` (scan-active.sh:439).
- `pub fn toon_table(name: &str, header: &[&str], rows: &[Vec<Cell>]) -> String` — emits `name[N]{col1,col2}:` header line (N = row count) then one 2-space-indented row per record, cells joined with commas.

Unit tests (vectors incl. spaces/quotes/backslashes):
- `table_header_shape` — empty table, header only
- `table_mixed_quoted_and_bare_cells` — mirrors scan-active.sh:439 repos row
- `table_escapes_quotes_and_backslashes_in_string_cells`
- `table_multiple_rows_each_indented`
- `table_empty_string_cell_stays_quoted`

## Command outputs

### `mise run check-rust` (the repo's declared check command)

Note: the sandbox home is read-only, so mise needed writable dirs (`MISE_STATE_DIR`/`MISE_CACHE_DIR`/`MISE_DATA_DIR`/`RUSTUP_HOME`/`CARGO_HOME` under /tmp/opencode, reusing the already-installed rust 1.91.1 toolchain). The check itself ran fully and passed:

```
mise rust@1.91.1     [1/3] install
info: syncing channel updates for 1.91.1-x86_64-unknown-linux-gnu
mise rust@1.91.1     [1/3]   1.91.1-x86_64-unknown-linux-gnu unchanged - rustc 1.91.1 (ed61e7d7e 2025-11-07)
info: checking for self-update (current version: 1.29.0)
mise rust@1.91.1     [1/3] rustc -V
mise rust@1.91.1     [1/3] rustc 1.91.1 (ed61e7d7e 2025-11-07)
mise rust@1.91.1   ✓ installed
[check-rust] $ mise run fmt-check && mise run lint && mise run test
[fmt-check] $ cargo fmt --check
[lint] $ cargo clippy --all-targets -- -D warnings
   Compiling proc-macro2 v1.0.107
   Compiling quote v1.0.47
   Compiling unicode-ident v1.0.24
   Compiling version_check v0.9.5
   Compiling libc v0.2.189
    Checking typenum v1.20.1
   Compiling serde_core v1.0.229
   Compiling autocfg v1.5.1
    Checking utf8parse v0.2.2
    Checking anstyle-parse v1.0.0
    Checking anstyle-query v1.1.5
    Checking is_terminal_polyfill v1.70.2
   Compiling zmij v1.0.23
    Checking colorchoice v1.0.5
   Compiling generic-array v0.14.7
    Checking anstyle v1.0.14
   Compiling heck v0.5.0
   Compiling serde_json v1.0.151
   Compiling num-traits v0.2.19
    Checking anstream v1.0.0
    Checking strsim v0.1.11
   Compiling unicode-case-mapping v1.0.0
    Checking option-ext v0.2.0
   Compiling serde v1.0.229
    Checking clap_lex v1.1.0
   Compiling thiserror v2.0.20
    Checking tinyvec_macros v0.1.1
    Checking tinyvec v1.12.0
   Compiling syn v3.0.4
    Checking clap_builder v4.6.6
    Checking block-buffer v0.10.4
    Checking crypto-common v0.1.7
    Checking digest v0.10.7
    Checking iana-time-zone v0.1.65
    Checking dirs-sys v0.4.1
    Checking cpufeatures v0.2.17
    Checking memchr v2.8.3
    Checking cfg-if v1.0.4
    Checking itoa v1.0.18
    Checking generic-array v0.14.7
    Checking thiserror v2.0.20
    Checking unicode-normalization v0.1.25
   Compiling serde_derive v1.0.229
   Compiling clap_derive v4.6.4
   Compiling thiserror-impl v2.0.20
    Checking clap v4.6.6
    Checking jeeves v0.1.0 (/workspace/jeeves-rust-fix1)
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 6.52s
[test] $ cargo test
   Compiling typenum v1.20.1
   Compiling utf8parse v0.2.2
   Compiling colorchoice v1.0.5
   Compiling libc v0.2.189
   Compiling is_terminal_polyfill v1.70.2
   Compiling anstyle v1.0.14
   Compiling anstyle-query v1.1.5
   Compiling serde_core v1.0.229
   Compiling strsim v0.1.11
   Compiling anstyle-parse v1.0.0
   Compiling clap_lex v1.1.0
   Compiling tinyvec_macros v0.1.1
   Compiling option-ext v0.2.0
   Compiling tinyvec v1.12.0
   Compiling num-traits v0.2.19
   Compiling zmij v1.0.23
   Compiling clap_builder v4.6.6
   Compiling itoa v1.0.18
   Compiling memchr v2.8.3
   Compiling iana-time-zone v0.1.65
   Compiling cfg-if v1.0.4
   Compiling cpufeatures v0.2.17
   Compiling thiserror v2.0.20
   Compiling unicode-case-mapping v1.0.0
   Compiling chrono v0.4.45
   Compiling unicode-normalization v0.1.25
   Compiling dirs-sys v0.4.1
   Compiling fs2 v0.4.3
   Compiling crypto-common v0.1.7
   Compiling dirs v5.0.1
   Compiling digest v0.10.7
   Compiling sha2 v0.10.9
   Compiling serde v1.0.229
   Compiling serde_json v1.0.151
   Compiling clap v4.6.6
   Compiling jeeves v0.1.0 (/workspace/jeeves-rust-fix1)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 4.30s
     Running unittests src/main.rs (target/debug/deps/jeeves-e932a3975ce06c6e)

running 53 tests
test core::config::tests::canonical_env_beats_alias ... ok
test core::config::tests::default_when_nothing_sets_it ... ok
test core::config::tests::config_beats_default ... ok
test core::config::tests::env_beats_config_and_default ... ok
test core::config::tests::empty_input_yields_empty_map ... ok
test core::config::tests::alias_used_when_canonical_unset ... ok
test core::config::tests::empty_env_is_skipped ... ok
test core::config::tests::parses_key_value_and_skips_junk ... ok
test core::config::tests::flag_beats_everything ... ok
test core::error::tests::kinds_are_distinct ... ok
test core::config::tests::parses_url_with_equals ... ok
test core::error::tests::exit_codes_map_correctly ... ok
test core::git::tests::log_units_empty_for_no_commits ... ok
test core::paths::tests::data_dir_falls_back_to_home ... ok
test core::paths::tests::data_dir_falls_back_to_xdg ... ok
test core::paths::tests::data_dir_override_is_honored ... ok
test core::paths::tests::dirs_are_absolute_paths ... ok
test core::paths::tests::state_dir_falls_back_to_home ... ok
test core::paths::tests::state_dir_falls_back_to_xdg ... ok
test core::paths::tests::state_dir_is_created_mode_0700 ... ok
test core::paths::tests::state_dir_override_is_honored ... ok
test core::proc::tests::dead_pid_is_stale ... ok
test core::proc::tests::garbage_file_is_unknown ... ok
test core::proc::tests::human_reason_is_unknown ... ok
test core::proc::tests::missing_file_is_unknown ... ok
test core::proc::tests::own_pid_is_live ... ok
test core::proc::tests::partial_record_is_unknown ... ok
test core::proc::tests::start_mismatch_is_stale ... ok
test core::git::tests::rev_parse_abs_git_dir_is_absolute ... ok
test core::time::tests::boundary_values ... ok
test core::time::tests::days_at_or_above_one_day ... ok
test core::time::tests::hours_below_one_day ... ok
test core::time::tests::minutes_below_one_hour ... ok
test core::toon::tests::embedded_backslash ... ok
test core::toon::tests::embedded_quote ... ok
test core::toon::tests::empty_string ... ok
test core::toon::tests::matches_reference_implementation_shape ... ok
test core::toon::tests::path_with_spaces ... ok
test core::toon::tests::plain_string ... ok
test core::toon::tests::quote_after_backslash_is_escaped_not_doubled ... ok
test core::toon::tests::table_empty_string_cell_stays_quoted ... ok
test core::toon::tests::table_escapes_quotes_and_backslashes_in_string_cells ... ok
test core::toon::tests::table_header_shape ... ok
test core::time::tests::activity_age_is_none_when_clean ... ok
test core::toon::tests::table_multiple_rows_each_indented ... ok
test core::toon::tests::table_mixed_quoted_and_bare_cells ... ok
test core::toon::tests::trailing_backslash ... ok
test core::git::tests::worktree_list_reports_detached_head ... ok
test core::git::tests::log_units_parses_separators ... ok
test core::git::tests::worktree_list_parses_spaces_in_paths ... ok
test core::git::tests::log_units_keeps_interior_empty_fields ... ok
test core::git::tests::merge_base_none_when_unrelated ... ok
test core::git::tests::merge_base_singular ... ok

test result: ok. 53 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.03s
```

Exit code: 0 — `mise run check-rust` PASSED.

### Individual commands (run directly, same result)

```
$ cargo fmt --check
(no output)   # exit 0

$ cargo clippy --all-targets -- -D warnings
   Checking jeeves v0.1.0 (/workspace/jeeves-rust-fix1)
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.26s
# exit 0

$ cargo test
test result: ok. 53 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.02s
# exit 0
```

## Final state

- `git add -A` staged only `src/core/git.rs` and `src/core/toon.rs`.
- Commit `f33aa60` `fix(core): keep interior empty fields in log_units; add toon_table emitter` (2 files changed, 116 insertions(+), 25 deletions(-)).
- `mise run check-rust`: PASS (fmt-check, lint, test all green).
"
