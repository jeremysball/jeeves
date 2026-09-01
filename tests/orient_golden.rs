//! Golden parity for the three small orient commands.

use serde_json::json;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn scratch_dir(prefix: &str) -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let dir = std::env::temp_dir().join(format!("jeeves-{prefix}-{}-{n}", std::process::id()));
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

fn init_repo(path: &Path) {
    std::fs::create_dir_all(path).unwrap();
    git(path, &["init", "-q", "-b", "main"]);
    git(path, &["config", "user.email", "t@t"]);
    git(path, &["config", "user.name", "t"]);
}

fn commit_at(repo: &Path, file: &str, contents: &str, message: &str, date: &str) {
    std::fs::write(repo.join(file), contents).unwrap();
    git(repo, &["add", "."]);
    let output = Command::new("git")
        .current_dir(repo)
        .env("GIT_AUTHOR_DATE", date)
        .env("GIT_COMMITTER_DATE", date)
        .args(["commit", "-qm", message])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git commit failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn ref_script(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(".superpowers/crispy/rust-rewrite/ref")
        .join(name)
}

fn run_ref(name: &str, args: &[&str]) -> Output {
    Command::new("bash")
        .arg(ref_script(name))
        .args(args)
        .output()
        .unwrap()
}

fn run_rust(command: &str, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg(command)
        .args(args)
        .output()
        .unwrap()
}

fn assert_stdout_parity(reference: &Output, rust: &Output, label: &str) {
    assert_eq!(
        reference.status.code(),
        rust.status.code(),
        "{label} exit status differs: ref stderr: {} / rust stderr: {}",
        String::from_utf8_lossy(&reference.stderr),
        String::from_utf8_lossy(&rust.stderr)
    );
    assert_eq!(
        rust.stdout,
        reference.stdout,
        "{label} stdout differs\nreference:\n{}\nrust:\n{}",
        String::from_utf8_lossy(&reference.stdout),
        String::from_utf8_lossy(&rust.stdout)
    );
}

#[test]
fn git_state_clean_dirty_and_error_cases_match_reference() {
    let dir = scratch_dir("git-state");
    let repo = dir.join("repo");
    init_repo(&repo);
    let old = "2020-01-02T03:04:05Z";
    for n in 0..6 {
        commit_at(
            &repo,
            &format!("commit-{n}.txt"),
            &format!("commit {n}\n"),
            &format!("commit {n}"),
            old,
        );
    }

    let repo_arg = repo.to_string_lossy().into_owned();
    let reference = run_ref("git-state.sh", &[&repo_arg]);
    let rust = run_rust("git-state", &[&repo_arg]);
    assert_stdout_parity(&reference, &rust, "git-state clean");

    std::fs::write(repo.join("commit-0.txt"), "changed\n").unwrap();
    std::fs::write(repo.join("untracked.txt"), "untracked\n").unwrap();
    let reference = run_ref("git-state.sh", &[&repo_arg]);
    let rust = run_rust("git-state", &[&repo_arg]);
    assert_stdout_parity(&reference, &rust, "git-state dirty");

    let plain = dir.join("plain");
    std::fs::create_dir_all(&plain).unwrap();
    let plain_arg = plain.to_string_lossy().into_owned();
    let reference = run_ref("git-state.sh", &[&plain_arg]);
    let rust = run_rust("git-state", &[&plain_arg]);
    assert_stdout_parity(&reference, &rust, "git-state non-git");

    let missing = dir.join("missing");
    let missing_arg = missing.to_string_lossy().into_owned();
    let reference = run_ref("git-state.sh", &[&missing_arg]);
    let rust = run_rust("git-state", &[&missing_arg]);
    assert_stdout_parity(&reference, &rust, "git-state cd failure");
    assert_eq!(rust.status.code(), Some(1));

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn roots_deduplicate_and_persist_with_matching_toon() {
    let dir = scratch_dir("roots");
    let tree = dir.join("tree");
    std::fs::create_dir_all(&tree).unwrap();

    let primary = tree.join("primary");
    init_repo(&primary);
    commit_at(
        &primary,
        "primary.txt",
        "primary\n",
        "primary",
        "2022-01-01T00:00:00Z",
    );
    git(
        &primary,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/team/project.git",
        ],
    );

    let linked = tree.join("linked");
    let linked_arg = linked.to_string_lossy().into_owned();
    git(
        &primary,
        &["worktree", "add", "-q", "-b", "linked", &linked_arg, "main"],
    );
    commit_at(
        &linked,
        "linked.txt",
        "linked\n",
        "linked newer commit",
        "2030-01-01T00:00:00Z",
    );

    let variant = tree.join("variant");
    init_repo(&variant);
    commit_at(
        &variant,
        "variant.txt",
        "variant\n",
        "variant",
        "2020-01-01T00:00:00Z",
    );
    git(
        &variant,
        &[
            "remote",
            "add",
            "origin",
            "git@example.com:team/project.git",
        ],
    );

    let no_origin = tree.join("no-origin");
    init_repo(&no_origin);
    commit_at(
        &no_origin,
        "local.txt",
        "local\n",
        "local only",
        "2025-01-01T00:00:00Z",
    );

    let state = dir.join("state");
    let reference_file = dir.join("reference-roots.txt");
    let rust_file = dir.join("rust-roots.txt");
    let tree_arg = tree.to_string_lossy().into_owned();

    let reference = Command::new("bash")
        .arg(ref_script("discover-roots.sh"))
        .env("ORIENT_ROOT_CANDIDATES", &tree_arg)
        .env("JEEVES_ROOT_CANDIDATES", dir.join("not-used"))
        .env("ORIENT_ROOTS_FILE", &reference_file)
        .env_remove("JEEVES_ROOTS_FILE")
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("roots")
        .env("ORIENT_ROOT_CANDIDATES", &tree_arg)
        .env("JEEVES_ROOT_CANDIDATES", dir.join("not-used"))
        .env_remove("ORIENT_ROOTS_FILE")
        .env("JEEVES_ROOTS_FILE", &rust_file)
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();

    assert_eq!(reference.status.code(), Some(0), "reference roots failed");
    assert_eq!(rust.status.code(), Some(0), "rust roots failed");
    assert_eq!(
        std::fs::read(&reference_file).unwrap(),
        std::fs::read(&rust_file).unwrap(),
        "roots file mismatch"
    );
    assert_eq!(
        normalize_toon(&reference.stdout),
        normalize_toon(&rust.stdout),
        "roots TOON mismatch\nreference:\n{}\nrust:\n{}",
        String::from_utf8_lossy(&reference.stdout),
        String::from_utf8_lossy(&rust.stdout)
    );
    assert!(String::from_utf8_lossy(&rust.stdout).contains("count: 1 distinct remotes"));
    assert!(String::from_utf8_lossy(&rust.stdout).contains(&format!("  {}", primary.display())));

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn roots_rewrite_existing_legacy_file_when_using_default_path() {
    let dir = scratch_dir("roots-migration");
    let tree = dir.join("tree");
    let repo = tree.join("repo");
    init_repo(&repo);
    commit_at(
        &repo,
        "file.txt",
        "content\n",
        "commit",
        "2022-01-01T00:00:00Z",
    );
    git(
        &repo,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/migration.git",
        ],
    );

    let state = dir.join("state");
    let legacy = state.join("orient/roots.txt");
    std::fs::create_dir_all(legacy.parent().unwrap()).unwrap();
    std::fs::write(&legacy, "stale\n").unwrap();
    let tree_arg = tree.to_string_lossy().into_owned();
    let output = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("roots")
        .arg(&tree_arg)
        .env_remove("JEEVES_ROOTS_FILE")
        .env_remove("ORIENT_ROOTS_FILE")
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(0));
    let canonical = state.join("jeeves/roots.txt");
    assert_eq!(
        std::fs::read(&canonical).unwrap(),
        std::fs::read(&legacy).unwrap()
    );

    let _ = std::fs::remove_dir_all(&dir);
}

