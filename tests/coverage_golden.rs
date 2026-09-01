//! Golden parity: `jeeves coverage` must reproduce ref/coverage-score
//! verdict-for-verdict on fixture repos. The reference is spawned at test
//! time (AUDIT_WORKTREES_LIB pointed at ref/lib.sh), so the expected output
//! is generated, never hardcoded.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn scratch_dir() -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let d = std::env::temp_dir().join(format!("jeeves-cov-{}-{n}", std::process::id()));
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

/// The reference CLI, resolved from CARGO_MANIFEST_DIR (the sandbox cannot
/// hardcode an absolute fallback path).
fn ref_coverage_score() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(".superpowers/crispy/rust-rewrite/ref/coverage-score")
}

fn ref_lib() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(".superpowers/crispy/rust-rewrite/ref/lib.sh")
}

fn run_ref(repo: &Path, base: &str, branch: &str) -> Output {
    Command::new("bash")
        .arg(ref_coverage_score())
        .arg(repo)
        .arg(base)
        .arg(branch)
        .env("AUDIT_WORKTREES_LIB", ref_lib())
        .output()
        .unwrap()
}

fn run_rust(repo: &Path, base: &str, branch: &str) -> Output {
    Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("coverage")
        .arg(repo)
        .arg(base)
        .arg(branch)
        .output()
        .unwrap()
}

fn stdout_of(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// The heart of the phase: the Rust verdict string must equal the reference
/// stdout, and both must exit 0.
fn assert_parity(repo: &Path, base: &str, branch: &str) {
    let reference = run_ref(repo, base, branch);
    let rust = run_rust(repo, base, branch);
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
        stdout_of(&rust),
        stdout_of(&reference),
        "verdict mismatch for {base}..{branch}"
    );
}

/// (a) squash-merged onto an advanced base: SCORED 100 while
/// merge-base --is-ancestor says false.
#[test]
fn squash_merged_onto_advanced_base_scores_100() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);
    commit(&repo, "b.txt", "b\n", "unrelated main commit");
    git(&repo, &["merge", "-q", "--squash", "feature"]);
    git(&repo, &["commit", "-qm", "squash: add feat (#1)"]);

    let branch_sha = git(&repo, &["rev-parse", "feature"]);
    let ancestor = Command::new("git")
        .current_dir(&repo)
        .args(["merge-base", "--is-ancestor", &branch_sha, "main"])
        .status()
        .unwrap();
    assert!(!ancestor.success(), "ancestry must say false on this shape");

    assert_parity(&repo, "main", "feature");
    assert_eq!(stdout_of(&run_rust(&repo, "main", "feature")), "SCORED 100");
    let _ = std::fs::remove_dir_all(&dir);
}

/// (b) genuinely unmerged feature branch: low SCORED, asserted by parity with
/// the reference (never hardcoded).
#[test]
fn genuinely_unmerged_branch_scores_low() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    let mut content = String::new();
    for i in 0..20 {
        content.push_str(&format!("line {i}\n"));
    }
    commit(&repo, "open.txt", &content, "real unshipped work");
    git(&repo, &["checkout", "-q", "main"]);

    assert_parity(&repo, "main", "feature");
    let verdict = stdout_of(&run_rust(&repo, "main", "feature"));
    assert!(
        verdict.starts_with("SCORED "),
        "expected SCORED, got {verdict}"
    );
    let score: i64 = verdict["SCORED ".len()..].parse().unwrap();
    assert!(score < 50, "unmerged work must score low, got {verdict}");
    let _ = std::fs::remove_dir_all(&dir);
}

