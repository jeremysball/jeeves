//! Active-repo scan (part 1): repo discovery + per-repo branch/tree
//! classification, mirroring ref/scan-active.sh. Output formatting (the TOON
//! emitter) is part 2, and consumes the pub types declared here.

use std::collections::{BTreeSet, HashSet};
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::core::git;

/// One commit row from `git log --branches --since=<since>`.
#[derive(Debug, Clone)]
pub struct CommitRow {
    pub sha: String,
    pub subject: String,
    pub age: String,
}

/// Per-branch merge/push classification, mirroring scan-active.sh:354-400.
#[derive(Debug, Clone)]
pub struct BranchInfo {
    pub name: String,
    pub classification: Classification,
    /// The reference's `state` detail string (upstream wording, DIVERGED /
    /// unpushed / behind / no-upstream, plus the "not in <base_ref> ancestry"
    /// suffix). Empty for merged/content-merged branches.
    pub state: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Classification {
    Merged,
    ContentMerged,
    PotentiallyOutstanding,
}

/// One scanned repo that has commits in the window (the reference's `active`
/// entries plus the R_* associative arrays).
#[derive(Debug, Clone)]
pub struct RepoScan {
    pub path: PathBuf,
    pub branch: String,
    pub tree: String,
    pub alerts: usize,
    pub branches: Vec<BranchInfo>,
    /// Capped at COMMIT_LIMIT (default 15) rows; `total` is the true count.
    pub commits: Vec<CommitRow>,
    pub total: usize,
}

/// The summary line's `active of scanned` bookkeeping.
#[derive(Debug, Default)]
pub struct ScanStats {
    pub scanned: usize,
    pub active: usize,
}

/// TODO (next unit): the content-coverage pass. The reference
/// (scan-active.sh:291-319) runs `coverage_score` against the resolved
/// base_ref for branches still unmerged after the exact passes, reclassifying
/// SCORED pct >= threshold as content-merged. Takes `&mut` results so part 2's
/// caller can apply the verdicts in place.
#[allow(clippy::needless_pass_by_value)]
pub fn score_pass(_results: &mut [RepoScan]) {}

/// Runs `scan-active <since> [root ...]`: prints the one-line summary
/// `scan: <active> of <scanned> repos` (the real TOON emitter is next unit).
pub fn run(args: &[String]) -> u8 {
    let Some(since) = args.first() else {
        eprintln!("error: <since> is required");
        eprintln!("help: jeeves scan-active \"yesterday 00:00\" [root ...]");
        return 2;
    };
    if since == "--help" {
        print_usage();
        return 0;
    }
    if since.starts_with("--") {
        println!("error: unknown flag {since} for `scan-active`");
        return 2;
    }

    let roots: Vec<PathBuf> = if args.len() > 1 {
        args[1..].iter().map(PathBuf::from).collect()
    } else {
        resolve_default_roots()
    };

    let commit_limit = std::env::var("ORIENT_COMMIT_LIMIT")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(15);

    let (stats, mut results) = scan(roots, since, commit_limit);
    score_pass(&mut results);
    println!("scan: {} of {} repos", stats.active, stats.scanned);
    0
}

fn print_usage() {
    println!("usage: scan-active.sh <since> [root ...]");
    println!();
    println!("arguments:");
    println!("  <since>   any `git log --since` expression, e.g. \"yesterday 00:00\"");
    println!("  [root]    dirs to scan; defaults to the discovered roots file, else");
    println!("            $ORIENT_ROOTS (space/colon separated), else /workspace");
}

/// Roots resolution, mirroring scan-active.sh:80-94: explicit args win, then
/// the roots file (canonical JEEVES_ROOTS_FILE, then legacy ORIENT_ROOTS_FILE,
/// default `$XDG_STATE_HOME/jeeves/roots.txt` then the legacy orient path),
/// then $ORIENT_ROOTS, then /workspace.
pub fn resolve_default_roots() -> Vec<PathBuf> {
    let state_home = std::env::var_os("XDG_STATE_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let home = std::env::var_os("HOME").unwrap_or_default();
            PathBuf::from(home).join(".local/state")
        });
    let canonical_file = std::env::var_os("JEEVES_ROOTS_FILE")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| state_home.join("jeeves/roots.txt"));
    let legacy_file = std::env::var_os("ORIENT_ROOTS_FILE")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| state_home.join("orient/roots.txt"));

    let roots_file = if canonical_file.is_file() && !is_empty(&canonical_file) {
        canonical_file
    } else if legacy_file.is_file() && !is_empty(&legacy_file) {
        legacy_file
    } else {
        return env_roots();
    };

    let Ok(content) = std::fs::read_to_string(&roots_file) else {
        return env_roots();
    };
    let roots: Vec<PathBuf> = content
        .lines()
        .map(|line| line.trim())
        .filter(|line| !line.is_empty())
        .map(PathBuf::from)
        .collect();
    if roots.is_empty() {
        env_roots()
    } else {
        roots
    }
}