fn normalize_toon(output: &[u8]) -> String {
    let text = String::from_utf8_lossy(output);
    let mut normalized = text
        .lines()
        .map(|line| {
            if line.starts_with("bin: ") {
                "bin: <dynamic>".to_string()
            } else if line.starts_with("roots_file: ") {
                "roots_file: <dynamic>".to_string()
            } else {
                line.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    if text.ends_with('\n') {
        normalized.push('\n');
    }
    normalized
}

#[test]
fn session_tail_filters_truncates_and_matches_reference() {
    let dir = scratch_dir("session-tail");
    let session = dir.join("session.jsonl");
    let long_text = "x".repeat(850);
    let lines = vec![
        json!({
            "timestamp": "2026-07-12T13:59:00Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "before"}]}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:00Z",
            "message": {"content": [{"type": "text", "text": "absent role"}]}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:00Z",
            "isSidechain": true,
            "message": {"content": [{"type": "text", "text": "sidechain"}]}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:00Z",
            "message": {"role": "assistant", "content": [{"type": "image", "source": "attachment"}]}
        })
        .to_string(),
        json!({
            "message": {"role": "assistant", "content": [{"type": "text", "text": "no timestamp"}]}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:01Z",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "first"},
                {"type": "image", "source": "attachment"},
                {"type": "text", "text": "second"}
            ]}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:02Z",
            "message": {"role": "user", "content": "plain content"}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:03Z",
            "message": {"role": "assistant", "content": {"kind": "object"}}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:04Z",
            "message": {"role": "system", "content": null}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:05Z",
            "message": {"role": "tool", "content": [{"type": "text", "text": "tool result"}]}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:06Z",
            "message": {"role": "user", "content": long_text}
        })
        .to_string(),
        json!({
            "timestamp": "2026-07-12T14:00:07Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "final"}]}
        })
        .to_string(),
    ];
    assert_eq!(lines.len(), 12);
    std::fs::write(&session, format!("{}\n", lines.join("\n"))).unwrap();

    let session_arg = session.to_string_lossy().into_owned();
    let since = "2026-07-12T14:00:00Z";
    let reference = run_ref("session-tail.sh", &[&session_arg, since, "4"]);
    let rust = run_rust("session-tail", &[&session_arg, since, "4"]);
    assert_stdout_parity(&reference, &rust, "session-tail filtered tail");

    let future = "2030-01-01T00:00:00Z";
    let reference = run_ref("session-tail.sh", &[&session_arg, future]);
    let rust = run_rust("session-tail", &[&session_arg, future]);
    assert_stdout_parity(&reference, &rust, "session-tail empty");
    assert!(rust.stdout.is_empty());

    let missing = dir.join("missing.jsonl");
    let missing_arg = missing.to_string_lossy().into_owned();
    let reference = run_ref("session-tail.sh", &[&missing_arg]);
    let rust = run_rust("session-tail", &[&missing_arg]);
    assert_stdout_parity(&reference, &rust, "session-tail missing file");
    assert_eq!(rust.status.code(), Some(1));

    let _ = std::fs::remove_dir_all(&dir);
}
