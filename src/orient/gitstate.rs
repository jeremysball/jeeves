//! Compact git state for one directory, matching ref/git-state.sh.

use std::path::{Path, PathBuf};
use std::process::{Command, Output};

/// Runs `git-state` with its reference-compatible positional arguments.
pub fn run(args: &[String]) -> u8 {
    let (dir, dir_display) = match args.first() {
        Some(value) if !value.is_empty() => (PathBuf::from(value), value.clone()),
        _ => {
            let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
            let display = cwd.to_string_lossy().into_owned();
            (cwd, display)
        }
    };

    // Unlike a failed git command, a failed `cd` is a user-visible refusal.
    if !dir.is_dir() {
        println!("error: cannot cd to {dir_display}");
        return 1;
    }

    let Some(root_output) = git(&dir, &["rev-parse", "--show-toplevel"]) else {
        println!("repo: none (not a git repository)");
        println!("dir: {dir_display}");
        return 0;
    };
    let root = command_substitution(&root_output.stdout);
    if root.is_empty() {
        println!("repo: none (not a git repository)");
        println!("dir: {dir_display}");
        return 0;
    }
    let root_path = PathBuf::from(&root);

    let branch = git_stdout(&root_path, &["branch", "--show-current"])
        .map(|s| command_substitution(s.as_bytes()))
        .unwrap_or_default();
    let branch = if branch.is_empty() {
        "(detached)".to_string()
    } else {
        branch
    };

    let tracking = match git_stdout(
        &root_path,
        &["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    )
    .map(|s| command_substitution(s.as_bytes()))
    {
        Some(upstream) if !upstream.is_empty() => {
            let counts = git_stdout(
                &root_path,
                &[
                    "rev-list",
                    "--left-right",
                    "--count",
                    &format!("HEAD...{upstream}"),
                ],
            )
            .map(|s| command_substitution(s.as_bytes()))
            .unwrap_or_else(|| "0 0".to_string());
            let mut fields = counts.split_whitespace();
            let ahead = fields.next().unwrap_or("0");
            let behind = fields.next().unwrap_or("0");
            format!("ahead {ahead} / behind {behind} (vs {upstream})")
        }
        _ => "no upstream".to_string(),
    };

    let last_iso = git_stdout(&root_path, &["log", "-1", "--format=%cI"])
        .map(|s| command_substitution(s.as_bytes()))
        .unwrap_or_else(|| "none".to_string());
    let last_rel = git_stdout(&root_path, &["log", "-1", "--format=%cr"])
        .map(|s| command_substitution(s.as_bytes()))
        .unwrap_or_else(|| "none".to_string());

    let mut output = String::new();
    output.push_str(&format!("repo: {root}\n"));
    output.push_str(&format!("branch: {branch}\n"));
    output.push_str(&format!("tracking: {tracking}\n"));
    output.push_str(&format!("last-commit-iso: {last_iso}\n"));
    output.push_str(&format!("last-commit-rel: {last_rel}\n"));
    output.push_str("recent-commits:\n");
    if let Some(commits) = git_stdout(&root_path, &["log", "-5", "--format=  %h %s (%cr)"]) {
        output.push_str(&commits);
    }

    let status =
        git_stdout(&root_path, &["-c", "color.ui=false", "status", "--short"]).unwrap_or_default();
    if !command_substitution(status.as_bytes()).is_empty() {
        output.push_str("dirty: yes\nstatus:\n");
        append_prefixed_lines(&mut output, &status, 40, false);
        output.push_str("diffstat:\n");
        let diffstat = git_stdout(&root_path, &["diff", "--stat"]).unwrap_or_default();
        append_prefixed_lines(&mut output, &diffstat, 20, true);
    } else {
        output.push_str("dirty: no\n");
    }

    output.push_str("worktrees:\n");
    if let Some(worktrees) = git_stdout(&root_path, &["worktree", "list"]) {
        append_prefixed_lines(&mut output, &worktrees, usize::MAX, false);
    }

    print!("{output}");
    0
}

fn git(repo: &Path, args: &[&str]) -> Option<Output> {
    Command::new("git")
        .current_dir(repo)
        .args(args)
        .output()
        .ok()
}

fn git_stdout(repo: &Path, args: &[&str]) -> Option<String> {
    let output = git(repo, args)?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Shell command substitution removes all trailing newline bytes, but no other
/// whitespace. The callers use the raw command output everywhere else.
fn command_substitution(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes)
        .trim_end_matches('\n')
        .to_string()
}

fn append_prefixed_lines(output: &mut String, text: &str, limit: usize, tail: bool) {
    let mut lines: Vec<&str> = text.split('\n').collect();
    if lines.last() == Some(&"") {
        lines.pop();
    }
    if tail && lines.len() > limit {
        lines = lines.split_off(lines.len() - limit);
    } else if !tail && lines.len() > limit {
        lines.truncate(limit);
    }
    for line in lines {
        output.push_str("  ");
        output.push_str(line);
        output.push('\n');
    }
}
