//! Report-only sweep for worktree/branch drift, mirroring
//! ref/audit-worktrees.sh exactly: per-repo report bucketed by what you'd
//! actually do about each branch. Never modifies anything.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::core::config;
use crate::core::error::CliError;
use crate::core::git;
use crate::core::proc::LockStatus;
use crate::core::time::{activity_age_secs_for, human_age};
use crate::worktrees::coverage::coverage_score;

/// Knobs for `audit_repo` / `audit_sweep`, resolved through the config
/// resolver (flag > env > config file > default).
#[derive(Debug, Clone)]
pub struct AuditOpts {
    /// Skip the content-merge scoring pass (the expensive one).
    pub no_content: bool,
    /// Activity newer than this (secs) counts as in flight and is hidden.
    pub inflight_secs: u64,
    /// Unmerged, idle at least this long, never pushed -> archaeology.
    pub archaeology_secs: u64,
    /// SCORED pct at/above this -> likely-content-merged bucket.
    pub content_merge_threshold: u64,
}

/// Resolves the audit knobs: canonical env first, then legacy aliases, then
/// the config file, then defaults. The content-merge threshold is validated
/// as a percentage and rejects 0 (a 0 threshold would offer every open
/// branch for batch archive), mirroring ref/audit-worktrees.sh:29-40.
pub fn resolve_opts(no_content: bool) -> AuditOpts {
    let config = config::read_config(None);
    let inflight = config::resolve(
        &None,
        &["JEEVES_AUDIT_INFLIGHT_SECS", "WORKTREE_AUDIT_INFLIGHT_SECS"],
        &config,
        "7200",
    )
    .parse()
    .unwrap_or(7200);
    let archaeology = config::resolve(
        &None,
        &[
            "JEEVES_AUDIT_ARCHAEOLOGY_SECS",
            "WORKTREE_AUDIT_ARCHAEOLOGY_SECS",
        ],
        &config,
        "7776000",
    )
    .parse()
    .unwrap_or(7776000);
    let mut threshold = config::resolve(
        &None,
        &["WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD"],
        &config,
        "95",
    );
    if threshold == "0" || !is_valid_pct(&threshold) {
        eprintln!(
            "  (skipped: WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD must be an integer 1-100 with no leading zero, got '{threshold}')"
        );
        threshold = "95".to_string();
    }
    AuditOpts {
        no_content,
        inflight_secs: inflight,
        archaeology_secs: archaeology,
        content_merge_threshold: threshold.parse().unwrap_or(95),
    }
}

/// Strict decimal percentage check, mirroring ref/lib.sh:254-268: rejects
/// non-digits, leading zeros (octal-looking), and anything longer than 3
/// digits (would wrap modulo 2^64 in the shell's arithmetic read).
fn is_valid_pct(val: &str) -> bool {
    if val.is_empty() || !val.bytes().all(|b| b.is_ascii_digit()) {
        return false;
    }
    if val.len() > 1 && val.starts_with('0') {
        return false;
    }
    if val.len() > 3 {
        return false;
    }
    let n: u64 = val.parse().unwrap_or(0);
    n <= 100
}