fn is_empty(path: &Path) -> bool {
    std::fs::metadata(path)
        .map(|meta| meta.len() == 0)
        .unwrap_or(true)
}

fn env_roots() -> Vec<PathBuf> {
    if let Some(value) = std::env::var("ORIENT_ROOTS")
        .ok()
        .filter(|value| !value.is_empty())
    {
        return value
            .split([':', ' '])
            .filter(|part| !part.is_empty())
            .map(PathBuf::from)
            .collect();
    }
    vec![PathBuf::from("/workspace")]
}

/// Discovery + per-repo classification, mirroring scan-active.sh:146-420.
/// The content-score pass is stubbed out (`score_pass`) until the next unit.
/// Returns the `active of scanned` bookkeeping plus every active repo's data
/// for part 2's emitter.
pub fn scan(roots: Vec<PathBuf>, since: &str, commit_limit: usize) -> (ScanStats, Vec<RepoScan>) {
    let mut stats = ScanStats::default();
    let mut seen_repos: HashSet<PathBuf> = HashSet::new();
    let mut results: Vec<RepoScan> = Vec::new();

    for root in roots {
        if !root.is_dir() {
            continue;
        }
        for gitdir in fd_git_dirs(&root) {
            let Some(repo) = gitdir.parent() else {
                continue;
            };
            if !seen_repos.insert(repo.to_path_buf()) {
                continue;
            }
            stats.scanned += 1;

            let mut subjects = git::log_units(
                repo,
                &[
                    "log",
                    "--branches",
                    "--since",
                    since,
                    "--format=%h%x1f%s%x1f%cr",
                ],
            );
            if subjects.is_empty() {
                continue;
            }

            let branch = checked_out_branch(repo);
            let tree = if worktree_dirty(repo) {
                "dirty"
            } else {
                "clean"
            };

            let branches = classify(repo);

            let total = subjects.len();
            if subjects.len() > commit_limit {
                subjects.truncate(commit_limit);
            }
            let commits: Vec<CommitRow> = subjects
                .into_iter()
                .map(|row| CommitRow {
                    sha: row.first().cloned().unwrap_or_default(),
                    subject: row.get(1).cloned().unwrap_or_default(),
                    age: row.get(2).cloned().unwrap_or_default(),
                })
                .collect();

            let alerts = branches
                .iter()
                .filter(|b| b.classification == Classification::PotentiallyOutstanding)
                .count();

            stats.active += 1;
            results.push(RepoScan {
                path: repo.to_path_buf(),
                branch,
                tree: tree.to_string(),
                alerts,
                branches,
                commits,
                total,
            });
        }
    }
    (stats, results)
}

/// fd discovery, mirroring scan-active.sh:420 (stderr swallowed so unreadable
/// dirs stay quiet). Returns `.git` directories, parented to repos by caller.
fn fd_git_dirs(root: &Path) -> Vec<PathBuf> {
    let Ok(output) = Command::new("fd")
        .args([
            "-t",
            "d",
            "-d",
            "4",
            "-H",
            "-E",
            "node_modules",
            "-E",
            ".cache",
            "^.git$",
        ])
        .arg(root)
        .stderr(std::process::Stdio::null())
        .output()
    else {
        return Vec::new();
    };
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter(|line| !line.is_empty())
        .map(PathBuf::from)
        .collect()
}

