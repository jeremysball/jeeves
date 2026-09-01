# Phase 1 — Rust scaffold and shared core

**Testable result:** `cargo test` runs; `jeeves --version` exits 0; core
modules unit-tested: config resolver (flag > env > config > default, with
legacy-name aliases), XDG path helpers, TOON emitter (`toon_str` escaping +
table shape), git subprocess wrappers (unit-sep log formats, porcelain
parsers), `human_age`/`activity_age_secs`/`proc_start_ticks`/`lock_status`.
**Files:** `Cargo.toml`, `src/main.rs`, `src/core/*`, `tests/core_*.rs`,
`.mise.toml` (rust + cargo tasks, coexisting with the python tasks),
`.taskferry.toml` (check = mise run check covering both trees).
**Checks:** `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`.

