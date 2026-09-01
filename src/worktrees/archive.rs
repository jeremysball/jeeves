use std::ffi::{OsStr, OsString};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use crate::core::config;
use crate::core::git;
use crate::core::proc::{lock_status, LockStatus};
use crate::core::time::{activity_age_secs_for, human_age};

const DEFAULT_IN_FLIGHT_SECS: u64 = 7_200;
const DEFAULT_ARCHIVE_PREFIX: &str = "archive";
const WIP_MESSAGE: &str = "wip: archived with uncommitted changes";

/// Parse and execute the `archive` subcommand arguments.
pub fn run(args: &[String]) -> u8 {
    if args.first().map(String::as_str) == Some("--list") {
        let Some(repo) = args.get(1) else {
            eprintln!("usage: archive-branch.sh --list <repo-path>");
            return 1;
        };
        return list(Path::new(repo));
    }

    let strict = args.first().map(String::as_str) == Some("--strict");
    let offset = usize::from(strict);
    if args.len() <= offset + 1 {
        eprintln!("usage: archive-branch.sh <repo-path> <branch> [<branch>...]");
        return 1;
    }

    let repo = Path::new(&args[offset]);
    archive_branches(repo, &args[offset + 1..], strict)
}

/// List tags below the configured archive prefix.
pub fn list(repo: &Path) -> u8 {
    let prefix = Settings::load().archive_prefix;
    let Some(root) = repo_root(repo) else {
        return 1;
    };
    let tag_prefix = format!("refs/tags/{prefix}");
    let Some(output) = git_output(
        &root,
        [
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short)  %(objectname:short)  %(creatordate:short)",
            &tag_prefix,
        ],
    ) else {
        return 1;
    };
    if !output.status.success() {
        return 1;
    }
    print_bytes(&output.stdout);
    0
}

/// Archive each named local branch, returning one when any branch is refused.
pub fn archive_branches(repo: &Path, branches: &[String], strict: bool) -> u8 {
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

        let path = worktree_path_for_branch(&root, branch);
        if path.as_deref() == Some(primary.as_str()) && !primary.is_empty() {
            eprintln!("refusing {branch}: checked out in the primary worktree ({primary}).");
            eprintln!(
                "    Archiving it would have to switch that checkout to {base}. Do it by hand if that's what you want."
            );
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

        let mut gitdir = None;
        if let Some(path) = path_ref {
            if let Ok(found) = git::rev_parse_abs_git_dir(path) {
                let found = PathBuf::from(found);
                let lock = found.join("locked");
                if lock.is_file() {
                    let status = lock_status(&lock);
                    if status != LockStatus::Stale {
                        eprintln!(
                            "refusing {branch}: worktree lock is {status}, not provably dead"
                        );
                        rc = 1;
                        continue;
                    }
                }
                gitdir = Some(found);
            }

            let dirty = worktree_dirty_count(path);
            if dirty > 0 {
                if strict {
                    eprintln!(
                        "refusing {branch}: strict mode does not archive a dirty worktree ({dirty} uncommitted file(s) in {})",
                        path.display()
                    );
                    rc = 1;
                    continue;
                }
                println!("  {branch}: committing {dirty} uncommitted file(s) before archiving");
                if !commit_worktree(path) {
                    eprintln!(
                        "refusing {branch}: couldn't commit its uncommitted changes — not deleting anything"
                    );
                    rc = 1;
                    continue;
                }
            }
        }

        let tag = format!("{}/{}", settings.archive_prefix, branch);
        let tag_ref = format!("refs/tags/{tag}");
        if git_success(&root, ["show-ref", "--verify", "--quiet", &tag_ref]) {
            eprintln!("refusing {branch}: tag {tag} already exists");
            rc = 1;
            continue;
        }

        let Some(tip) = git::rev_parse(&root, &branch_ref).ok() else {
            rc = 1;
            continue;
        };

        if strict {
            let transaction = format!(
                "start\nverify {branch_ref} {tip}\ncreate {tag_ref} {tip}\nprepare\ncommit\n"
            );
            if !update_ref(&root, &transaction) {
                eprintln!(
                    "refusing {branch}: branch moved during archive (atomic tag aborted) — not deleting"
                );
                rc = 1;
                continue;
            }
        } else {
            let source_ref = format!("refs/heads/{branch}");
            if !git_success(&root, ["tag", &tag, &source_ref]) {
                rc = 1;
                continue;
            }
            let tag_tip = format!("{tag_ref}^{{commit}}");
            if git::rev_parse(&root, &tag_tip).ok().as_deref() != Some(tip.as_str()) {
                eprintln!("refusing {branch}: tag {tag} doesn't resolve to {tip} — not deleting");
                rc = 1;
                continue;
            }
        }

        let current_tip = git::rev_parse(&root, &branch_ref).ok();
        if current_tip.as_deref() != Some(tip.as_str()) {
            let now = rev_parse_short(&root, &branch_ref).unwrap_or_default();
            eprintln!(
                "refusing {branch}: branch advanced after tagging (now {now}, expected {tip}) — worktree/branch left in place"
            );
            rc = 1;
            continue;
        }

        if let Some(path) = path_ref {
            if gitdir
                .as_deref()
                .is_some_and(|gitdir| gitdir.join("locked").is_file())
            {
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

        let transaction = format!("start\ndelete {branch_ref} {tip}\nprepare\ncommit\n");
        if !update_ref(&root, &transaction) {
            eprintln!(
                "refusing {branch}: branch advanced during archive (conditional delete aborted) — tag {tag} kept, branch left in place"
            );
            rc = 1;
            continue;
        }

        let short_tip = rev_parse_short(&root, &tip).unwrap_or_else(|| tip.clone());
        println!("  archived {branch} -> {tag} ({short_tip})");
    }

    prune(&root);
    rc
}

struct Settings {
    in_flight_secs: u64,
    archive_prefix: String,
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
        let archive_prefix = config::resolve(
            &None,
            &[
                "JEEVES_AUDIT_ARCHIVE_PREFIX",
                "WORKTREE_AUDIT_ARCHIVE_PREFIX",
            ],
            &config,
            DEFAULT_ARCHIVE_PREFIX,
        );
        Self {
            in_flight_secs,
            archive_prefix,
        }
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

fn commit_worktree(path: &Path) -> bool {
    git_success(path, ["add", "-A"]) && git_success(path, ["commit", "-q", "-m", WIP_MESSAGE])
}

fn rev_parse_short(repo: &Path, revision: &str) -> Option<String> {
    let output = git_output(repo, ["rev-parse", "--short", revision])?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn update_ref(repo: &Path, transaction: &str) -> bool {
    let mut command = Command::new("git");
    command
        .arg("-C")
        .arg(repo)
        .args(["update-ref", "--stdin"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    let Ok(mut child) = command.spawn() else {
        return false;
    };
    let write_ok = child
        .stdin
        .take()
        .map(|mut stdin| stdin.write_all(transaction.as_bytes()).is_ok())
        .unwrap_or(false);
    let status_ok = child.wait().map(|status| status.success()).unwrap_or(false);
    write_ok && status_ok
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

fn print_bytes(bytes: &[u8]) {
    let text = String::from_utf8_lossy(bytes);
    print!("{text}");
}

fn print_bytes_to_stderr(bytes: &[u8]) {
    let text = String::from_utf8_lossy(bytes);
    eprint!("{text}");
}
