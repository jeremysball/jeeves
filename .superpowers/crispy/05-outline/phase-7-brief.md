# Phase 7 — ledger + `jeeves todos`

**Testable result:** `todo.md` round-trip byte-compat, normalize()/line_hash
vector table generated from Python BEFORE deletion and pinned, add/check/
dismiss/reconcile/prune, classify_evidence with stubbed gh-axi and the
in-repo coverage binary (real-boundary, no stub scorer), pending/seen/memo
stores.
**Files:** `src/digest/{ledger,todos}.rs`, `tests/ledger_compat.rs`,
`tests/todos_evidence.rs`.
**Checks:** vector table green; stub-vs-real scorer agreement tests.

