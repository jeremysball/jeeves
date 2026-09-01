//! Active-repo scan: repo discovery + per-repo branch/tree classification and
//! the content-coverage pass, mirroring ref/scan-active.sh, plus the TOON
//! emitter that renders the report.

use std::collections::{BTreeSet, HashSet};
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::core::git;
use crate::core::toon::{toon_str, toon_table, Cell};
use crate::worktrees::coverage;

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

/// The content-coverage pass, mirroring scan-active.sh:291-319 + 326-352.
/// Skips when content scoring is off (JEEVES_ORIENT_CONTENT_SCORING, then
/// legacy ORIENT_CONTENT_SCORING; "0" disables). For branches still unmerged
/// after the exact passes, runs the in-repo coverage verdict against the
/// resolved base; SCORED pct >= threshold reclassifies as content-merged with
/// detail `content: N% of its lines already in <base>`. UNSCORED/UNKNOWN
/// verdicts leave rows untouched. After scoring, the proved-beats-scored
/// closure runs once more so descendants of a freshly scored branch are
/// caught too.
pub fn score_pass(results: &mut [RepoScan], cfg: &ScoreCfg) {
    if !cfg.content_scoring {
        return;
    }
    for repo in results {
        let (base_branch, base_ref) = detect_base(&repo.path);
        let Some(base_ref) = base_ref else {
            continue;
        };
        let base_branch = base_branch.unwrap_or_else(|| base_ref.clone());
        for b in &mut repo.branches {
            if b.classification != Classification::PotentiallyOutstanding {
                continue;
            }
            let Ok(verdict) = coverage::coverage_score(&repo.path, &base_ref, &b.name) else {
                continue;
            };
            let Some(pct) = verdict.strip_prefix("SCORED ") else {
                continue;
            };
            let Ok(pct) = pct.parse::<u64>() else {
                continue;
            };
            if pct >= cfg.threshold {
                b.classification = Classification::ContentMerged;
                b.state = format!("content: {pct}% of its lines already in {base_branch}");
            }
        }
        proved_beats_scored(repo, Some(&base_branch));
        repo.alerts = repo
            .branches
            .iter()
            .filter(|b| b.classification == Classification::PotentiallyOutstanding)
            .count();
    }
}

/// The proved-beats-scored transitive closure, mirroring scan-active.sh:326-352
/// (the same rule as part 1's first closure, scan-active.sh:261-289): a branch
/// that is an ancestor of a proved-merged branch is proved, and one that is
/// only an ancestor of a content-scored branch is content-scored too.
fn proved_beats_scored(repo: &mut RepoScan, base_branch: Option<&str>) {
    let mut iter = 0;
    let mut changed = true;
    while changed && iter < 20 {
        changed = false;
        iter += 1;
        for i in 0..repo.branches.len() {
            if Some(repo.branches[i].name.as_str()) == base_branch {
                continue;
            }
            if repo.branches[i].classification != Classification::PotentiallyOutstanding {
                continue;
            }
            let mut scored_ancestor: Option<usize> = None;
            let mut proved = false;
            for j in 0..repo.branches.len() {
                if repo.branches[j].classification == Classification::PotentiallyOutstanding {
                    continue;
                }
                if is_ancestor(&repo.path, &repo.branches[i].name, &repo.branches[j].name) {
                    if repo.branches[j].classification == Classification::ContentMerged {
                        if scored_ancestor.is_none() {
                            scored_ancestor = Some(j);
                        }
                    } else {
                        let detail = format!(
                            "ancestor of {} ({})",
                            repo.branches[j].name, repo.branches[j].state
                        );
                        repo.branches[i].classification = Classification::Merged;
                        repo.branches[i].state = detail;
                        changed = true;
                        proved = true;
                        break;
                    }
                }
            }
            if !proved {
                if let Some(j) = scored_ancestor {
                    let detail = format!(
                        "ancestor of {} ({})",
                        repo.branches[j].name, repo.branches[j].state
                    );
                    repo.branches[i].classification = Classification::ContentMerged;
                    repo.branches[i].state = detail;
                    changed = true;
                }
            }
        }
    }
}

/// Knobs for the content-coverage pass, resolved from env like
/// scan-active.sh:122-144.
#[derive(Debug, Clone, Copy)]
pub struct ScoreCfg {
    pub content_scoring: bool,
    pub threshold: u64,
}

