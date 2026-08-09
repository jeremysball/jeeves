# Fix jeeves' evidence pipeline

## Problem

The digest's categorization is sound; its evidence plumbing is not. Four
defects, all confirmed against live state on 2026-08-09.

1. **The `[UNVERIFIED]` gate flags real refs.** `_valid_refs` (collect.py:250)
   allowlists only refs appearing in the live GitHub feed (`state=open` PRs,
   issues updated in 7 days) and the git scan (window `yesterday 00:00`). A
   merged PR is absent from an open-PR feed by definition, so every shipped PR
   older than the scan window is flagged. Today's digest: 20+ flags. `#331`
   and `#117` both verified real and MERGED 2026-08-07. The flag string is
   injected inside backticked evidence tags, corrupting the rendered card.
   `_mutation_refs_valid` then drops the matching todo `check` mutations, so
   the ledger can add loose ends but cannot close them.

2. **PR evidence can never verify.** `verify_evidence` (todos.py:131) runs
   `gh-axi pr view <n> -R <abs local path>`; `-R` expects `OWNER/REPO` and
   returns `NOT_FOUND` for a path. Independently, `PR_RE` is anchored
   `^PR #(\d+)$` while the model emits `"PR #419 merged"`, so the branch is
   never even reached. Every PR check lands in `pending.json` permanently.
   `issue #N` is emitted by the model and unsupported entirely.

3. **Two silent data-loss paths.** A slice under `trivial_min` is dropped and
   its offset advanced (collect.py:316) — 378 cumulative skips; a session
   accruing 3 prose entries an hour is never summarized. When the ferry omits
   a session's block, a stub is written and the offset advanced anyway
   (collect.py:343).

4. **The repo regex eats a newline.** `github\.com[:/]([^/]+)/([^/.]+)`
   (collect.py:96) captures the trailing `\n` when an origin URL has no `.git`
   suffix. Confirmed for `jeremysball/dotfiles-stow` →
   `gh api repos/jeremysball/dotfiles-stow\n/pulls` → `invalid control
   character in URL`. That repo contributes nothing to the feed.

Out of scope here, tracked as a follow-up: the extraction ferry sees 1.64% of
transcript bytes (161,760 of 9,857,047 across the 12 most recent transcripts)
because `denoise` keeps only `type:"text"` blocks. That needs a design pass,
not a patch.

Also out of scope: the JSON round-trip. 1,844 lifetime parse failures look
alarming but 1,819 of them predate 2026-08-02; the lenient fallbacks in
`parse_fenced_json` fixed it. Current rate is ~1/day.

## Guiding change

Invert the verification default. Today a ref is guilty until it appears in a
narrow feed. It should be **innocent unless disproved**: flag only when the
repo is known *and* the host says the ref does not exist. An undeterminable
ref is left alone rather than smeared — absent beats wrong.

## Tasks

### 1. Evidence-kind parsing accepts trailing prose

`PR_RE`/`HEX_RE`/`FILE_RE` anchor on `$`. Loosen to `\b` so `"PR #419 merged"`
and `"commit 3c33869 reset"` parse. Add an `issue #N` kind.

Tests: each regex against the exact strings found in `collect.log`.

### 2. PR/issue verification runs in the repo, not via `-R`

Replace `gh-axi pr view <n> -R <path>` with a `cwd=<path>` invocation, which
is the form verified to work. Same for issues.

Tests: `_runs` stubbed; assert the command shape and `cwd`.

### 3. Ref flagging becomes a disproof check

Delete `_valid_refs`/`_mutation_refs_valid` string matching. Resolve each
`#N` to a repo via the digest bullet's `- <repo>:` prefix; ask the host once
per `(repo, n)`, memoized. Flag only on a definite negative. Unresolvable
repo ⇒ no flag, no mutation drop.

Tests: known-real ref survives unflagged; proven-absent ref is flagged;
unresolvable ref is left untouched.

### 4. Offsets stop advancing over dropped content

Trivial-skip and no-block both hold the offset so the slice accumulates. Cap
the carry by age so an abandoned session cannot pin an offset forever.

Tests: a sub-`trivial_min` slice leaves the offset unmoved; a slice past the
age cap advances.

### 5. Origin-URL regex stops capturing whitespace

Strip the match and tolerate an optional `.git`. Test against both URL shapes,
with and without a trailing newline.

## Verification

`uvx --with pytest pytest -q` green (58 baseline + new). Then one real
`collect.py` run against live state, confirming `collect.log` shows no
`unverified refs` line for refs that exist and no `check demoted to pending
(evidence)` for a PR that genuinely merged.
