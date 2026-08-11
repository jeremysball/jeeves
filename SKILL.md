---
name: jeeves
description: "Use when the user wants grounding on what they actually accomplished — \"wake me up\", \"jeeves\", \"what did I even do today\", \"ground me\", \"what did I accomplish\", \"what did I leave behind\" — and for the cross-project morning standup (\"what did I get done yesterday\", \"catch me up on all my projects\", \"morning reorientation\"): a backward-looking digest of recent Claude Code sessions (shipped work, agentic oversight, loose ends, tangents never chased, tool failure modes, GitHub obligations needing a response) rolled up against a cross-repo git-state scan, plus an auto-managed todo ledger. NOT for continuing work in one known project: \"where was I\", \"what's left\", \"reorient me\" belong to orient/orient-quick."
---

# jeeves

## Overview

Introspective grounding for a scattered-but-productive, heavily-agentic work
style: what did you actually accomplish, what did you oversee, what did you
overlook, what threads did you leave behind. A silent hourly cron
(`collect.py`) ferries session transcripts into a daily digest, snapshots
cross-repo git state (via orient's `scan-active.sh`), pulls live GitHub
obligations, and maintains a todo ledger from session evidence; this skill
is the on-demand read of that state. It subsumes the former orient-global
skill: the cross-project "what got done / what's in flight / what to be
aware of" standup is a jeeves read now. **Invocation must be super fast —
local file reads only. Never dispatch a ferry during invocation.**

Principle: code owns all writes and all identity; models own all meaning.
Ledger edits go through `todos.py`, never by hand-editing from chat. Design:
`.superpowers/specs/2026-07-29-jeeves-design.md`.

## When to use

- "wake me up", "ground me", "what did I even do today", "what did I
  accomplish", "what did I leave behind", or addressed as "jeeves"
- The user sounds lost, tired, or mid-scatter and needs a backwards look
- Cross-project morning standup (absorbed from orient-global): "what did I
  get done yesterday", "catch me up on all my projects", "morning
  reorientation", deciding which project to pick back up

NOT: "where was I", "what's left", "reorient me", "quick orient" in one
known project (orient line — forward-looking); a single project's
done-vs-remaining card (orient-quick).

## Setup (first run only)

1. Load `choosing-a-model`, pick a live route (check
   `working-report.md`'s Standing rules first — the free tier is
   suspended by standing directive as of 2026-08-05, so `model` needs a
   paid Token Plan / `opencode-go` route, not an `opencode/*-free` id).
   Also pick a `fallback_model` on a *different* provider than `model`, so
   an account-wide outage on one provider doesn't take down both routes —
   and check whether either needs `--executor opencode` (`openai/*`,
   `xiaomi-tknplan/*`) or `--executor opencode --variant thinking`
   (`minimax/MiniMax-M3` specifically): `ferry()` in `jeeves_lib.py` never
   passes `--executor`/`--variant`, so a model that needs either will 401
   or crash regardless of route health. Then:
   ```bash
   python3 ~/.claude/skills/jeeves/bin/collect.py --help >/dev/null  # creates $XDG_STATE_HOME/jeeves
   echo "model = <chosen slug>" >> "$HOME/.local/state/jeeves/config"
   echo "fallback_model = <chosen fallback slug>" >> "$HOME/.local/state/jeeves/config"
   ```
2. `python3 ~/.claude/skills/jeeves/bin/install-cron.py --install` (idempotent).
3. `python3 ~/.claude/skills/jeeves/bin/collect.py --seed` — mark all current
   transcript bytes as seen so jeeves starts from now, not from the beginning
   of history. (To backfill a specific past day anyway, delete that day's
   rows from `offsets.tsv` and run `collect.py` backgrounded — it dispatches
   real ferries and takes minutes.)

## Invocation flow (super fast — no ferries, no network)

State dirs: `$JEEVES_STATE_DIR` or `~/.local/state/jeeves/`; ledger at
`$JEEVES_DATA_DIR/todo.md` or `~/.local/share/jeeves/todo.md`.

1. Read `~/.local/state/jeeves/digests/<today ET>.md`. Empty or missing
   (morning) ⇒ yesterday's. Date via:
   `python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; print(datetime.now(ZoneInfo('America/New_York')).date())"`
2. Live tail of the current session (the cron is up to an hour behind):
   ```bash
   J=$(python3 ~/.claude/skills/jeeves/bin/tail.py --discover "$(pwd)")
   python3 ~/.claude/skills/jeeves/bin/tail.py "$J" --offset 0 --max 25
   ```
   Append its output as a short "in this session so far" note. (The offset
   line prints on stderr; for continuity across wake-ups, use the offset
   recorded for this path in `~/.local/state/jeeves/offsets.tsv` if present.)
3. `python3 ~/.claude/skills/jeeves/bin/todos.py --delta` — open/done/
   dismissed/pending counts and top recurrences.
4. If pending > 0: `python3 ~/.claude/skills/jeeves/bin/todos.py --prune-pending`
   first (drains rows that already resolved), then
   `python3 ~/.claude/skills/jeeves/bin/todos.py --pending` — surface each
   survivor with its `reason` and its `state` for one-line resolution.
   Both are local git/`gh-axi` reads; the speed contract holds.
5. `python3 ~/.claude/skills/jeeves/bin/todos.py --wake`
6. Render the card (below).

**Cold start** (no digest at all): summarize today's slices from the step-2
tail output inline — seconds, still no ferry.

## Card format

**Lead with shipped work, grouped by repo and theme.** The user wants to
see what landed, not meta-state about it. For each active repo in the
git-state snapshot, render:

- **By PR or by commit hash**: PR numbers inline (`#251`) are the
  natural identifier when the work was reviewed and merged; otherwise
  short SHA + conventional-commit subject.
- **Prove merged before calling anything shipped.** Never infer merge
  state from a commit appearing in `git log --all`, from a PR number
  existing, or from a confident-sounding subject line. Ancestry is not
  proof either: a squash or rebase merge rewrites the branch into a new
  commit, so `git merge-base --is-ancestor` reports shipped work as
  unmerged, and this repo squash-merges its own PRs. Call `todos.py`'s
  `classify_evidence`, which asks three sources cheapest-first — ancestry,
  then how much of the commit's content is already in the base branch (via
  `auditing-worktrees`' `coverage-score`, when it is installed; point
  `AUDIT_WORKTREES_BIN` elsewhere to move it), then GitHub for the pull
  requests carrying the commit — and returns `landed` / `outstanding` /
  `unknown`. The middle one catches most squashes without a network call at
  all, and is the only source that answers for a repo whose origin is not
  GitHub. Anything not proven `landed` belongs under **Unmerged work**,
  never under **Shipped**.