/// Audits a single repo and returns its report text (possibly empty when
/// nothing is actionable and nothing is dangling), mirroring
/// ref/audit-worktrees.sh:42-240.
pub fn audit_repo(repo: &Path, opts: &AuditOpts) -> String {
    let mut out = String::new();

    let base = match detect_base(repo) {
        Some(b) => b,
        None => {
            out.push_str(
                "  (skipped: no main/master/origin-HEAD found, can't determine base branch)\n",
            );
            return out;
        }
    };

    // branch -> worktree path, from porcelain worktree list (parsed
    // line-by-line so paths containing spaces survive intact).
    let mut wt_path: HashMap<String, String> = HashMap::new();
    if let Ok(list) = git::worktree_list(repo) {
        for (path, _, branch) in list {
            if let Some(name) = branch.strip_prefix("refs/heads/") {
                wt_path.insert(name.to_string(), path);
            }
        }
    }

    // Upstream state via for-each-ref's own fields rather than grepping
    // human-oriented `branch -vv` text.
    let mut gone: HashMap<String, bool> = HashMap::new();
    let mut has_upstream: HashMap<String, bool> = HashMap::new();
    let rows = git::log_units(
        repo,
        &[
            "for-each-ref",
            "refs/heads",
            "--format=%(refname:short)%x1f%(upstream:short)%x1f%(upstream:track)",
        ],
    );
    for row in rows {
        if row.len() >= 3 {
            if !row[1].is_empty() {
                has_upstream.insert(row[0].clone(), true);
            }
            if row[2].contains("[gone]") {
                gone.insert(row[0].clone(), true);
            }
        }
    }

    let mut any = false;
    let mut inflight_count = 0u64;
    let mut safe: Vec<String> = Vec::new();
    let mut triage: Vec<String> = Vec::new();
    let mut archaeology: Vec<String> = Vec::new();
    let mut handsoff: Vec<String> = Vec::new();
    let mut unknown_merge: Vec<String> = Vec::new();
    let mut content_merged: Vec<String> = Vec::new();

    let branches = git::log_units(
        repo,
        &["for-each-ref", "refs/heads", "--format=%(refname:short)"],
    )
    .into_iter()
    .filter_map(|row| row.first().cloned())
    .collect::<Vec<String>>();

    for branch in &branches {
        if branch == &base {
            continue;
        }
        any = true;

        let path = wt_path.get(branch).cloned();

        // In-flight wins over every other bucket, including safe-to-clean.
        let age = activity_age_secs_for(repo, branch, path.as_deref().map(Path::new)).unwrap_or(0);
        if age < opts.inflight_secs {
            inflight_count += 1;
            continue;
        }

        // merge-base --is-ancestor: exit 1 = definitively not an ancestor
        // (unmerged); >1 = real error (unrelated histories, bad ref) - don't
        // guess, flag it.
        let ancestor = is_ancestor(repo, branch, &base);
        let merged = match ancestor {
            Ok(true) => true,
            Ok(false) => false,
            Err(_) => {
                unknown_merge.push(branch.clone());
                continue;
            }
        };

        let mut is_content_merged = false;
        let mut unscored = false;
        let mut reason = "unmerged".to_string();
        if !opts.no_content && !merged {
            match coverage_score(repo, &base, branch) {
                Ok(score) => {
                    if let Some(pct) = score.strip_prefix("SCORED ") {
                        let pct: u64 = pct.trim().parse().unwrap_or(0);
                        if pct >= opts.content_merge_threshold {
                            is_content_merged = true;
                        } else {
                            reason = format!("{reason}, SCORED {pct}");
                        }
                    } else if score.starts_with("UNSCORED") || score.starts_with("UNKNOWN") {
                        unscored = true;
                        reason = format!(
                            "{reason}, {}",
                            score.split_whitespace().next().unwrap_or("")
                        );
                    }
                }
                Err(_) => {
                    unscored = true;
                    reason = format!("{reason}, UNKNOWN");
                }
            }
        }

        // status: None = "none" (no worktree, no gitdir, or no lock file);
        // Some(live|stale|unknown) only when a lock file exists
        // (ref/audit-worktrees.sh:136-143).
        let status = match &path {
            Some(p) => worktree_lock_status(p),
            None => None,
        };

        let dirty = worktree_dirty_count(path.as_deref());

        let mut desc = branch.clone();
        if let Some(p) = &path {
            desc = format!("{desc}  (worktree: {p})");
        }
        desc = format!("{desc}  [idle {}]", human_age(age));

        if let Some(s) = status {
            if matches!(s, LockStatus::Live | LockStatus::Unknown) {
                handsoff.push(format!("{desc}  [lock: {s}]"));
            } else if merged {
                // Merged means 0 commits ahead, so there is nothing to lose -
                // but uncommitted files in the worktree are NOT in base and
                // would die with it. Send those to triage instead of calling
                // them safe.
                if dirty > 0 {
                    triage.push(format!(
                        "{desc} — merged, but {dirty} uncommitted file(s) would be lost"
                    ));
                } else {
                    safe.push(desc);
                }
            } else {
                if s == LockStatus::Stale {
                    reason = "unmerged, stale lock (dead session)".to_string();
                }
                if gone.get(branch).copied().unwrap_or(false) {
                    reason = format!("{reason}, upstream deleted");
                }
                if dirty > 0 {
                    reason = format!("{reason}, {dirty} uncommitted file(s)");
                }
                if is_content_merged {
                    // Carry the dirty-file detail through: this bucket routes
                    // to the strict archive path, which refuses a dirty
                    // worktree.
                    let mut cm_note = String::new();
                    if dirty > 0 {
                        cm_note = format!(", {dirty} uncommitted file(s)");
                    }
                    content_merged.push(format!(
                        "{desc} — content-merged (work already in base, different hash){cm_note}"
                    ));
                } else if unscored {
                    // Unscored or unknown coverage: needs-triage, never
                    // archaeology.
                    triage.push(format!("{desc} — {reason}"));
                } else if age >= opts.archaeology_secs
                    && !has_upstream.get(branch).copied().unwrap_or(false)
                {
                    archaeology.push(format!("{desc} — {reason}, never pushed"));
                } else {
                    triage.push(format!("{desc} — {reason}"));
                }
            }
        } else if merged {
            if dirty > 0 {
                triage.push(format!(
                    "{desc} — merged, but {dirty} uncommitted file(s) would be lost"
                ));
            } else {
                safe.push(desc);
            }
        } else {
            if gone.get(branch).copied().unwrap_or(false) {
                reason = format!("{reason}, upstream deleted");
            }
            if dirty > 0 {
                reason = format!("{reason}, {dirty} uncommitted file(s)");
            }
            if is_content_merged {
                let mut cm_note = String::new();
                if dirty > 0 {
                    cm_note = format!(", {dirty} uncommitted file(s)");
                }
                content_merged.push(format!(
                    "{desc} — content-merged (work already in base, different hash){cm_note}"
                ));
            } else if unscored {
                triage.push(format!("{desc} — {reason}"));
            } else if age >= opts.archaeology_secs
                && !has_upstream.get(branch).copied().unwrap_or(false)
            {
                archaeology.push(format!("{desc} — {reason}, never pushed"));
            } else {
                triage.push(format!("{desc} — {reason}"));
            }
        }
    }

    // Dangling worktree registrations (prunable): report, don't touch.
    // Porcelain emits "prunable <reason>", not a bare "prunable" line.
    let mut prunable: Vec<String> = Vec::new();
    if let Ok(out) = git::porcelain_worktree_list(repo) {
        let mut cur_path = String::new();
        for line in out.lines() {
            if let Some(rest) = line.strip_prefix("worktree ") {
                cur_path = rest.to_string();
            } else if line.starts_with("prunable") {
                prunable.push(cur_path.clone());
            }
        }
    }

    if !any && prunable.is_empty() {
        return out;
    }
    // Nothing actionable and nothing dangling: stay quiet rather than
    // printing a header for a repo whose only branches are in flight.
    if safe.is_empty()
        && triage.is_empty()
        && archaeology.is_empty()
        && handsoff.is_empty()
        && unknown_merge.is_empty()
        && prunable.is_empty()
        && content_merged.is_empty()
    {
        return out;
    }

    out.push_str(&format!("=== {} (base: {base}) ===\n", repo.display()));
    if !safe.is_empty() {
        out.push_str("  safe-to-clean (merged, clean):\n");
        for s in &safe {
            out.push_str(&format!("    {s}\n"));
        }
    }
    if !triage.is_empty() {
        out.push_str("  needs-triage:\n");
        for s in &triage {
            out.push_str(&format!("    {s}\n"));
        }
    }
    if !archaeology.is_empty() {
        out.push_str(&format!(
            "  archaeology (older than {}, never pushed — batch archive):\n",
            human_age(opts.archaeology_secs)
        ));
        for s in &archaeology {
            out.push_str(&format!("    {s}\n"));
        }
    }
    if !content_merged.is_empty() {
        out.push_str("  likely-content-merged (work already in base under a different hash — batch archive):\n");
        for s in &content_merged {
            out.push_str(&format!("    {s}\n"));
        }
    }
    if !handsoff.is_empty() {
        out.push_str("  hands-off (live or unrecognized lock — never touch):\n");
        for s in &handsoff {
            out.push_str(&format!("    {s}\n"));
        }
    }
    if !unknown_merge.is_empty() {
        out.push_str("  couldn't determine merge state (unrelated history / bad ref):\n");
        for s in &unknown_merge {
            out.push_str(&format!("    {s}\n"));
        }
    }
    if !prunable.is_empty() {
        out.push_str("  dangling worktree registrations (git worktree prune is safe):\n");
        for s in &prunable {
            out.push_str(&format!("    {s}\n"));
        }
    }
    if inflight_count > 0 {
        out.push_str(&format!(
            "  ({} in flight, active within {} — hidden)\n",
            inflight_count,
            human_age(opts.inflight_secs)
        ));
    }
    out.push('\n');
    out
}