/// (c) empty patch branch: UNSCORED no-text-rows.
#[test]
fn empty_patch_is_unscored_no_text_rows() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    git(&repo, &["commit", "-q", "--allow-empty", "-m", "empty"]);

    assert_parity(&repo, "main", "feature");
    assert_eq!(
        stdout_of(&run_rust(&repo, "main", "feature")),
        "UNSCORED no-text-rows"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (d) binary file added: UNSCORED binary.
#[test]
fn binary_file_is_unscored_binary() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    std::fs::write(repo.join("blob.bin"), [0x00, 0x01, 0x02, 0xff]).unwrap();
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-qm", "binary"]);

    assert_parity(&repo, "main", "feature");
    assert_eq!(
        stdout_of(&run_rust(&repo, "main", "feature")),
        "UNSCORED binary"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (e) chmod-only change: UNSCORED mode-only.
#[test]
fn chmod_only_is_unscored_mode_only() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    let mut perms = std::fs::metadata(repo.join("a.txt")).unwrap().permissions();
    use std::os::unix::fs::PermissionsExt;
    perms.set_mode(perms.mode() | 0o111);
    std::fs::set_permissions(repo.join("a.txt"), perms).unwrap();
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-qm", "chmod"]);

    assert_parity(&repo, "main", "feature");
    assert_eq!(
        stdout_of(&run_rust(&repo, "main", "feature")),
        "UNSCORED mode-only"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (f) unrelated history: UNKNOWN no-merge-base.
#[test]
fn unrelated_history_is_unknown_no_merge_base() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "--orphan", "other"]);
    commit(&repo, "b.txt", "b\n", "other");

    assert_parity(&repo, "main", "other");
    assert_eq!(
        stdout_of(&run_rust(&repo, "main", "other")),
        "UNKNOWN no-merge-base"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (g) conflicting merge: UNKNOWN merge-conflict. Both sides add a line at
/// the same position so O > 0 and merge-tree actually conflicts.
#[test]
fn conflicting_merge_is_unknown_merge_conflict() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "one\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "a.txt", "one\nfeature\n", "feature change");
    git(&repo, &["checkout", "-q", "main"]);
    commit(&repo, "a.txt", "one\nmain\n", "main change");

    assert_parity(&repo, "main", "feature");
    assert_eq!(
        stdout_of(&run_rust(&repo, "main", "feature")),
        "UNKNOWN merge-conflict"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// (h) scoring from a LINKED worktree's path: `--git-path objects` must
/// resolve the real object store (the per-worktree gitdir has no `objects/`),
/// so Rust's verdict equals the reference's. A squashed-merged branch plus an
/// extra main commit makes feat genuinely unmerged (SCORED 0 from the main
/// repo path, reference parity asserted on both).
#[test]
fn linked_worktree_path_scores_like_main_repo() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");
    let init_sha = git(&repo, &["rev-parse", "HEAD"]);
    git(&repo, &["checkout", "-q", "-b", "old"]);
    commit(&repo, "old.txt", "old work\n", "old work");
    git(&repo, &["checkout", "-q", "main"]);
    git(&repo, &["merge", "-q", "--squash", "old"]);
    git(&repo, &["commit", "-qm", "squash: old (#1)"]);
    commit(&repo, "extra.txt", "extra\n", "extra main commit");

    git(&repo, &["checkout", "-q", "-b", "feat", &init_sha]);
    commit(&repo, "feat.txt", "feat work\n", "feat work");
    git(&repo, &["checkout", "-q", "main"]);

    let wt = dir.join("wt");
    git(
        &repo,
        &["worktree", "add", "-q", &wt.to_string_lossy(), "feat"],
    );

    assert_parity(&repo, "main", "feat");
    assert_parity(&wt, "main", "feat");
    assert_eq!(stdout_of(&run_rust(&wt, "main", "feat")), "SCORED 0");

    git(
        &repo,
        &["worktree", "remove", "--force", &wt.to_string_lossy()],
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// Usage errors mirror ref/coverage-score: exact strings on stdout, exit 2.
#[test]
fn usage_errors_match_reference() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    commit(&repo, "a.txt", "a\n", "init");

    let bin = env!("CARGO_BIN_EXE_jeeves");

    let out = Command::new(bin)
        .args(["coverage", "--bogus", "x", "y", "z"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert_eq!(stdout_of(&out), "error: unknown flag --bogus");

    let out = Command::new(bin)
        .args(["coverage", &repo.to_string_lossy(), "main"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert_eq!(
        stdout_of(&out),
        "error: usage: coverage-score <repo> <base> <branch>"
    );

    let missing = dir.join("missing-repo");
    let missing_arg = missing.to_string_lossy().into_owned();
    let out = Command::new(bin)
        .args(["coverage", &missing_arg, "main", "feature"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert_eq!(
        stdout_of(&out),
        format!("error: not a directory: {missing_arg}")
    );

    let _ = std::fs::remove_dir_all(&dir);
}
