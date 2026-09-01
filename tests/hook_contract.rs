use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn scratch_dir() -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let dir = std::env::temp_dir().join(format!("jeeves-hook-{}-{n}", std::process::id()));
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

fn init_repo(dir: &Path) -> PathBuf {
    let repo = dir.join("repo");
    std::fs::create_dir_all(&repo).unwrap();
    git(&repo, &["init", "-q", "-b", "main"]);
    git(&repo, &["config", "user.email", "test@example.com"]);
    git(&repo, &["config", "user.name", "Jeeves Test"]);
    std::fs::write(repo.join("base.txt"), "base\n").unwrap();
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-q", "-m", "base"]);
    repo
}

fn run_hook(process_dir: &Path, payload: &str, state_dir: &Path) -> Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("session-hook")
        .current_dir(process_dir)
        .env("JEEVES_STATE_DIR", state_dir)
        .env("JEEVES_AUDIT_INFLIGHT_SECS", "0")
        .env("WORKTREE_AUDIT_INFLIGHT_SECS", "0")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(payload.as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
}

#[test]
fn repo_hook_emits_ordered_json_context_for_drift() {
    let dir = scratch_dir();
    let repo = init_repo(&dir);
    git(&repo, &["checkout", "-q", "-b", "feature"]);
    std::fs::write(repo.join("feature.txt"), "feature\n").unwrap();
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-q", "-m", "feature"]);
    git(&repo, &["checkout", "-q", "main"]);
    let payload = serde_json::json!({"cwd": repo}).to_string();

    let output = run_hook(&dir, &payload, &dir.join("state"));
    let stdout = String::from_utf8_lossy(&output.stdout);
    let prefix = "{\n  \"hookSpecificOutput\": {\n    \"hookEventName\": \"SessionStart\",\n    \"additionalContext\": \"";
    assert_eq!(output.status.code(), Some(0));
    assert!(
        stdout.starts_with(prefix),
        "unexpected hook output: {stdout}"
    );
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    let context = json["hookSpecificOutput"]["additionalContext"]
        .as_str()
        .unwrap();
    assert!(context.contains("Worktree drift"));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn empty_stdin_in_non_repo_is_silent_success() {
    let dir = scratch_dir();
    let output = run_hook(&dir, "", &dir.join("state"));

    assert_eq!(output.status.code(), Some(0));
    assert!(output.stdout.is_empty());
    let _ = std::fs::remove_dir_all(&dir);
}