fn checked_out_branch(repo: &Path) -> String {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["branch", "--show-current"])
        .output();
    let Ok(out) = out else {
        return "(detached)".to_string();
    };
    if !out.status.success() {
        return "(detached)".to_string();
    }
    let branch = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if branch.is_empty() {
        "(detached)".to_string()
    } else {
        branch
    }
}

fn worktree_dirty(repo: &Path) -> bool {
    let Ok(out) = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["status", "--short"])
        .output()
    else {
        return false;
    };
    out.status.success() && !out.stdout.is_empty()
}

/// Per-repo branch topology, mirroring scan-active.sh:172-352: base detection
/// (local main/master, then origin/main/master, then origin/HEAD), branch
/// tree collection, the ancestry + tree-match first pass, and the transitive
/// closure with proved-beats-scored. The content pass is stubbed to the next
/// unit. Returns every non-base branch; the caller derives alerts.
fn classify(repo: &Path) -> Vec<BranchInfo> {
    let (base_branch, base_ref) = detect_base(repo);

    let branch_names = local_branches(repo);
    let tree_of = |branch: &str| rev_parse_tree(repo, branch);

    let mut base_trees: BTreeSet<String> = BTreeSet::new();
    if let Some(base_branch) = &base_branch {
        for bref in [
            format!("refs/heads/{base_branch}"),
            format!("refs/remotes/origin/{base_branch}"),
            "refs/remotes/origin/HEAD".to_string(),
        ] {
            for row in git::log_units(repo, &["log", bref.as_str(), "--format=%T"]) {
                if let Some(tree) = row.first().filter(|tree| !tree.is_empty()) {
                    base_trees.insert(tree.clone());
                }
            }
        }
    }

    // First pass: ancestry or tree match (scan-active.sh:238-250).
    let mut merged: Vec<(String, String, bool)> = Vec::new();
    for b in &branch_names {
        if Some(b.as_str()) == base_branch.as_deref() {
            continue;
        }
        if let Some(base_ref) = &base_ref {
            if is_ancestor(repo, b, base_ref) {
                merged.push((b.clone(), format!("ancestry of {base_ref}"), false));
                continue;
            }
        }
        if let Some(tree) = tree_of(b) {
            if base_trees.contains(&tree) {
                let detail = match &base_ref {
                    Some(base_ref) => format!("squash: tree {tree} in {base_ref} history"),
                    None => format!("squash: tree {tree} in base history"),
                };
                merged.push((b.clone(), detail, false));
            }
        }
    }

    // Transitive closure, proved-beats-scored (scan-active.sh:261-289).
    let mut iter = 0;
    let mut changed = true;
    while changed && iter < 20 {
        changed = false;
        iter += 1;
        for b in &branch_names {
            if Some(b.as_str()) == base_branch.as_deref() {
                continue;
            }
            if merged.iter().any(|(name, _, _)| name == b) {
                continue;
            }
            let mut scored_ancestor: Option<(String, String)> = None;
            let mut proved = false;
            for (c, detail, is_content) in &merged {
                if is_ancestor(repo, b, c) {
                    if *is_content {
                        if scored_ancestor.is_none() {
                            scored_ancestor = Some((c.clone(), detail.clone()));
                        }
                    } else {
                        merged.push((b.clone(), format!("ancestor of {c} ({detail})"), false));
                        changed = true;
                        proved = true;
                        break;
                    }
                }
            }
            if !proved {
                if let Some((c, detail)) = scored_ancestor {
                    merged.push((b.clone(), format!("ancestor of {c} ({detail})"), true));
                    changed = true;
                }
            }
        }
    }

    let mut result = Vec::new();
    for b in branch_names {
        if Some(b.as_str()) == base_branch.as_deref() {
            continue;
        }
        if let Some((_, detail, is_content)) = merged.iter().find(|(name, _, _)| *name == b) {
            result.push(BranchInfo {
                classification: if *is_content {
                    Classification::ContentMerged
                } else {
                    Classification::Merged
                },
                state: detail.clone(),
                name: b,
            });
        } else {
            let state = upstream_state(repo, &b, base_ref.as_deref());
            result.push(BranchInfo {
                classification: Classification::PotentiallyOutstanding,
                state,
                name: b,
            });
        }
    }
    result
}