/// Resolves the scoring knobs: JEEVES_ORIENT_CONTENT_SCORING then legacy
/// ORIENT_CONTENT_SCORING ("0" skips), and the threshold via
/// JEEVES_CONTENT_MERGE_THRESHOLD then legacy
/// WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD, default 95. Invalid or 0 threshold
/// warns on stderr exactly like scan-active.sh:137-143 and falls back to 95.
pub fn resolve_score_cfg() -> ScoreCfg {
    let scoring = std::env::var("JEEVES_ORIENT_CONTENT_SCORING")
        .ok()
        .or_else(|| std::env::var("ORIENT_CONTENT_SCORING").ok())
        .map(|v| v != "0")
        .unwrap_or(true);
    let mut threshold = std::env::var("JEEVES_CONTENT_MERGE_THRESHOLD")
        .ok()
        .or_else(|| std::env::var("WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD").ok())
        .unwrap_or_else(|| "95".to_string());
    if threshold == "0" || !is_valid_pct(&threshold) {
        eprintln!(
            "warning: WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD must be an integer 1-100 with no leading zero, got '{threshold}'; using 95"
        );
        threshold = "95".to_string();
    }
    ScoreCfg {
        content_scoring: scoring,
        threshold: threshold.parse().unwrap_or(95),
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

/// Renders the TOON report, mirroring scan-active.sh:423-468.
pub fn emit(stats: &ScanStats, repos: &[RepoScan], since: &str, self_path: &str) -> String {
    let mut out = String::new();
    out.push_str(&format!("bin: {}\n", self_display(self_path)));
    out.push_str("description: Git repos with local commits in a window, with per-branch push and merge state\n");
    out.push_str(&format!("window: {}\n", toon_str(since)));

    if repos.is_empty() {
        out.push_str(&format!(
            "repos: 0 of {} scanned repos have commits since {}\n",
            stats.scanned,
            toon_str(since)
        ));
        out.push_str("help[1]:\n");
        out.push_str("  Run `scan-active.sh \"1 week ago\"` to widen the window\n");
        return out;
    }

    out.push_str(&format!(
        "count: {} of {} scanned repos active\n",
        repos.len(),
        stats.scanned
    ));
    out.push('\n');
    let rows: Vec<Vec<Cell>> = repos
        .iter()
        .map(|r| {
            vec![
                Cell::Str(r.path.to_string_lossy().into_owned()),
                Cell::Str(r.branch.clone()),
                Cell::Bare(r.tree.clone()),
                Cell::Bare(r.alerts.to_string()),
            ]
        })
        .collect();
    out.push_str(&toon_table(
        "repos",
        &["path", "branch", "tree", "alerts"],
        &rows,
    ));
    out.push('\n');

    for repo in repos {
        out.push('\n');
        out.push_str(&format!(
            "repo: {}\n",
            toon_str(&repo.path.to_string_lossy())
        ));
        if repo.branches.is_empty() {
            out.push_str("  branches: 0 non-base branches\n");
        } else {
            out.push_str(&format!(
                "  branches[{}]{{name,classification,detail}}:\n",
                repo.branches.len()
            ));
            for b in &repo.branches {
                out.push_str(&format!(
                    "    {},{},{}\n",
                    toon_str(&b.name),
                    toon_str(classification_str(b.classification)),
                    toon_str(&b.state)
                ));
            }
        }
        out.push_str(&format!(
            "  commits_all_branches: {} of {} in window\n",
            repo.commits.len(),
            repo.total
        ));
        out.push_str(&format!(
            "  commits_all_branches[{}]{{sha,subject,age}}:\n",
            repo.commits.len()
        ));
        for c in &repo.commits {
            out.push_str(&format!(
                "    {},{},{}\n",
                c.sha,
                toon_str(&c.subject),
                toon_str(&c.age)
            ));
        }
        if repo.commits.len() < repo.total {
            out.push_str("  help[1]:\n");
            out.push_str(&format!(
                "    Run `ORIENT_COMMIT_LIMIT={} scan-active.sh` to see all {}\n",
                repo.total, repo.total
            ));
        }
    }

    out.push('\n');
    out.push_str("help[3]:\n");
    out.push_str(
        "  Read the branches table, not the branch field, before claiming a repo is pushed\n",
    );
    out.push_str("  Treat content-merged as landed but scored, not proved: archive it with `archive-branch.sh --strict`, never `clean-safe.sh`\n");
    out.push_str("  Run `git -C <path> rev-list --left-right --count main...origin/main` to confirm a DIVERGED repo\n");
    out
}

fn classification_str(c: Classification) -> &'static str {
    match c {
        Classification::Merged => "merged",
        Classification::ContentMerged => "content-merged",
        Classification::PotentiallyOutstanding => "potentially outstanding",
    }
}

/// Collapses $HOME to ~ for the bin: line (AXI §10), mirroring
/// scan-active.sh:6-8 and roots.rs's self_display.
fn self_display(self_path: &str) -> String {
    let home = std::env::var("HOME").unwrap_or_default();
    if !home.is_empty() {
        if self_path == home {
            return "~".to_string();
        }
        if let Some(rest) = self_path.strip_prefix(&(home.clone() + "/")) {
            return format!("~/{rest}");
        }
    }
    self_path.to_string()
}

/// Runs `scan-active <since> [root ...]`: prints the TOON report.
pub fn run(args: &[String]) -> u8 {
    // AXI §6: reject unknown flags by name before doing any work; --help
    // always passes (scan-active.sh:48-58).
    for arg in args {
        if arg == "--help" {
            print_usage();
            return 0;
        }
        if arg.starts_with("--") {
            println!("error: unknown flag {arg} for `scan-active.sh`");
            println!("help: the only flag is --help; positional args are <since> [root ...]");
            return 2;
        }
    }

    let Some(since) = args.first() else {
        eprintln!("error: <since> is required");
        eprintln!("help: jeeves scan-active \"yesterday 00:00\" [root ...]");
        return 2;
    };

    // fd preflight (scan-active.sh:66-75): repo discovery is entirely `fd`,
    // and that call swallows stderr, so a missing `fd` must not read as a
    // quiet "0 of 0 scanned repos" scan.
    if !fd_on_path() {
        println!("error: fd not found on PATH");
        println!("help: repo discovery needs `fd`; install it or add its dir to PATH (mise installs are not on a bare cron PATH)");
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
    let cfg = resolve_score_cfg();
    score_pass(&mut results, &cfg);
    let self_path = std::env::args()
        .next()
        .unwrap_or_else(|| "jeeves".to_string());
    print!("{}", emit(&stats, &results, since, &self_path));
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

/// PATH resolvability of `fd`, mirroring the reference's `command -v fd`
/// preflight (scan-active.sh:71).
fn fd_on_path() -> bool {
    let Ok(path) = std::env::var("PATH") else {
        return false;
    };
    for dir in std::env::split_paths(&path) {
        if dir.join("fd").is_file() {
            return true;
        }
    }
    false
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
