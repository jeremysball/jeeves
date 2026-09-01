//! Golden parity: `jeeves audit` must reproduce ref/audit-worktrees.sh
//! output-for-output on fixture repos. The reference is spawned at test time
//! with AUDIT_WORKTREES_LIB pointed at ref/lib.sh, so the expected output is
//! generated, never hardcoded. Both sides run with the SAME knob env:
//! canonical (JEEVES_AUDIT_*) and legacy (WORKTREE_AUDIT_*) names set to
//! identical values, mirroring how the binary resolves them.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn scratch_dir() -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let d = std::env::temp_dir().join(format!("jeeves-audit-{}-{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn git(repo: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .current_dir(repo)
        .args(args)
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

fn init_repo(dir: &Path) -> PathBuf {
    let repo = dir.join("repo");
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q", "-b", "main"]);
    git(&repo, &["config", "user.email", "t@t"]);
    git(&repo, &["config", "user.name", "t"]);
    repo
}

fn commit(repo: &Path, fname: &str, content: &str, msg: &str) {
    std::fs::write(repo.join(fname), content).unwrap();
    git(repo, &["add", "."]);
    git(repo, &["commit", "-qm", msg]);
}

/// Audit knobs, identical on both sides of every comparison. `None`
/// threshold means the env var is left unset (default 95 on both sides).
struct Knobs {
    inflight_secs: &'static str,
    archaeology_secs: &'static str,
    content_merge_threshold: Option<&'static str>,
}

/// The reference CLI, resolved from CARGO_MANIFEST_DIR (the sandbox cannot
/// hardcode an absolute fallback path).
fn ref_audit_worktrees() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(".superpowers/crispy/rust-rewrite/ref/audit-worktrees.sh")
}

fn ref_lib() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(".superpowers/crispy/rust-rewrite/ref/lib.sh")
}

fn apply_env(cmd: &mut Command, knobs: &Knobs, scratch: &Path) {
    cmd.env("JEEVES_AUDIT_INFLIGHT_SECS", knobs.inflight_secs)
        .env("WORKTREE_AUDIT_INFLIGHT_SECS", knobs.inflight_secs)
        .env("JEEVES_AUDIT_ARCHAEOLOGY_SECS", knobs.archaeology_secs)
        .env("WORKTREE_AUDIT_ARCHAEOLOGY_SECS", knobs.archaeology_secs)
        .env("AUDIT_WORKTREES_LIB", ref_lib())
        .env("JEEVES_STATE_DIR", scratch.join("state"));
    if let Some(t) = knobs.content_merge_threshold {
        cmd.env("WORKTREE_AUDIT_CONTENT_MERGE_THRESHOLD", t);
    }
}

fn run_ref(repo_parent: &Path, knobs: &Knobs) -> Output {
    let mut cmd = Command::new("bash");
    cmd.arg(ref_audit_worktrees()).arg(repo_parent);
    apply_env(&mut cmd, knobs, repo_parent);
    cmd.output().unwrap()
}

fn run_rust(repo_parent: &Path, knobs: &Knobs) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_jeeves"));
    cmd.arg("audit").arg(repo_parent);
    apply_env(&mut cmd, knobs, repo_parent);
    cmd.output().unwrap()
}