/// Audits every git repo one level under each root (fd -H -t d '^\.git$'
/// --max-depth 2, deduped, repo = parent of .git), mirroring
/// ref/audit-worktrees.sh:242-250.
pub fn audit_sweep(roots: &[PathBuf], opts: &AuditOpts) -> String {
    let mut seen: Vec<PathBuf> = Vec::new();
    let mut out = String::new();
    for root in roots {
        let abs = match std::fs::canonicalize(root) {
            Ok(p) if p.is_dir() => p,
            _ => continue,
        };
        let gitdirs = fd_git_dirs(&abs);
        for gitdir in gitdirs {
            let Some(repo) = gitdir.parent() else {
                continue;
            };
            let repo = match std::fs::canonicalize(repo) {
                Ok(p) => p,
                Err(_) => continue,
            };
            if seen.contains(&repo) {
                continue;
            }
            seen.push(repo.clone());
            out.push_str(&audit_repo(&repo, opts));
        }
    }
    out
}

/// `fd -H -t d '^\.git$' <root> --max-depth 2` (ref/audit-worktrees.sh:250).
fn fd_git_dirs(root: &Path) -> Vec<PathBuf> {
    let out = Command::new("fd")
        .arg("-H")
        .arg("-t")
        .arg("d")
        .arg("^\\.git$")
        .arg(root)
        .arg("--max-depth")
        .arg("2")
        .output();
    let Ok(out) = out else {
        return Vec::new();
    };
    if !out.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(PathBuf::from)
        .collect()
}