/// Base detection, mirroring scan-active.sh:179-202: local main/master, then
/// origin/main/master, then origin/HEAD (last resort that finds a base not
/// called main/master).
fn detect_base(repo: &Path) -> (Option<String>, Option<String>) {
    for cand in ["main", "master"] {
        if show_ref_verify(repo, &format!("refs/heads/{cand}")) {
            return (Some(cand.to_string()), Some(cand.to_string()));
        }
    }
    for cand in ["main", "master"] {
        if show_ref_verify(repo, &format!("refs/remotes/origin/{cand}")) {
            return (Some(cand.to_string()), Some(format!("origin/{cand}")));
        }
    }
    let Ok(out) = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
        .output()
    else {
        return (None, None);
    };
    if out.status.success() {
        let name = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if let Some(rest) = name.strip_prefix("refs/remotes/origin/") {
            return (Some(rest.to_string()), Some(format!("origin/{rest}")));
        }
    }
    (None, None)
}

fn show_ref_verify(repo: &Path, refname: &str) -> bool {
    let Ok(out) = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["show-ref", "-q", "--verify", refname])
        .output()
    else {
        return false;
    };
    out.status.success()
}

fn local_branches(repo: &Path) -> Vec<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["for-each-ref", "--format=%(refname:short)", "refs/heads"])
        .output();
    let Ok(out) = out else {
        return Vec::new();
    };
    if !out.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter(|line| !line.is_empty())
        .map(|line| line.to_string())
        .collect()
}

fn rev_parse_tree(repo: &Path, rev: &str) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["rev-parse", &format!("{rev}^{{tree}}")])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let tree = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if tree.is_empty() {
        None
    } else {
        Some(tree)
    }
}

fn is_ancestor(repo: &Path, a: &str, b: &str) -> bool {
    let Ok(out) = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["merge-base", "--is-ancestor", a, b])
        .output()
    else {
        return false;
    };
    out.status.success()
}

/// Upstream push state wording, copied verbatim from scan-active.sh:376-397.
/// `ahead`/`behind` come from `git rev-list --left-right --count b...up`; the
/// reference's `cut -f1` is `b..up` order (left = commits on b not on up).
fn upstream_state(repo: &Path, branch: &str, base_ref: Option<&str>) -> String {
    let up = upstream_of(repo, branch);
    let mut state = if let Some(up) = &up {
        let counts = left_right_count(repo, branch, up);
        let (ahead, behind) = counts.unwrap_or((0, 0));
        if ahead > 0 && behind > 0 {
            format!("DIVERGED from {up} (+{ahead}/-{behind}); push rejected, merge first")
        } else if ahead > 0 {
            format!("unpushed: {ahead}")
        } else if behind > 0 {
            format!("behind {up} by {behind}")
        } else {
            String::new()
        }
    } else {
        "no upstream; exists only on this disk".to_string()
    };

    if let Some(base_ref) = base_ref {
        let not_in = rev_list_count(repo, &format!("{base_ref}..{branch}")).unwrap_or(0);
        if not_in > 0 {
            if !state.is_empty() {
                state.push_str("; ");
            }
            state.push_str(&format!("not in {base_ref} ancestry: {not_in}"));
        }
    }
    state
}

fn upstream_of(repo: &Path, branch: &str) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args([
            "for-each-ref",
            "--format=%(upstream:short)",
            &format!("refs/heads/{branch}"),
        ])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let up = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if up.is_empty() {
        None
    } else {
        Some(up)
    }
}

/// `git rev-list --left-right --count branch...up`, left = ahead of upstream.
fn left_right_count(repo: &Path, branch: &str, up: &str) -> Option<(usize, usize)> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args([
            "rev-list",
            "--left-right",
            "--count",
            &format!("{branch}...{up}"),
        ])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8_lossy(&out.stdout);
    let mut parts = s.split_whitespace();
    let ahead = parts.next()?.parse().ok()?;
    let behind = parts.next()?.parse().ok()?;
    Some((ahead, behind))
}

fn rev_list_count(repo: &Path, range: &str) -> Option<usize> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["rev-list", "--count", range])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    String::from_utf8_lossy(&out.stdout).trim().parse().ok()
}
