# SDD ledger — plan: .superpowers/crispy/rust-rewrite/05-outline.md

Preflight scan: Phases 2..6 share src/worktrees/coverage.rs and src/orient/scan.rs; sequencing fixed by outline order (coverage before scan content pass). Phase 4/6 roots-file default change is read-compatible (legacy fallback). Phase 7 normalize vectors must be generated from Python BEFORE Phase 9 deletion — ordering constraint respected. No task-internal contradictions found.

Task 1: review round 1: spec ❌ (log_units drops interior empty fields — controller-verified vs shell; toon_table missing). Root cause: execution-error.
Task 1: fix round 1/5 dispatched (resumed session ses_fa4e9604, worktree jeeves-rust-fix1, oc_mti5plnk).
Task 1: minor (deferred): reviewer could not read sdd artifacts initially (untracked in worktree) — fixed by committing them.
Task 1: fix round 1/5 (2 addressed, 0 open; commits dbe7a93..776efa8)
Task 1: complete (commits fe4daad..776efa8, review clean after round 1)
Note: mise gate fails in sandbox on trusted-config symlink (RO state dir) - manual host verification + --force is the standing workaround this migration.
Task 2: review round 1: spec fail - linked-worktree objects path (controller-reproduced: rust UNKNOWN merge-conflict vs ref SCORED 0 on /tmp/covchk/wt). Root cause: execution-error.
Task 2: minor (deferred): main.rs bad-dir-vs-count error ordering differs from ref cd semantics (same exit-2 family).
Task 2: fix round 1/5 dispatched (oc_mti7h3iq, worktree jeeves-rust-fix2).
Task 2: fix round 1/5 (2 addressed, 0 open; commits 95bd97e..de89052)
Task 2: complete (commits 2af03f4..de89052, review clean after round 1)
Task 3a: 3-dispatch split (core/tests/hook) after two context-death attempts on the monolithic brief.
Task 3a-1: core landed (host-verified 62 tests, clippy clean). Note: audit.rs carries 14 em-dashes mirroring the reference report strings; inherited convention, flag at final review.
Task 3a: done in 3 units (core/collapse/golden) - byte parity on all 5 buckets.
Task 3 split further: 3a done (core+collapse+golden), 3b-i archive, 3b-ii clean, 3c tests+hook in flight.
Ruling: model route switched to openai/gpt-5.6-luna (max) after 6 silent provider deaths on ollama flash for >100-turn-scale writes; ollama still fine for short units/reviews.
Task 3: review pending after 3c.
Task 3: review round 1: 6 Important + 3 Minor findings (hook panic path, clean empty-args no-op, update-ref stdout swallowed, two knobs off-resolver, hook repo-mode divergence, missing reference-comparison tests). Controller spot-verified: hook rc 101 vs ref rc 0; findings CONFIRMED real.
Task 3: fix round 1 unit A (impl) dispatched oc_mtie2jds.
Task 3: fix round 1/5 (5 addressed, 2 open: F2 wording, F6 bucket coverage).
Task 3: RULING on F2 — usage prose (not a refusal string) may carry the new program name; the binding constraint is the exit-code family + refusal-message parity, both met. Parked. Ruling: costs nothing if wrong (usage text only), reversible in one line.
Task 3: fix round 2/5 (F6 ADDRESSED: prunable + UNKNOWN passthrough both sides byte-equal; unknown-merge bucket shown unreachable for either implementation (is-ancestor rc 1 not >1) - parity preserved by construction).
Task 3: parked - F2 usage wording (Ruling: program name in usage prose legitimately changes with the rename; binding constraint is exit-code family + refusal strings, both met. Costs nothing if wrong.)
Task 3: complete (commits 06f7e4c..559a369 + docs, review clean after round 2, 1 parked)
