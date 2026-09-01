use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn scratch_dir() -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let dir = std::env::temp_dir().join(format!("jeeves-safety-{}-{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn git(repo: &Path, args: &[&str]) -> String {
    let output = Command::new("git")
        .current_dir(repo)
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8_lossy(&output.stdout).trim().to_string()
}

fn git_success(repo: &Path, args: &[&str]) -> bool {
    Command::new("git")
        .current_dir(repo)
        .args(args)
        .output()
        .unwrap()
        .status
        .success()
}

fn init_repo(dir: &Path) -> PathBuf {
    let repo = dir.join("repo");
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q", "-b", "main"]);
    git(&repo, &["config", "user.email", "test@example.com"]);
    git(&repo, &["config", "user.name", "Jeeves Test"]);
    commit(&repo, "base.txt", "base\n", "base");
    repo
}

fn commit(repo: &Path, name: &str, contents: &str, message: &str) {
    std::fs::write(repo.join(name), contents).unwrap();
    git(repo, &["add", "."]);
    git(repo, &["commit", "-q", "-m", message]);
}

fn add_feature_worktree(dir: &Path, repo: &Path) -> PathBuf {
    git(repo, &["checkout", "-q", "-b", "feature"]);
    commit(repo, "feature.txt", "feature\n", "feature");
    git(repo, &["checkout", "-q", "main"]);
    let worktree = dir.join("feature-worktree");
    let worktree_arg = worktree.to_string_lossy().into_owned();
    git(repo, &["worktree", "add", "-q", &worktree_arg, "feature"]);
    worktree
}

fn run_jeeves(args: &[&str], state_dir: &Path, inflight_secs: &str) -> Output {
    Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .args(args)
        .env("JEEVES_STATE_DIR", state_dir)
        .env("JEEVES_AUDIT_INFLIGHT_SECS", inflight_secs)
        .env("WORKTREE_AUDIT_INFLIGHT_SECS", inflight_secs)
        .output()
        .unwrap()
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn archive_tags_then_deletes_branch_and_worktree() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    let worktree = add_feature_worktree(&dir, &repo);
    let tip = git(&repo, &["rev-parse", "refs/heads/feature"]);
    let short_tip = git(&repo, &["rev-parse", "--short", "refs/heads/feature"]);
    let repo_arg = repo.to_string_lossy().into_owned();

    let output = run_jeeves(&["archive", &repo_arg, "feature"], &dir.join("state"), "0");

    assert_eq!(output.status.code(), Some(0));
    assert_eq!(
        String::from_utf8_lossy(&output.stdout),
        format!(
            "start: ok\nprepare: ok\ncommit: ok\n  archived feature -> archive/feature ({short_tip})\n"
        )
    );
    assert_eq!(git(&repo, &["rev-parse", "refs/tags/archive/feature"]), tip);
    assert!(!git_success(
        &repo,
        &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
    ));
    assert!(!worktree.exists());

    let oid = short_tip.as_bytes();
    assert!((7..=40).contains(&oid.len()));
    assert!(oid.iter().all(u8::is_ascii_hexdigit));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn strict_archive_refuses_dirty_worktree_without_tagging_or_removing() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    let worktree = add_feature_worktree(&dir, &repo);
    std::fs::write(worktree.join("dirty.txt"), "uncommitted\n").unwrap();
    let repo_arg = repo.to_string_lossy().into_owned();

    let output = run_jeeves(
        &["archive", "--strict", &repo_arg, "feature"],
        &dir.join("state"),
        "0",
    );

    assert_eq!(output.status.code(), Some(1));
    assert!(stderr(&output).contains("strict mode does not archive a dirty worktree"));
    assert!(!git_success(
        &repo,
        &[
            "show-ref",
            "--verify",
            "--quiet",
            "refs/tags/archive/feature"
        ]
    ));
    assert!(worktree.is_dir());
    assert!(git_success(
        &repo,
        &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
    ));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn archive_refuses_base_branch() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    let repo_arg = repo.to_string_lossy().into_owned();

    let output = run_jeeves(&["archive", &repo_arg, "main"], &dir.join("state"), "0");

    assert_eq!(output.status.code(), Some(1));
    assert!(stderr(&output).contains("this is the base branch"));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn clean_refuses_unmerged_branch_without_deleting_anything() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    let worktree = add_feature_worktree(&dir, &repo);
    let repo_arg = repo.to_string_lossy().into_owned();

    let output = run_jeeves(&["clean", &repo_arg, "feature"], &dir.join("state"), "0");

    assert_eq!(output.status.code(), Some(1));
    assert!(stderr(&output).contains("not confirmed merged"));
    assert!(worktree.is_dir());
    assert!(git_success(
        &repo,
        &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
    ));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn clean_removes_merged_idle_clean_worktree_and_branch() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    let worktree = add_feature_worktree(&dir, &repo);
    git(
        &repo,
        &["merge", "-q", "--no-ff", "feature", "-m", "merge feature"],
    );
    let repo_arg = repo.to_string_lossy().into_owned();

    let output = run_jeeves(&["clean", &repo_arg, "feature"], &dir.join("state"), "0");

    assert_eq!(output.status.code(), Some(0));
    assert!(!worktree.exists());
    assert!(!git_success(
        &repo,
        &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
    ));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn clean_refuses_in_flight_branch() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    let worktree = add_feature_worktree(&dir, &repo);
    git(
        &repo,
        &["merge", "-q", "--no-ff", "feature", "-m", "merge feature"],
    );
    let repo_arg = repo.to_string_lossy().into_owned();

    let output = run_jeeves(
        &["clean", &repo_arg, "feature"],
        &dir.join("state"),
        "18446744073709551615",
    );

    assert_eq!(output.status.code(), Some(1));
    assert!(stderr(&output).contains("active"));
    assert!(worktree.is_dir());
    assert!(git_success(
        &repo,
        &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
    ));
    let _ = std::fs::remove_dir_all(&dir);
}
