//! Golden parity: `jeeves scan-active` must reproduce ref/scan-active.sh
//! output-for-output on fixture workspaces. The reference is spawned at test
//! time with AUDIT_WORKTREES_LIB pointed at ref/lib.sh, so the expected
//! output is generated, never hardcoded. Both sides run with the SAME knob
//! env: ORIENT_COMMIT_LIMIT=1, JEEVES_ROOTS_FILE and ORIENT_ROOTS_FILE pointed
//! at a nonexistent temp path (so explicit roots win on both sides), and the
//! same AUDIT_WORKTREES_LIB. The first line (the bin: line) differs by
//! construction â the reference prints its own script path, the binary its
//! argv[0] â so both outputs are normalized by dropping it, and relative ages
//! are normalized to "N ago" on both sides.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn scratch_dir() -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let d = std::env::temp_dir().join(format!("jeeves-scan-{}-{n}", std::process::id()));
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

fn init_repo(dir: &Path, name: &str) -> PathBuf {
    let repo = dir.join(name);
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

fn ref_scan_active() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(".superpowers/crispy/rust-rewrite/ref/scan-active.sh")
}

fn ref_lib() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join(".superpowers/crispy/rust-rewrite/ref/lib.sh")
}

/// Knob env, identical on both sides of every comparison. The roots files are
/// pointed at a nonexistent path so the explicit [root ...] args win on both
/// sides, and the same AUDIT_WORKTREES_LIB enables the content pass on both.
fn apply_env(cmd: &mut Command, scratch: &Path) {
    cmd.env("ORIENT_COMMIT_LIMIT", "1")
        .env("AUDIT_WORKTREES_LIB", ref_lib())
        .env("JEEVES_ROOTS_FILE", scratch.join("no-roots.txt"))
        .env("ORIENT_ROOTS_FILE", scratch.join("no-roots.txt"));
}

fn run_ref(root: &Path, since: &str) -> Output {
    let mut cmd = Command::new("bash");
    cmd.arg(ref_scan_active()).arg(since).arg(root);
    apply_env(&mut cmd, root);
    cmd.output().unwrap()
}

fn run_rust(root: &Path, since: &str) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_jeeves"));
    cmd.arg("scan-active").arg(since).arg(root);
    apply_env(&mut cmd, root);
    cmd.output().unwrap()
}

/// Shared normalization for BOTH sides: drop the first line (the bin: line,
/// which differs by construction) and collapse relative ages to "N ago".
fn normalize(out: &Output) -> String {
    let text = String::from_utf8_lossy(&out.stdout);
    let mut lines = text.lines();
    lines.next();
    let body = lines.collect::<Vec<_>>().join("\n");
    let mut result = String::with_capacity(body.len());
    let mut rest = body.as_str();
    while let Some(start) = rest.find(|c: char| c.is_ascii_digit()) {
        result.push_str(&rest[..start]);
        rest = &rest[start..];
        let num_end = rest
            .find(|c: char| !c.is_ascii_digit())
            .unwrap_or(rest.len());
        let unit_start = rest[num_end..]
            .strip_prefix(' ')
            .map(|_| num_end + 1)
            .unwrap_or(rest.len());
        let unit_end = rest[unit_start..]
            .find(|c: char| !c.is_ascii_alphabetic())
            .map(|i| unit_start + i)
            .unwrap_or(rest.len());
        let unit = &rest[unit_start..unit_end];
        if matches!(
            unit,
            "second" | "seconds" | "minute" | "minutes" | "hour" | "hours" | "day" | "days"
        ) && rest[unit_end..].starts_with(" ago")
        {
            result.push_str("N ago");
            rest = &rest[unit_end + 4..];
        } else {
            result.push_str(&rest[..num_end]);
            rest = &rest[num_end..];
        }
    }
    result.push_str(rest);
    result
}

fn assert_parity(root: &Path, since: &str) {
    let reference = run_ref(root, since);
    let rust = run_rust(root, since);
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
        normalize(&rust),
        normalize(&reference),
        "scan-active report mismatch"
    );
}

/// (1) Two repos: A has a branch merged into main with --no-ff (merged,
/// context not an alert); B has a branch 2 commits ahead of main, never
/// pushed (potentially outstanding, unpushed). The count line and both
/// branches tables must match.
#[test]
fn merged_and_outstanding() {
    let dir = scratch_dir();
    let a = init_repo(&dir, "a");
    commit(&a, "a.txt", "a\n", "init");
    git(&a, &["checkout", "-q", "-b", "feature"]);
    commit(&a, "feat.txt", "feature work\n", "add feat");
    git(&a, &["checkout", "-q", "main"]);
    git(
        &a,
        &["merge", "-q", "--no-ff", "feature", "-m", "merge feature"],
    );

    let b = init_repo(&dir, "b");
    commit(&b, "a.txt", "a\n", "init");
    git(&b, &["checkout", "-q", "-b", "wip"]);
    commit(&b, "feat.txt", "feature work\n", "add feat");
    commit(&b, "feat2.txt", "more work\n", "add feat2");
    git(&b, &["checkout", "-q", "main"]);

    assert_parity(&dir, "10 years ago");
    let _ = std::fs::remove_dir_all(&dir);
}

/// (2) Squash merge with no base drift: the branch tip tree exists verbatim
/// in main's history, so the branch is proved merged by tree match and the
/// report says `squash: tree <sha> in main history`.
#[test]
fn tree_match() {
    let dir = scratch_dir();
    let repo = init_repo(&dir, "repo");
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);
    git(&repo, &["merge", "-q", "--squash", "feature"]);
    git(&repo, &["commit", "-qm", "squash: add feat (#1)"]);

    assert_parity(&dir, "10 years ago");
    let _ = std::fs::remove_dir_all(&dir);
}

/// (3) Same squash shape but main advanced on BOTH sides of the squash, so
/// the branch tip tree is in no main commit and ancestry never held: the
/// content pass scores it and the report says
/// `content: 100% of its lines already in main`.
#[test]
fn content_merged() {
    let dir = scratch_dir();
    let repo = init_repo(&dir, "repo");
    commit(&repo, "a.txt", "a\n", "init");
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    commit(&repo, "feat.txt", "feature work\n", "add feat");
    git(&repo, &["checkout", "-q", "main"]);
    commit(&repo, "b.txt", "b\n", "unrelated main commit");
    git(&repo, &["merge", "-q", "--squash", "feature"]);
    git(&repo, &["commit", "-qm", "squash: add feat (#1)"]);
    commit(&repo, "e1.txt", "e1\n", "extra main commit 1");
    commit(&repo, "e2.txt", "e2\n", "extra main commit 2");
    commit(&repo, "e3.txt", "e3\n", "extra main commit 3");
    commit(&repo, "e4.txt", "e4\n", "extra main commit 4");

    assert_parity(&dir, "10 years ago");
    let _ = std::fs::remove_dir_all(&dir);
}
