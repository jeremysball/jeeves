# Phase 9 — Delete Python, fold docs, CI single-tree

**Testable result:** `bin/*.py`, `ref/`, `prompts/`-referenced python,
`pyproject.toml`, uv lockfile gone; CI = rust legs only with no sibling
fetch and no `JEEVES_CI_PAT`; `.mise.toml` python tasks removed; SKILL.md
updated to the folded-into-one-binary wording; `check.yml` badge green.
**Files:** deletions + `Cargo.toml`, `.github/workflows/check.yml`,
`SKILL.md`, `.superpowers/` bookkeeping.
**Checks:** full CI green; grep for `python|uv|coverage-score|scan-active`
references in code paths returns nothing.

# Follow-ups (outside this PR, gated on it)

- Machine repoint: apply `jeeves migrate` output (crontab, settings.json).
- dotclaude PR: fold orient/orient-quick/auditing-worktrees SKILL.md docs to
  call `jeeves` subcommands; delete their `bin/`; archive source dirs.
- dotfiles hook → `jeeves coverage` (worktree-budget sequencing, D-E).
