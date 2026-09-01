use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::Duration;

use serde::Serialize;

use crate::core::git;
use crate::worktrees::audit::{self, AuditOpts};

const DEFAULT_TIMEOUT_SECS: u64 = 15;

enum AuditMode {
    Repo(PathBuf),
    Sweep(PathBuf),
}

#[derive(Serialize)]
struct HookPayload<'a> {
    #[serde(rename = "hookSpecificOutput")]
    hook_specific_output: HookSpecificOutput<'a>,
}

#[derive(Serialize)]
struct HookSpecificOutput<'a> {
    #[serde(rename = "hookEventName")]
    hook_event_name: &'static str,
    #[serde(rename = "additionalContext")]
    additional_context: &'a str,
}

/// Run the SessionStart hook. Every path through this function is successful;
/// a hook failure must never prevent a session from starting.
pub fn run() {
    let mut payload = String::new();
    if std::io::stdin().read_to_string(&mut payload).is_err() {
        return;
    }

    let cwd = hook_cwd(&payload);
    let (mode, root, repo_mode) = match git::rev_parse(&cwd, "--show-toplevel") {
        Ok(root) if !root.is_empty() => (AuditMode::Repo(PathBuf::from(&root)), root, true),
        _ => {
            let root = cwd.to_string_lossy().into_owned();
            (AuditMode::Sweep(cwd), root, false)
        }
    };

    let timeout_secs = resolve_timeout_secs();
    let opts = audit::resolve_opts(true);
    let report = match audit_with_timeout(mode, opts, timeout_secs) {
        Ok(report) => report,
        Err(()) => {
            emit(&format!(
                "Worktree audit exceeded its {timeout_secs}s budget in {} and was skipped. Run the auditing-worktrees skill manually for the full report.",
                basename(Path::new(&root))
            ));
            return;
        }
    };

    let report = report.trim_end_matches('\n');
    if report.is_empty() {
        return;
    }

    let body = match mode_for_root(repo_mode, &root, report) {
        Some(body) => body,
        None => return,
    };
    emit(&body);
}

fn hook_cwd(payload: &str) -> PathBuf {
    let requested = serde_json::from_str::<serde_json::Value>(payload)
        .ok()
        .and_then(|value| {
            value
                .get("cwd")
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned)
        })
        .map(PathBuf::from);

    let process_cwd = std::env::current_dir().ok();
    match requested.filter(|path| path.is_dir()) {
        Some(path) if path.is_absolute() => path,
        Some(path) => process_cwd
            .as_ref()
            .map_or(path.clone(), |cwd| cwd.join(path)),
        None => process_cwd.unwrap_or_else(|| PathBuf::from(".")),
    }
}

fn resolve_timeout_secs() -> u64 {
    let raw = ["JEEVES_HOOK_TIMEOUT", "WORKTREE_AUDIT_HOOK_TIMEOUT"]
        .iter()
        .find_map(|name| match std::env::var(name) {
            Ok(value) if !value.is_empty() => Some(value),
            _ => None,
        });
    raw.and_then(|value| value.parse().ok())
        .unwrap_or(DEFAULT_TIMEOUT_SECS)
}

fn audit_with_timeout(mode: AuditMode, opts: AuditOpts, timeout_secs: u64) -> Result<String, ()> {
    if timeout_secs == 0 {
        return Err(());
    }

    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let report = match mode {
            AuditMode::Repo(root) => audit::audit_repo(&root, &opts),
            AuditMode::Sweep(root) => audit::audit_sweep(&[root], &opts),
        };
        let _ = sender.send(report);
    });

    match receiver.recv_timeout(Duration::from_secs(timeout_secs)) {
        Ok(report) => Ok(report),
        Err(RecvTimeoutError::Timeout) => Err(()),
        Err(RecvTimeoutError::Disconnected) => Ok(String::new()),
    }
}

fn mode_for_root(repo_mode: bool, root: &str, report: &str) -> Option<String> {
    if repo_mode {
        let body = format!(
            "Worktree drift in {} (from the auditing-worktrees SessionStart hook, report only):\n\n{report}\nNothing here has been changed. To act on it, use the auditing-worktrees skill.",
            basename(Path::new(root))
        );
        return Some(body);
    }

    let summary = audit::summary_collapse(report);
    if summary.is_empty() {
        return None;
    }
    Some(format!(
        "Worktree drift across {root} (from the auditing-worktrees SessionStart hook, report only):\n\n{}\nNothing here has been changed. Use the auditing-worktrees skill for detail or to act on it.",
        summary.trim_end_matches('\n')
    ))
}

fn basename(path: &Path) -> String {
    path.file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| path.to_string_lossy().into_owned())
}

fn emit(body: &str) {
    let payload = HookPayload {
        hook_specific_output: HookSpecificOutput {
            hook_event_name: "SessionStart",
            additional_context: body,
        },
    };
    if let Ok(json) = serde_json::to_string(&payload) {
        println!("{json}");
    }
}