fn stdout_of(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// The heart of the suite: the Rust report must byte-match the reference
/// stdout, and both must exit 0.
fn assert_parity(repo_parent: &Path, knobs: &Knobs) {
    let reference = run_ref(repo_parent, knobs);
    let rust = run_rust(repo_parent, knobs);
    assert_eq!(
        reference.status.code(),
        Some(0),
        "reference exited nonzero: {}",
        String::from_utf8_lossy(&reference.stderr)
    );
    assert_eq!(
        rust.status.code(),
        Some(0),
        "rust exited nonzero: {}",
        String::from_utf8_lossy(&rust.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&rust.stdout),
        String::from_utf8_lossy(&reference.stdout),
        "audit report mismatch"
    );
}

/// (1) clean unmerged branch with no worktree: needs-triage with a SCORED
/// reason (the branch's work is provably not in base).
#[test]
fn clean_unmerged_branch_needs_triage() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);

    let knobs = Knobs {
        inflight_secs: "0",
        archaeology_secs: "7776000",
        content_merge_threshold: None,
    };
    assert_parity(&dir, &knobs);
    let out = stdout_of(&run_rust(&dir, &knobs));
    assert!(
        out.contains("  needs-triage:")
            && out.contains("feature  [idle 0m]")
            && out.contains("unmerged, SCORED 0"),
        "expected needs-triage line, got:\n{out}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (2) merged branch with a worktree holding two uncommitted files: not
/// safe-to-clean; the uncommitted work is not in base and would die with it.
#[test]
fn merged_dirty_worktree_needs_triage_uncommitted() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);
    git(
        &repo,
        &["merge", "-q", "--no-ff", "feature", "-m", "merge feature"],
    );

    let wt = dir.join("wt");
    git(
        &repo,
        &["worktree", "add", "-q", &wt.to_string_lossy(), "feature"],
    );
    std::fs::write(wt.join("dirty1.txt"), "dirty\n").unwrap();
    std::fs::write(wt.join("dirty2.txt"), "dirty\n").unwrap();

    let knobs = Knobs {
        inflight_secs: "0",
        archaeology_secs: "7776000",
        content_merge_threshold: None,
    };
    assert_parity(&dir, &knobs);
    let out = stdout_of(&run_rust(&dir, &knobs));
    assert!(
        out.contains("  needs-triage:")
            && out.contains("feature  (worktree: ")
            && out.contains(")  [idle 0m]")
            && out.contains("merged, but 2 uncommitted file(s) would be lost"),
        "expected merged-but-dirty triage line, got:\n{out}"
    );
    git(
        &repo,
        &["worktree", "remove", "--force", &wt.to_string_lossy()],
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (3) squash-merged onto an advanced base plus one extra base commit:
/// `merge-base --is-ancestor` says false, but the content provably landed
/// (SCORED 100 >= threshold), so the branch is offered for batch archive.
#[test]
fn squash_merged_onto_advanced_base_is_content_merged() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);
    commit(&repo, "b.txt", "b\n", "unrelated main commit");
    git(&repo, &["merge", "-q", "--squash", "feature"]);
    git(&repo, &["commit", "-qm", "squash: add feat (#1)"]);
    commit(&repo, "extra.txt", "extra\n", "extra main commit");

    let branch_sha = git(&repo, &["rev-parse", "feature"]);
    let ancestor = Command::new("git")
        .current_dir(&repo)
        .args(["merge-base", "--is-ancestor", &branch_sha, "main"])
        .status()
        .unwrap();
    assert!(!ancestor.success(), "ancestry must say false on this shape");

    let knobs = Knobs {
        inflight_secs: "0",
        archaeology_secs: "7776000",
        content_merge_threshold: Some("80"),
    };
    assert_parity(&dir, &knobs);
    let out = stdout_of(&run_rust(&dir, &knobs));
    assert!(
        out.contains("  likely-content-merged (")
            && out.contains("feature  [idle 0m]")
            && out.contains("content-merged (work already in base, different hash)"),
        "expected content-merged line, got:\n{out}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (4) no-upstream branch old enough for archaeology (legacy knob 0 on both
/// sides: every age qualifies) with a SCORED reason, no worktree.
#[test]
fn no_upstream_stale_branch_is_archaeology() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);

    let knobs = Knobs {
        inflight_secs: "0",
        archaeology_secs: "0",
        content_merge_threshold: None,
    };
    assert_parity(&dir, &knobs);
    let out = stdout_of(&run_rust(&dir, &knobs));
    assert!(
        out.contains("  archaeology")
            && out.contains("feature  [idle 0m]")
            && out.contains("unmerged, SCORED 0, never pushed"),
        "expected archaeology line, got:\n{out}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (5) in-flight repo only: a brand-new commit is active within the 7200s
/// threshold, so the report stays completely silent. The binary must print
/// NOTHING (exact empty stdout), and so must the reference.
#[test]
fn inflight_only_repo_prints_nothing() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);

    let knobs = Knobs {
        inflight_secs: "7200",
        archaeology_secs: "7776000",
        content_merge_threshold: None,
    };
    assert_parity(&dir, &knobs);
    let rust = run_rust(&dir, &knobs);
    let reference = run_ref(&dir, &knobs);
    assert!(
        rust.stdout.is_empty(),
        "binary must print nothing, got: {}",
        String::from_utf8_lossy(&rust.stdout)
    );
    assert!(
        reference.stdout.is_empty(),
        "reference must print nothing, got: {}",
        String::from_utf8_lossy(&reference.stdout)
    );
    let _ = std::fs::remove_dir_all(&dir);
}
