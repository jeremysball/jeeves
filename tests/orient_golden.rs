//! Golden parity for the three small orient commands.

use serde_json::json;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

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

fn run_with_stdin(program: &Path, args: &[&str], input: &str) -> Output {
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(input.as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
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

#[cfg(unix)]
#[test]
fn git_state_rejects_an_unenterable_directory_like_reference() {
    let uid = Command::new("id").arg("-u").output().unwrap();
    if String::from_utf8_lossy(&uid.stdout).trim() == "0" {
        eprintln!("skip: chmod-000 cd test is not meaningful as root");
        return;
    }

    use std::os::unix::fs::PermissionsExt;

    let dir = scratch_dir("git-state-permissions");
    let blocked = dir.join("blocked");
    std::fs::create_dir_all(&blocked).unwrap();
    std::fs::set_permissions(&blocked, std::fs::Permissions::from_mode(0o000)).unwrap();
    let blocked_arg = blocked.to_string_lossy().into_owned();

    let reference = run_ref("git-state.sh", &[&blocked_arg]);
    let rust = run_rust("git-state", &[&blocked_arg]);

    std::fs::set_permissions(&blocked, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert_stdout_parity(&reference, &rust, "git-state unenterable directory");
    assert_eq!(rust.status.code(), Some(1));
    assert_eq!(
        rust.stdout,
        format!("error: cannot cd to {blocked_arg}\n").as_bytes()
    );

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
        .env("JEEVES_ROOT_CANDIDATES", &tree_arg)
        .env("ORIENT_ROOTS_FILE", &reference_file)
        .env_remove("JEEVES_ROOTS_FILE")
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("roots")
        .env("ORIENT_ROOT_CANDIDATES", dir.join("not-used"))
        .env("JEEVES_ROOT_CANDIDATES", &tree_arg)
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
fn roots_environment_aliases_and_precedence_match_reference() {
    let dir = scratch_dir("roots-env");
    let canonical_tree = dir.join("canonical-tree");
    let legacy_tree = dir.join("legacy-tree");
    let canonical_repo = canonical_tree.join("canonical");
    let legacy_repo = legacy_tree.join("legacy");

    init_repo(&canonical_repo);
    commit_at(
        &canonical_repo,
        "canonical.txt",
        "canonical\n",
        "canonical",
        "2022-01-01T00:00:00Z",
    );
    git(
        &canonical_repo,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/env-canonical.git",
        ],
    );

    init_repo(&legacy_repo);
    commit_at(
        &legacy_repo,
        "legacy.txt",
        "legacy\n",
        "legacy",
        "2022-01-01T00:00:00Z",
    );
    git(
        &legacy_repo,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/env-legacy.git",
        ],
    );

    let state = dir.join("state");
    let reference_canonical_file = dir.join("reference-canonical.txt");
    let rust_canonical_file = dir.join("rust-canonical.txt");
    let rust_legacy_file = dir.join("rust-legacy.txt");
    let canonical_arg = canonical_tree.to_string_lossy().into_owned();
    let legacy_arg = legacy_tree.to_string_lossy().into_owned();

    let reference = Command::new("bash")
        .arg(ref_script("discover-roots.sh"))
        .env_remove("JEEVES_ROOT_CANDIDATES")
        .env("ORIENT_ROOT_CANDIDATES", &canonical_arg)
        .env_remove("JEEVES_ROOTS_FILE")
        .env("ORIENT_ROOTS_FILE", &reference_canonical_file)
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("roots")
        .env("JEEVES_ROOT_CANDIDATES", &canonical_arg)
        .env("ORIENT_ROOT_CANDIDATES", &legacy_arg)
        .env("JEEVES_ROOTS_FILE", &rust_canonical_file)
        .env("ORIENT_ROOTS_FILE", &rust_legacy_file)
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();

    assert_eq!(reference.status.code(), Some(0));
    assert_eq!(rust.status.code(), Some(0));
    assert_eq!(
        std::fs::read(&reference_canonical_file).unwrap(),
        std::fs::read(&rust_canonical_file).unwrap(),
        "canonical roots file mismatch"
    );
    assert_eq!(
        normalize_toon(&reference.stdout),
        normalize_toon(&rust.stdout),
        "roots canonical environment precedence"
    );
    assert!(!rust_legacy_file.exists(), "legacy file should not win");

    let reference_legacy_file = dir.join("reference-legacy.txt");
    let rust_legacy_only_file = dir.join("rust-legacy-only.txt");
    let reference = Command::new("bash")
        .arg(ref_script("discover-roots.sh"))
        .env_remove("JEEVES_ROOT_CANDIDATES")
        .env("ORIENT_ROOT_CANDIDATES", &legacy_arg)
        .env_remove("JEEVES_ROOTS_FILE")
        .env("ORIENT_ROOTS_FILE", &reference_legacy_file)
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("roots")
        .env_remove("JEEVES_ROOT_CANDIDATES")
        .env("ORIENT_ROOT_CANDIDATES", &legacy_arg)
        .env_remove("JEEVES_ROOTS_FILE")
        .env("ORIENT_ROOTS_FILE", &rust_legacy_only_file)
        .env("XDG_STATE_HOME", &state)
        .output()
        .unwrap();

    assert_eq!(reference.status.code(), rust.status.code());
    assert_eq!(
        normalize_toon(&reference.stdout),
        normalize_toon(&rust.stdout),
        "roots legacy-only environment"
    );
    assert_eq!(
        std::fs::read(&reference_legacy_file).unwrap(),
        std::fs::read(&rust_legacy_only_file).unwrap(),
        "legacy roots file mismatch"
    );
    assert!(String::from_utf8_lossy(&rust.stdout)
        .contains(&format!("roots_file: {}", rust_legacy_only_file.display())));

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn roots_fd_traversal_matches_reference() {
    let dir = scratch_dir("roots-fd");
    let tree = dir.join("tree");
    let visible = tree.join("visible");
    init_repo(&visible);
    commit_at(
        &visible,
        "visible.txt",
        "visible\n",
        "visible",
        "2022-01-01T00:00:00Z",
    );
    git(
        &visible,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/fd-visible.git",
        ],
    );

    let ignored = tree.join("node_modules/ignored");
    init_repo(&ignored);
    commit_at(
        &ignored,
        "ignored.txt",
        "ignored\n",
        "ignored",
        "2022-01-01T00:00:00Z",
    );
    git(
        &ignored,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/fd-node-modules.git",
        ],
    );

    let cached = tree.join(".cache/ignored");
    init_repo(&cached);
    commit_at(
        &cached,
        "cached.txt",
        "cached\n",
        "cached",
        "2022-01-01T00:00:00Z",
    );
    git(
        &cached,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/fd-cache.git",
        ],
    );

    let deep = tree.join("a/b/c/d/repo");
    init_repo(&deep);
    commit_at(&deep, "deep.txt", "deep\n", "deep", "2022-01-01T00:00:00Z");
    git(
        &deep,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/fd-too-deep.git",
        ],
    );

    let source = dir.join("source");
    init_repo(&source);
    commit_at(
        &source,
        "source.txt",
        "source\n",
        "source",
        "2022-01-01T00:00:00Z",
    );
    git(
        &source,
        &[
            "remote",
            "add",
            "origin",
            "https://example.com/fd-worktree.git",
        ],
    );
    let linked = tree.join("linked");
    let linked_arg = linked.to_string_lossy().into_owned();
    git(
        &source,
        &["worktree", "add", "-q", "-b", "linked", &linked_arg, "main"],
    );

    let reference_file = dir.join("reference.txt");
    let rust_file = dir.join("rust.txt");
    let tree_arg = tree.to_string_lossy().into_owned();
    let reference = Command::new("bash")
        .arg(ref_script("discover-roots.sh"))
        .arg(&tree_arg)
        .env("ORIENT_ROOTS_FILE", &reference_file)
        .env_remove("JEEVES_ROOTS_FILE")
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("roots")
        .arg(&tree_arg)
        .env("JEEVES_ROOTS_FILE", &rust_file)
        .env_remove("ORIENT_ROOTS_FILE")
        .output()
        .unwrap();

    assert_eq!(reference.status.code(), rust.status.code());
    assert_eq!(
        normalize_toon(&reference.stdout),
        normalize_toon(&rust.stdout),
        "roots fd traversal"
    );
    assert_eq!(
        std::fs::read(&reference_file).unwrap(),
        std::fs::read(&rust_file).unwrap(),
        "fd traversal roots file mismatch"
    );
    let stdout = String::from_utf8_lossy(&rust.stdout);
    assert!(stdout.contains(&format!("  {}", visible.display())));
    assert!(!stdout.contains(&ignored.display().to_string()));
    assert!(!stdout.contains(&cached.display().to_string()));
    assert!(!stdout.contains(&deep.display().to_string()));
    assert!(!stdout.contains(&linked.display().to_string()));

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

