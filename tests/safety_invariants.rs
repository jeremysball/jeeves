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

fn commit_at(repo: &Path, name: &str, contents: &str, message: &str, date: &str) {
    std::fs::write(repo.join(name), contents).unwrap();
    git(repo, &["add", "."]);
    let output = Command::new("git")
        .current_dir(repo)
        .env("GIT_AUTHOR_DATE", date)
        .env("GIT_COMMITTER_DATE", date)
        .args(["commit", "-q", "-m", message])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git commit failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
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

fn ref_archive_branch() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(".superpowers/crispy/rust-rewrite/ref/archive-branch.sh")
}

struct ArchiveKnobs {
    inflight_secs: &'static str,
    archive_prefix: &'static str,
}

fn apply_archive_env(cmd: &mut Command, knobs: &ArchiveKnobs, state_dir: &Path) {
    cmd.env("JEEVES_AUDIT_INFLIGHT_SECS", knobs.inflight_secs)
        .env("WORKTREE_AUDIT_INFLIGHT_SECS", knobs.inflight_secs)
        .env("JEEVES_AUDIT_ARCHIVE_PREFIX", knobs.archive_prefix)
        .env("WORKTREE_AUDIT_ARCHIVE_PREFIX", knobs.archive_prefix)
        .env("JEEVES_STATE_DIR", state_dir);
}

fn run_ref_archive(
    repo: &Path,
    branches: &[&str],
    knobs: &ArchiveKnobs,
    state_dir: &Path,
) -> Output {
    let mut command = Command::new("bash");
    command.arg(ref_archive_branch()).arg(repo).args(branches);
    apply_archive_env(&mut command, knobs, state_dir);
    command.output().unwrap()
}

fn run_rust_archive(
    repo: &Path,
    branches: &[&str],
    knobs: &ArchiveKnobs,
    state_dir: &Path,
) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_jeeves"));
    command.arg("archive").arg(repo).args(branches);
    apply_archive_env(&mut command, knobs, state_dir);
    command.output().unwrap()
}

fn deterministic_archive_fixture(dir: &Path) -> (PathBuf, PathBuf) {
    let repo = dir.join("repo");
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q", "-b", "main"]);
    git(&repo, &["config", "user.email", "test@example.com"]);
    git(&repo, &["config", "user.name", "Jeeves Test"]);
    let old = "2000-01-01T00:00:00Z";
    commit_at(&repo, "base.txt", "base\n", "base", old);
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit_at(&repo, "feature.txt", "feature\n", "feature", old);
    git(&repo, &["checkout", "-q", "main"]);
    let worktree = dir.join("feature-worktree");
    let worktree_path = worktree.to_string_lossy().into_owned();
    git(&repo, &["worktree", "add", "-q", &worktree_path, "feature"]);
    (repo, worktree)
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
fn archive_success_stdout_matches_reference() {
    let reference_dir = scratch_dir();
    let (reference_repo, reference_worktree) = deterministic_archive_fixture(&reference_dir);
    let rust_dir = scratch_dir();
    let (rust_repo, rust_worktree) = deterministic_archive_fixture(&rust_dir);
    let knobs = ArchiveKnobs {
        inflight_secs: "0",
        archive_prefix: "retained",
    };
    let reference_tip = git(&reference_repo, &["rev-parse", "refs/heads/feature"]);
    let short_tip = git(
        &reference_repo,
        &["rev-parse", "--short", "refs/heads/feature"],
    );

    let reference = run_ref_archive(
        &reference_repo,
        &["feature"],
        &knobs,
        &reference_dir.join("state"),
    );
    let rust = run_rust_archive(&rust_repo, &["feature"], &knobs, &rust_dir.join("state"));

    assert_eq!(reference.status.code(), Some(0));
    assert_eq!(
        rust.status.code(),
        Some(0),
        "rust archive failed: {}",
        stderr(&rust)
    );
    assert_eq!(rust.stdout, reference.stdout, "archive stdout mismatch");
    assert_eq!(
        String::from_utf8_lossy(&rust.stdout),
        format!(
            "start: ok\nprepare: ok\ncommit: ok\n  archived feature -> retained/feature ({short_tip})\n"
        )
    );

    for (repo, worktree) in [
        (&reference_repo, &reference_worktree),
        (&rust_repo, &rust_worktree),
    ] {
        assert_eq!(
            git(repo, &["rev-parse", "refs/tags/retained/feature"]),
            reference_tip
        );
        assert!(!git_success(
            repo,
            &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
        ));
        assert!(!worktree.exists());
    }

    let _ = std::fs::remove_dir_all(&reference_dir);
    let _ = std::fs::remove_dir_all(&rust_dir);
}

#[test]
fn archive_missing_branch_skip_keeps_success_status() {
    let reference_dir = scratch_dir();
    let (reference_repo, reference_worktree) = deterministic_archive_fixture(&reference_dir);
    let rust_dir = scratch_dir();
    let (rust_repo, rust_worktree) = deterministic_archive_fixture(&rust_dir);
    let knobs = ArchiveKnobs {
        inflight_secs: "0",
        archive_prefix: "retained",
    };

    let reference = run_ref_archive(
        &reference_repo,
        &["feature", "does-not-exist"],
        &knobs,
        &reference_dir.join("state"),
    );
    let rust = run_rust_archive(
        &rust_repo,
        &["feature", "does-not-exist"],
        &knobs,
        &rust_dir.join("state"),
    );

    assert_eq!(reference.status.code(), Some(0));
    assert_eq!(rust.status.code(), Some(0));
    assert_eq!(rust.stdout, reference.stdout, "archive stdout mismatch");
    assert!(String::from_utf8_lossy(&reference.stderr).contains("skip does-not-exist"));
    assert!(stderr(&rust).contains("skip does-not-exist"));
    assert!(!git_success(
        &reference_repo,
        &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
    ));
    assert!(!git_success(
        &rust_repo,
        &["show-ref", "--verify", "--quiet", "refs/heads/feature"]
    ));
    assert!(!reference_worktree.exists());
    assert!(!rust_worktree.exists());

    let _ = std::fs::remove_dir_all(&reference_dir);
    let _ = std::fs::remove_dir_all(&rust_dir);
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
