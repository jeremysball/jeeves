# Give the extraction ferry the transcript, not a prose summary of it

## Problem

`denoise` keeps only `type: "text"` blocks from user/assistant messages.
Measured across the 12 most recent transcripts: **161,760 of 9,857,047 bytes
survive — 1.64%** (594 of 5,819 lines). Everything else is dropped before the
model sees anything: every tool call, every tool result, every
`isSidechain` entry, and each surviving message is cut at 800 characters.

The consequences show up directly in the digest:

- **Evidence refs are invented shapes.** Commit hashes and PR numbers live in
  tool calls and their results, so they only reach the model when the
  assistant happened to narrate them in prose. That is why real digests carry
  evidence tags like `commit applied:true`, a bare `commit`, and
  `session analysis` — the model is producing the *shape* of a ref it never
  saw.
- **Failure modes are whatever got said out loud.** A crashed command, a
  timeout, a rate limit all live in tool results. The Failure modes section
  reflects narration, not events.
- **Oversight is invisible.** `isSidechain` entries are dropped entirely, so
  Task-tool subagent work — which SKILL.md explicitly calls real work — never
  reaches the extraction pass at all.

The code is making a meaning judgment (what matters in this session) with the
one input that cannot support it, while the model is asked to hand-serialize
a JSON schema, which is the mechanical job. The two halves are swapped.

## Constraint that shapes the design

`~/.claude` is in taskferry's default sandbox denylist
(`src/sandbox.js:138`), and transcripts live under `~/.claude/projects`. A
per-project `read_only_paths` entry cannot re-admit it: verified by running
`resolveReadOnlyProjectBinds`, which classifies `~/.claude/projects` as
`unsafe` and drops it, because it overlaps a protected path. Its docstring
names this exact case. Overriding the global denylist would open all of
`~/.claude` — credentials and every project's transcripts — to every ferry on
the machine, which is a worse trade than copying.

So: **stage the slice, don't mount the source.**

## Design

1. **Stage.** For each session in the run, `collect.py` writes that session's
   raw delta lines (the bytes between the stored offset and the new one,
   unfiltered) to `<staging>/<slug>--<sid>.jsonl`, where `<staging>` is a
   per-run directory under `$JEEVES_STATE_DIR/staging/`. Offsets, identity,
   and slice boundaries stay in code — those have never been the problem.
2. **Dispatch with the directory.** `ferry()` gains a `directory` argument and
   passes `--directory <staging> --no-overlay`. The overlay buys nothing for a
   scratch dir jeeves owns, and skipping it means the ferry's output file
   lands directly instead of needing an accept/reject round-trip.
3. **Explore, don't receive.** The extraction prompt stops carrying a rendered
   prose blob. It names the staged files and tells the ferry to work the raw
   JSONL with `rg`/`jq` — tool calls, tool results, and sidechain entries all
   reachable — and says which fields carry what.
4. **Write a file, not a message.** The ferry writes
   `<staging>/summary-<sid>.json` per session. `collect.py` reads those files;
   the final message becomes a fallback, not the channel. This is the fix for
   the failure that cost four review ferries their output earlier today: a
   turn that ends on a tool call still leaves the artifact behind.
5. **Keep `denoise` as the fallback path.** If staging or the file-read fails,
   fall back to today's rendered-prose dispatch rather than losing the run.

## Tasks

1. `stage_slice()` writes raw delta lines per session; staging dirs are
   pruned after a successful run and on a TTL.
2. `ferry(..., directory=)` threads `--directory`/`--no-overlay` through
   `_ferry_once`.
3. New `prompts/extract-tools.md`: names the staged paths, the JSONL shape
   (`message.content[]` blocks, `toolUseResult`, `isSidechain`), the seven
   categories, and the output-file contract.
4. `read_staged_summaries()` collects `summary-*.json`, with the current
   message-parsing path as fallback.
5. Prune staging; never leave transcript copies lying around indefinitely.

## Verification

Unit tests for staging, pruning, the directory flag, and file-vs-message
precedence. Then the real boundary, which mocks cannot cover: one live
`collect.py` run against real transcripts, confirming the ferry actually read
tool results — a digest whose evidence refs are real commit hashes and PR
numbers rather than `commit applied:true`.

## Explicitly out of scope

Removing `denoise` entirely. It stays as the fallback until a live run proves
the staged path is at least as good.