#[test]
fn session_tail_malformed_json_preserves_prior_output_and_reference_status() {
    let dir = scratch_dir("session-tail-malformed");
    let session = dir.join("session.jsonl");
    let before = json!({
        "timestamp": "2026-07-12T14:00:00Z",
        "message": {"role": "user", "content": "before"}
    });
    let after = json!({
        "timestamp": "2026-07-12T14:00:01Z",
        "message": {"role": "assistant", "content": "after"}
    });
    std::fs::write(&session, format!("{}\nnot json\n{}\n", before, after)).unwrap();

    let session_arg = session.to_string_lossy().into_owned();
    let reference = run_ref("session-tail.sh", &[&session_arg]);
    let rust = run_rust("session-tail", &[&session_arg]);
    assert_stdout_parity(&reference, &rust, "session-tail malformed JSON");
    assert_eq!(rust.status.code(), Some(5));
    assert!(String::from_utf8_lossy(&rust.stdout).contains("user: before"));
    assert!(!String::from_utf8_lossy(&rust.stdout).contains("assistant: after"));

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn session_tail_gnu_max_forms_and_diagnostics_match_reference() {
    let dir = scratch_dir("session-tail-max");
    let session = dir.join("session.jsonl");
    let lines: Vec<String> = (0..5)
        .map(|n| {
            json!({
                "timestamp": format!("2026-07-12T14:00:0{n}Z"),
                "message": {"role": "user", "content": format!("entry-{n}")}
            })
            .to_string()
        })
        .collect();
    std::fs::write(&session, format!("{}\n", lines.join("\n"))).unwrap();
    let session_arg = session.to_string_lossy().into_owned();

    for max in ["+2", "-2", "2", "0"] {
        let reference = run_ref("session-tail.sh", &[&session_arg, "", max]);
        let rust = run_rust("session-tail", &[&session_arg, "", max]);
        assert_stdout_parity(&reference, &rust, &format!("session-tail max {max}"));
    }

    let reference = run_ref("session-tail.sh", &[]);
    let rust = run_rust("session-tail", &[]);
    assert_stdout_parity(&reference, &rust, "session-tail missing argument");
    assert!(rust.stdout.is_empty());
    assert!(!rust.stderr.is_empty());

    let reference = run_ref("session-tail.sh", &[&session_arg, "", "invalid"]);
    let rust = run_rust("session-tail", &[&session_arg, "", "invalid"]);
    assert_stdout_parity(&reference, &rust, "session-tail invalid max");
    assert_eq!(rust.status.code(), Some(1));
    assert!(rust.stdout.is_empty());
    assert!(!rust.stderr.is_empty());

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn sessions_newest_claude_file_and_empty_case_match_reference() {
    let dir = scratch_dir("sessions");
    let home = dir.join("home");
    let project_dir = dir.join("project");
    let projects = home.join(".claude/projects");
    std::fs::create_dir_all(&project_dir).unwrap();
    let slug: String = project_dir
        .to_string_lossy()
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() {
                byte as char
            } else {
                '-'
            }
        })
        .collect();
    let claude_project = projects.join(slug);
    std::fs::create_dir_all(&claude_project).unwrap();
    let older = claude_project.join("older.jsonl");
    let newer = claude_project.join("newer.jsonl");
    std::fs::write(&older, "older\n").unwrap();
    std::thread::sleep(Duration::from_secs(1));
    std::fs::write(&newer, "newer\n").unwrap();

    let project_arg = project_dir.to_string_lossy().into_owned();
    let opencode_available = Command::new("sh")
        .args(["-c", "command -v opencode >/dev/null 2>&1"])
        .status()
        .unwrap()
        .success();
    let scan = if opencode_available { "0" } else { "12" };
    if opencode_available {
        eprintln!("note: opencode found; OpenCode session pass is disabled for parity test");
    } else {
        eprintln!("note: opencode absent; OpenCode session pass is skipped");
    }

    let reference = Command::new("bash")
        .arg(ref_script("session-discover.sh"))
        .arg(&project_arg)
        .env("HOME", &home)
        .env("ORIENT_OPENCODE_SCAN", scan)
        .env("JEEVES_ORIENT_OPENCODE_SCAN", scan)
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("sessions")
        .arg(&project_arg)
        .env("HOME", &home)
        .env("ORIENT_OPENCODE_SCAN", scan)
        .env("JEEVES_ORIENT_OPENCODE_SCAN", scan)
        .output()
        .unwrap();
    assert_stdout_parity(&reference, &rust, "sessions newest Claude file");
    assert_eq!(rust.status.code(), Some(0));
    assert_eq!(
        String::from_utf8_lossy(&rust.stdout),
        format!("CLAUDE_JSONL={}\n", newer.display())
    );
    assert!(!String::from_utf8_lossy(&rust.stdout).contains("OPENCODE_SESSION="));

    let missing = dir.join("missing");
    let missing_arg = missing.to_string_lossy().into_owned();
    let reference = Command::new("bash")
        .arg(ref_script("session-discover.sh"))
        .arg(&missing_arg)
        .env("HOME", &home)
        .env("ORIENT_OPENCODE_SCAN", "0")
        .env("JEEVES_ORIENT_OPENCODE_SCAN", "0")
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("sessions")
        .arg(&missing_arg)
        .env("HOME", &home)
        .env("ORIENT_OPENCODE_SCAN", "0")
        .env("JEEVES_ORIENT_OPENCODE_SCAN", "0")
        .output()
        .unwrap();
    assert_stdout_parity(&reference, &rust, "sessions no matching Claude file");
    assert_eq!(rust.status.code(), Some(0));
    assert!(rust.stdout.is_empty());

    let _ = std::fs::remove_dir_all(&dir);
}

