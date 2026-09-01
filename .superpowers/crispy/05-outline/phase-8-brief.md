# Phase 8 — `jeeves collect` (the pipeline)

**Testable result:** full hourly run against synthetic state: session
discovery, offset delta reads w/ rotation, denoise field set, roots refresh
→ scan → git-state.md, repo-todo ingest, taskferry list/dispatch/wait/result
TOON parsing against stubbed taskferry fixtures captured from real runs,
staging dirs + summary field checks, synthesis assembly, digest render,
prune rules, flock skip.
**Files:** `src/digest/collect.rs`, `tests/collect_pipeline.rs`,
`tests/fixtures/taskferry_*.toon`.
**Checks:** end-to-end digest equality on a frozen fixture set; one real
unattended run on the machine after install (PR notes the verification).