- **Label every item's state inline**, so the two are never
  ambiguous at a glance: `#413 merged` vs `b8f57f0 unmerged
  (feat/ro-rw-dirs, PR #401 open)`. A bare SHA next to a bare PR number
  reads as one undifferentiated pile of "done", which is the single
  most misleading thing this card can do — the user cannot act on
  "shipped" and "still needs merging" the same way.
- **Stat when it matters**: `tasks.js` 7400+/6800- across 53 files says
  more than the subject line. Include `git diff --stat main..branch`
  for unmerged worktrees and `git log --stat` snippets for merged work
  that landed significant churn.
- **Themes, not chronology**: group commits by what they fixed
  (sandbox correctness / daemon stability / advisor cleanup) rather
  than by time order. The user reads for "is this area done?" not
  "what did I do at 3pm?".
- **Then meta-state**: unmerged worktrees, open PRs, loose ends, GitHub
  obligations, todos — the things the digest is good at.

**Section order** when the digest is fresh:

1. **This session** (only if there's something concrete from the
   current wake's own work)
2. **Shipped** — grouped by repo, themed within each
3. **Unmerged work** — worktree branches with file count + diff stat
4. **GitHub** — open PRs first (none reviewed = needs you), then open
   issues, then recent closes
5. **Loose ends** — ledger open items grouped by file/repo
6. **Failure modes** — tooling/models/infra that misbehaved
7. **Todos** — delta + pending with stale-evidence flagged

When the digest is **stale** (cron broken, >24h since last refresh,
synthesis malformed), lead with that fact and lean harder on git log
ground truth. A thin digest is not a thin day.

Same scan rules as orient-quick: plain Unicode glyphs (no color emoji),
one bold payload per bullet, backticked identifiers, noun phrases not
sentences, flat lists, omit empty sections entirely. The Todos section
leads with the delta since last wake ("checked 2, added 3, dismissed 1")
and one oldest-open line. Recurrence counts surface flatly — a loose
end seen three times is signal, not a nag.

**Drain the pending queue before rendering, don't just eyeball it.**
Run `todos.py --prune-pending` at step 4. It re-runs the gates over
every queued row: applies the ones whose evidence now shows `landed`,
drops the ones whose ledger line has since been checked or dismissed
(`moot`) or was never there (`stale`), and keeps only what is genuinely
still outstanding. `todos.py --pending` then annotates each survivor
with a `state` field, so a row pointing at a merged PR is visibly
distinct from one pointing at an unmerged branch commit.

Both commands reach the network. Classifying a commit that is not an
ancestor of the base branch costs one GitHub lookup, memoized per repo
and sha for the run, so a queue of rows citing the same commit pays for
it once. A lookup that fails reports `unknown`, never `outstanding`.

Only rows that survive the prune belong on the card, and each should
carry its state. A row whose evidence reads `unknown` is not the same
as one reading `outstanding` — surface the ambiguity rather than
picking the flattering interpretation.

The cron also persists a cross-repo git-state snapshot at
`~/.local/state/jeeves/git-state.md` (a read-only `scan-active.sh` rollup:
per-repo branches with push/merge classification, dirty trees, recent
commit subjects). The digest treats it as ground truth two ways: a
cross-check on session-claimed work, and a Shipped source in its own
right — commits in the window no transcript captured still surface as
shipped, each attributed to a day by subtracting its age (relative to
the scan timestamp in the snapshot header) from that timestamp.
Anomalies fold into Loose ends; when rendering a card, a quick local
read of that file may surface "be aware of" material the digest predates
(a branch that diverged since the last cron hour). Reading it is a
local file read — speed contract intact.

**Deep-orient hand-off** (inherited from orient-global): after presenting
the card, offer _"Deep-orient which project?"_ — when the user picks one,
invoke the `orient` skill with that repo's path as `DIR` for the full
single-project briefing including session recall.

## Conversational edits

- "kill X" / "drop X" ⇒ `todos.py --dismiss "X"`
- "add X" ⇒ `todos.py --add "X" --kind manual --source user`
- Pending resolution ⇒ `--dismiss` for rejects; a confirmed check-off the
  evidence gate rejected is applied by hand in the ledger only after the
  user has seen the evidence mismatch.
- Never hand-edit todo.md from chat when a flag exists.

## Common mistakes

- Dispatching a ferry during invocation — breaks the speed contract; the
  background hour already did the thinking.
- Padding empty sections — omit them.
- Judgmental prose in the card — observe, never scold ("two-hour debug
  spiral" is a shape, not a verdict).
- Editing todo.md directly — breaks reconciliation; the ledger file is the
  user's face of truth, `todos.py` is the write path.
- Answering "where was I" with this skill — that's orient; jeeves looks
  back, orient looks forward.
- Reporting HEAD's tracking state as the repo's (inherited scar from
  orient-global): a repo once reported "nothing unpushed" while `main` sat
  diverged 16-against-17 with four unmerged task branches, because the
  checked-out branch happened to be clean. Read the `branches:` block in
  the git-state snapshot, always; `DIVERGED` means a push will be rejected
  (fix is a merge), not a backlog of unpushed work.
- Calling a squash-merged branch unmerged — `git rev-list main..branch`
  still lists the original commits after a squash merge even when the tip
  is in `origin/main`. Trust the snapshot's classification: `potentially
  outstanding` means not proven merged, never "active work".
- Treating the digest as ground truth — it isn't. The synthesis model
  abbreviates and drops detail on purpose. Git log per active repo is
  the actual record; the digest is a
  cross-check. When cron is broken (silent failure mode seen 2026-08-01:
  argv-overflow + fence-omission parser, both unnoticed for ~2 days),
  the digest goes stale and the card will be thin even when real work
  landed. Detect cron health: `tail ~/.local/state/jeeves/collect.log`
  for "synthesis failed" / "extraction output unparseable" / "digest not
  refreshed" patterns. If present, lead with the failure and go to git.
- Reading an `[UNVERIFIED]` tag as a hedge — it is a disproof. It means
  the repo's issue/PR numbering does not reach that number, so the ref
  cannot exist. The absence of a tag is not confirmation: a line naming
  no known repo, or two, is left unadjudicated rather than guessed at.
- Listing pending todos without checking their evidence refs — a todo
  queued before its evidence went stale (issue closed, branch merged,
  PR landed) is dead weight, not a real loose end. Run
  `--prune-pending` before rendering.
- Presenting an unmerged branch commit as shipped work. A commit shows
  up in `git log --all` whether or not it is in `main`; a PR number
  resolves whether or not it merged. Both were once reported under
  **Shipped** on this card — `b8f57f0`, which lived only on an open
  PR's branch — because the card was built from `git log` output alone.
  Prove `landed` per the Card format rules, or file it as unmerged.
- Rendering the card twice with the same shape after the user said
  "this is weak" — if a card lands flat, the answer is to go to ground
  truth (git log) and rebuild, not paraphrase the digest more politely.
