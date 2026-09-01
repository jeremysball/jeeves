use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use crate::core::config;
use crate::core::git;
use crate::core::proc::{lock_status, LockStatus};
use crate::core::time::{activity_age_secs_for, human_age};

const DEFAULT_IN_FLIGHT_SECS: u64 = 7_200;

/// Clean each named local branch, returning one when any branch is refused.
pub fn clean_branches(repo: &Path, branches: &[String]) -> u8 {
    let Some(root) = repo_root(repo) else {
        eprintln!(
            "refusing: can't determine base branch for {}",
            repo.display()
        );
        return 1;
    };

    let Some(base) = detect_base(&root) else {
        eprintln!(
            "refusing: can't determine base branch for {}",
            root.display()
        );
        prune(&root);
        return 1;
    };

    let primary = primary_worktree(&root).unwrap_or_default();
    let settings = Settings::load();
    let mut rc = 0;

    for branch in branches {
        if branch == &base {
            eprintln!("refusing {branch}: this is the base branch");
            rc = 1;
            continue;
        }

        let branch_ref = format!("refs/heads/{branch}");
        if !git_success(&root, ["show-ref", "--verify", "--quiet", &branch_ref]) {
            eprintln!("skip {branch}: no such local branch");
            continue;
        }

        let merge_rc = git_output(
            &root,
            [
                "merge-base",
                "--is-ancestor",
                branch.as_str(),
                base.as_str(),
            ],
        )
        .and_then(|output| output.status.code())
        .unwrap_or(1);
        if merge_rc != 0 {
            eprintln!(
                "refusing {branch}: not confirmed merged into {base} (re-check gave exit {merge_rc}) — do not delete"
            );
            rc = 1;
            continue;
        }

        let path = worktree_path_for_branch(&root, branch);
        if path.as_deref() == Some(primary.as_str()) && !primary.is_empty() {
            eprintln!("refusing {branch}: checked out in the primary worktree ({primary})");
            rc = 1;
            continue;
        }

        let path_ref = path.as_deref().map(Path::new);
        let age = activity_age_secs_for(&root, branch, path_ref).unwrap_or(0);
        if age < settings.in_flight_secs {
            eprintln!(
                "refusing {branch}: active {} ago, under the {} in-flight threshold",
                human_age(age),
                human_age(settings.in_flight_secs)
            );
            rc = 1;
            continue;
        }

        if let Some(path) = path_ref {
            let dirty = worktree_dirty_count(path);
            if dirty > 0 {
                eprintln!(
                    "refusing {branch}: {dirty} uncommitted file(s) in {} — merged, but these are in no commit and would be lost",
                    path.display()
                );
                rc = 1;
                continue;
            }
        }

        if let Some(path) = path_ref {
            let gitdir = git::rev_parse_abs_git_dir(path).ok().map(PathBuf::from);
            if let Some(gitdir) = gitdir {
                let lock = gitdir.join("locked");
                if lock.is_file() {
                    let status = lock_status(&lock);
                    if status != LockStatus::Stale {
                        eprintln!(
                            "refusing {branch}: worktree lock is {status}, not provably dead — do not delete"
                        );
                        rc = 1;
                        continue;
                    }

                    let Some(output) = git_output(
                        &root,
                        vec![
                            OsString::from("worktree"),
                            OsString::from("unlock"),
                            path.as_os_str().to_os_string(),
                        ],
                    ) else {
                        rc = 1;
                        continue;
                    };
                    if !output.status.success() {
                        print_bytes_to_stderr(&output.stderr);
                        rc = 1;
                        continue;
                    }
                }
            }

            let Some(output) = git_output(
                &root,
                vec![
                    OsString::from("worktree"),
                    OsString::from("remove"),
                    path.as_os_str().to_os_string(),
                ],
            ) else {
                rc = 1;
                continue;
            };
            if !output.status.success() {
                print_bytes_to_stderr(&output.stderr);
                rc = 1;
                continue;
            }
        }

        let Some(output) = git_output(&root, ["branch", "-d", branch.as_str()]) else {
            rc = 1;
            continue;
        };
        if !output.status.success() {
            print_bytes_to_stderr(&output.stderr);
            eprintln!(
                "refusing {branch}: git branch -d declined (see message above) — worktree removed, branch ref left for manual review"
            );
            rc = 1;
        }
    }

    prune(&root);
    rc
}

struct Settings {
    in_flight_secs: u64,
}

impl Settings {
    fn load() -> Self {
        let config = config::read_config(None);
        let in_flight_secs = config::resolve(
            &None,
            &["JEEVES_AUDIT_INFLIGHT_SECS", "WORKTREE_AUDIT_INFLIGHT_SECS"],
            &config,
            "7200",
        )
        .parse()
        .unwrap_or(DEFAULT_IN_FLIGHT_SECS);
        Self { in_flight_secs }
    }
}

fn repo_root(repo: &Path) -> Option<PathBuf> {
    git::rev_parse(repo, "--show-toplevel")
        .ok()
        .map(PathBuf::from)
}

fn detect_base(repo: &Path) -> Option<String> {
    for branch in ["main", "master"] {
        let branch_ref = format!("refs/heads/{branch}");
        if git_success(repo, ["show-ref", "--verify", "--quiet", &branch_ref]) {
            return Some(branch.to_owned());
        }
    }

    let output = git_output(
        repo,
        ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
    )?;
    if !output.status.success() {
        return None;
    }
    let symbolic = String::from_utf8_lossy(&output.stdout);
    symbolic
        .trim()
        .strip_prefix("refs/remotes/origin/")
        .map(str::to_owned)
}

fn primary_worktree(repo: &Path) -> Option<String> {
    let porcelain = git::porcelain_worktree_list(repo).ok()?;
    porcelain
        .lines()
        .find_map(|line| line.strip_prefix("worktree ").map(str::to_owned))
}

fn worktree_path_for_branch(repo: &Path, branch: &str) -> Option<String> {
    let porcelain = git::porcelain_worktree_list(repo).ok()?;
    let branch_line = format!("branch refs/heads/{branch}");
    let mut current_path = None;
    for line in porcelain.lines() {
        if let Some(path) = line.strip_prefix("worktree ") {
            current_path = Some(path.to_owned());
        } else if line == branch_line {
            return current_path;
        }
    }
    None
}

fn worktree_dirty_count(path: &Path) -> u64 {
    let Some(output) = git_output(path, ["status", "--porcelain"]) else {
        return 0;
    };
    if !output.status.success() {
        return 0;
    }
    output.stdout.iter().filter(|byte| **byte == b'\n').count() as u64
}

fn prune(repo: &Path) {
    let _ = git_success(repo, ["worktree", "prune"]);
}

fn git_success<I, S>(repo: &Path, args: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    git_output(repo, args)
        .map(|output| output.status.success())
        .unwrap_or(false)
}

fn git_output<I, S>(repo: &Path, args: I) -> Option<Output>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .ok()
}

fn print_bytes_to_stderr(bytes: &[u8]) {
    let text = String::from_utf8_lossy(bytes);
    eprint!("{text}");
}