#[cfg(unix)]
#[test]
fn sessions_preserves_a_symlink_argument_for_the_project_slug() {
    use std::os::unix::fs::symlink;

    let dir = scratch_dir("sessions-symlink");
    let home = dir.join("home");
    let real = dir.join("real-project");
    let link = dir.join("logical-project");
    std::fs::create_dir_all(&real).unwrap();
    symlink(&real, &link).unwrap();

    let slug: String = real
        .to_string_lossy()
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() {
                byte as char
            } else {
                '-'
            }
        })
        .collect();
    let claude_project = home.join(".claude/projects").join(slug);
    std::fs::create_dir_all(&claude_project).unwrap();
    let session = claude_project.join("real-target.jsonl");
    std::fs::write(&session, "real target\n").unwrap();

    let link_arg = link.to_string_lossy().into_owned();
    let reference = Command::new("bash")
        .arg(ref_script("session-discover.sh"))
        .arg(&link_arg)
        .env("HOME", &home)
        .env("ORIENT_OPENCODE_SCAN", "0")
        .env_remove("JEEVES_ORIENT_OPENCODE_SCAN")
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("sessions")
        .arg(&link_arg)
        .env("HOME", &home)
        .env("ORIENT_OPENCODE_SCAN", "0")
        .env("JEEVES_ORIENT_OPENCODE_SCAN", "0")
        .output()
        .unwrap();

    assert_stdout_parity(&reference, &rust, "sessions symlink argument");
    assert!(rust.stdout.is_empty());

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn checkin_lint_stdin_and_file_golden_match_reference() {
    let dir = scratch_dir("checkin-lint");
    let long_body = "x".repeat(119);
    let markdown = format!(
        "# status\n\nA non-bullet line, with commas, **bold**, and no linting.\n\n- clean bullet\n  * clean, with two commas, okay\n- {long_body}\n* one, two, three, four\n- **first** and **second**\n- {long_body}, one, two, three, **first** and **second**\nnot a bullet - still untouched\n"
    );

    let script = ref_script("lint-checkin.py");
    let script_arg = script.to_string_lossy().into_owned();
    let reference = run_with_stdin(Path::new("python3"), &[&script_arg], &markdown);
    let rust = run_with_stdin(
        Path::new(env!("CARGO_BIN_EXE_jeeves")),
        &["checkin-lint"],
        &markdown,
    );
    assert_stdout_parity(&reference, &rust, "checkin-lint stdin");
    assert_eq!(rust.status.code(), Some(1));
    assert!(!String::from_utf8_lossy(&rust.stdout).contains("non-bullet"));

    let clean = dir.join("clean.md");
    let clean_markdown = "A paragraph, with commas, is untouched.\n- clean\n* one, two\n";
    std::fs::write(&clean, clean_markdown).unwrap();
    let reference = Command::new("python3")
        .arg(script)
        .arg(&clean)
        .output()
        .unwrap();
    let rust = Command::new(env!("CARGO_BIN_EXE_jeeves"))
        .arg("checkin-lint")
        .arg(&clean)
        .output()
        .unwrap();
    assert_stdout_parity(&reference, &rust, "checkin-lint clean file");
    assert_eq!(rust.status.code(), Some(0));
    assert_eq!(rust.stdout, b"OK: all bullets pass.\n");

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn checkin_lint_three_problems_have_three_diagnostics_and_summary() {
    let long_body = "x".repeat(119);
    let markdown = format!("- {long_body}, one, two, three **first** **second**\nplain\n");
    let script = ref_script("lint-checkin.py");
    let script_arg = script.to_string_lossy().into_owned();
    let reference = run_with_stdin(Path::new("python3"), &[&script_arg], &markdown);
    let rust = run_with_stdin(
        Path::new(env!("CARGO_BIN_EXE_jeeves")),
        &["checkin-lint"],
        &markdown,
    );

    assert_stdout_parity(&reference, &rust, "checkin-lint three problems");
    assert_eq!(rust.status.code(), Some(1));
    let output = String::from_utf8_lossy(&rust.stdout);
    assert_eq!(output.lines().count(), 4);
    assert_eq!(output.lines().last(), Some("3 violation(s) found."));
}