/// `git merge-base --is-ancestor <branch> <base>`; `Ok(true)` = ancestor,
/// `Ok(false)` = rc 1 (not an ancestor), `Err` = rc > 1 (unrelated
/// histories, bad ref, ...).
fn is_ancestor(repo: &Path, branch: &str, base: &str) -> Result<bool, CliError> {
    let out = Command::new("git")
        .current_dir(repo)
        .args(["merge-base", "--is-ancestor", branch, base])
        .output()
        .map_err(|e| CliError::refusal(format!("git merge-base --is-ancestor failed: {e}")))?;
    match out.status.code() {
        Some(0) => Ok(true),
        Some(1) => Ok(false),
        _ => Err(CliError::refusal(format!(
            "git merge-base --is-ancestor {branch} {base} failed with {}",
            out.status
        ))),
    }
}

/// Lock status of a worktree's gitdir, mirroring ref/audit-worktrees.sh:137-143:
/// `None` when there is no gitdir or no lock file (the reference's "none").
fn worktree_lock_status(path: &str) -> Option<LockStatus> {
    let gitdir = git::rev_parse_abs_git_dir(Path::new(path)).ok()?;
    let lock = PathBuf::from(gitdir).join("locked");
    if !lock.is_file() {
        return None;
    }
    Some(crate::core::proc::lock_status(&lock))
}

/// Number of changed (modified, staged, deleted, or untracked) paths,
/// mirroring ref/lib.sh:96-100.
fn worktree_dirty_count(path: Option<&str>) -> u64 {
    let Some(path) = path else {
        return 0;
    };
    if !Path::new(path).is_dir() {
        return 0;
    }
    let out = Command::new("git")
        .current_dir(path)
        .args(["status", "--porcelain"])
        .output();
    match out {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).lines().count() as u64,
        _ => 0,
    }
}

/// main/master/origin-HEAD detection, mirroring ref/lib.sh:19-27.
fn detect_base(repo: &Path) -> Option<String> {
    for cand in ["main", "master"] {
        let out = Command::new("git")
            .current_dir(repo)
            .args([
                "show-ref",
                "--verify",
                "--quiet",
                &format!("refs/heads/{cand}"),
            ])
            .output()
            .ok()?;
        if out.status.success() {
            return Some(cand.to_string());
        }
    }
    let out = Command::new("git")
        .current_dir(repo)
        .args(["symbolic-ref", "refs/remotes/origin/HEAD"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout);
    s.trim()
        .strip_prefix("refs/remotes/origin/")
        .map(str::to_string)
}
