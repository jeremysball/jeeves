//! Thin wrappers over `git`, always run with `.current_dir(repo)` so results
//! never depend on the caller's cwd (mirrors ref/lib.sh's `git -C` policy).
//!
//! All errors are returned as `CliError::refusal` at this layer, keeping
//! `anyhow`-style context out of the shared core.

use std::path::Path;
use std::process::{Command, Output};

use crate::core::error::CliError;

fn run(repo: &Path, args: &[&str]) -> Result<Output, CliError> {
    let out = Command::new("git")
        .current_dir(repo)
        .args(args)
        .output()
        .map_err(|e| CliError::refusal(format!("git {:?} failed: {e}", args.first())))?;
    Ok(out)
}

fn stdout(repo: &Path, args: &[&str]) -> Result<String, CliError> {
    let out = run(repo, args)?;
    if !out.status.success() {
        return Err(CliError::refusal(format!(
            "git {} failed with {}: {}",
            args.first().unwrap_or(&""),
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        )));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// `git log` rows, one Vec per commit record. Records are separated by the
/// `\x1f` unit separator in the caller-supplied format; the caller picks the
/// fields (e.g. `%h%x1f%s%x1f%cr` as in scan-active.sh:163-164). Empty output
/// or a failed log (repo without commits, unreadable repo, ...) yields an
/// empty Vec, mirroring the script's `git ... 2>/dev/null; [ -z "$subjects" ] && continue`.
///
/// A record is a line of `\x1f`-separated fields. The raw stdout is split on
/// `\n` first (dropping only the trailing empty record produced by the final
/// newline), then each record is split on `\x1f` keeping ALL fields, so an
/// interior empty field (e.g. an empty subject in `%h%x1f%s%x1f%cr`) is
/// preserved and records stay aligned.
pub fn log_units(repo: &Path, args: &[&str]) -> Vec<Vec<String>> {
    let Ok(out) = stdout(repo, args) else {
        return Vec::new();
    };
    if out.is_empty() {
        return Vec::new();
    }
    let mut lines: Vec<&str> = out.split('\n').collect();
    if lines.last() == Some(&"") {
        lines.pop();
    }
    lines
        .iter()
        .map(|line| line.split('\x1f').map(|f| f.to_string()).collect())
        .collect()
}

/// `git rev-parse --absolute-git-dir` (ref/lib.sh:54-56). Errors -> refusal.
pub fn rev_parse_abs_git_dir(repo: &Path) -> Result<String, CliError> {
    Ok(stdout(repo, &["rev-parse", "--absolute-git-dir"])?
        .trim()
        .to_string())
}

/// Singular merge-base of two refs, mirroring ref/lib.sh:149-156: counts
/// `--all` first and treats >1 base (criss-cross history) as an error.
/// `Ok(None)` = no merge base (rc 1 from git); `Err` = anything else.
pub fn merge_base(repo: &Path, a: &str, b: &str) -> Result<Option<String>, CliError> {
    let all = run(repo, &["merge-base", "--all", a, b])?;
    if !all.status.success() {
        return if all.status.code() == Some(1) {
            Ok(None)
        } else {
            Err(CliError::refusal(format!(
                "git merge-base --all {a} {b} failed with {}: {}",
                all.status,
                String::from_utf8_lossy(&all.stderr).trim()
            )))
        };
    }
    let count = all.stdout.iter().filter(|&&b| b == b'\n').count();
    if count != 1 {
        return Err(CliError::refusal(format!(
            "criss-cross history: {count} merge bases between {a} and {b}"
        )));
    }
    let base = stdout(repo, &["merge-base", a, b])?;
    Ok(Some(base.trim().to_string()))
}

/// `git rev-parse --path-format=absolute --git-path objects`
/// (ref/lib.sh:203). `Ok(None)` when git fails or prints nothing, mirroring
/// the reference's `2>/dev/null` read + empty check -> `UNKNOWN no-object-dir`
/// (ref/lib.sh:204). Unlike `--absolute-git-dir`, this resolves the actual
/// object store even from a linked worktree (whose per-worktree gitdir has no
/// `objects/` directory).
pub fn objects_dir(repo: &Path) -> Result<Option<String>, CliError> {
    let out = run(
        repo,
        &[
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "objects",
        ],
    )?;
    if !out.status.success() {
        return Ok(None);
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() {
        return Ok(None);
    }
    Ok(Some(s))
}

/// `git rev-parse <ref>` (ref/lib.sh:205). Errors -> refusal.
pub fn rev_parse(repo: &Path, r: &str) -> Result<String, CliError> {
    Ok(stdout(repo, &["rev-parse", r])?.trim().to_string())
}

/// `git worktree list --porcelain` raw output (ref/audit-worktrees.sh:189-194
/// needs the raw text to spot `prunable <reason>` lines). Errors -> refusal.
pub fn porcelain_worktree_list(repo: &Path) -> Result<String, CliError> {
    stdout(repo, &["worktree", "list", "--porcelain"])
}

/// `(path, sha, branch)` for every worktree, parsed line-by-line from
/// `git worktree list --porcelain` so paths containing spaces survive intact
/// (mirrors ref/lib.sh:81-93). The branch is the full ref name (e.g.
/// `refs/heads/main`) or empty for a detached HEAD. Records are terminated by
/// blank lines; every non-blank line (including a `branch` line) belongs to
/// the most recent `worktree` line, so a path that merely contains the word
/// "worktree" cannot corrupt the parse.
pub fn worktree_list(repo: &Path) -> Result<Vec<(String, String, String)>, CliError> {
    let out = stdout(repo, &["worktree", "list", "--porcelain"])?;
    let mut result = Vec::new();
    let mut cur_path: Option<String> = None;
    let mut cur_branch = String::new();
    for line in out.lines() {
        if let Some(rest) = line.strip_prefix("worktree ") {
            if let Some(path) = cur_path.take() {
                result.push((path, String::new(), std::mem::take(&mut cur_branch)));
            }
            cur_path = Some(rest.to_string());
        } else if let Some(rest) = line.strip_prefix("branch ") {
            cur_branch = rest.to_string();
        } else if let Some(rest) = line.strip_prefix("HEAD ") {
            let _ = rest;
        } else if line.is_empty() {
            if let Some(path) = cur_path.take() {
                result.push((path, String::new(), std::mem::take(&mut cur_branch)));
            }
        }
    }
    if let Some(path) = cur_path.take() {
        result.push((path, String::new(), std::mem::take(&mut cur_branch)));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::{log_units, merge_base, rev_parse_abs_git_dir, worktree_list};
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn scratch_repo() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let d = std::env::temp_dir().join(format!("jeeves-git-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn git(repo: &PathBuf, args: &[&str]) {
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
    }

    fn init_repo() -> PathBuf {
        let d = scratch_repo();
        git(&d, &["init", "-q", "-b", "main"]);
        git(&d, &["config", "user.email", "t@t"]);
        git(&d, &["config", "user.name", "t"]);
        std::fs::write(d.join("f.txt"), "one\n").unwrap();
        git(&d, &["add", "."]);
        git(&d, &["commit", "-q", "-m", "first commit"]);
        d
    }

    #[test]
    fn log_units_parses_separators() {
        let d = init_repo();
        let rows = log_units(&d, &["log", "--format=%h%x1f%s%x1f%cr"]);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].len(), 3);
        assert!(!rows[0][0].is_empty());
        assert_eq!(rows[0][1], "first commit");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn log_units_keeps_interior_empty_fields() {
        let d = init_repo();
        git(
            &d,
            &[
                "commit",
                "-q",
                "--allow-empty",
                "--allow-empty-message",
                "-m",
                "",
            ],
        );
        let rows = log_units(&d, &["log", "--format=%h%x1f%s%x1f%cr"]);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].len(), 3);
        assert_eq!(rows[0][1], "");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn log_units_empty_for_no_commits() {
        let d = scratch_repo();
        git(&d, &["init", "-q", "-b", "main"]);
        let rows = log_units(&d, &["log", "--format=%h%x1f%s"]);
        assert!(rows.is_empty());
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn rev_parse_abs_git_dir_is_absolute() {
        let d = init_repo();
        let g = rev_parse_abs_git_dir(&d).unwrap();
        assert!(g.starts_with('/'), "got {g}");
        assert!(g.ends_with(".git"));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn merge_base_singular() {
        let d = init_repo();
        git(&d, &["checkout", "-q", "-b", "feature"]);
        std::fs::write(d.join("f.txt"), "two\n").unwrap();
        git(&d, &["add", "."]);
        git(&d, &["commit", "-q", "-m", "second"]);
        let mb = merge_base(&d, "main", "feature").unwrap();
        assert!(mb.is_some());
        assert_eq!(mb.unwrap().len(), 40);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn merge_base_none_when_unrelated() {
        let d = scratch_repo();
        git(&d, &["init", "-q", "-b", "main"]);
        git(&d, &["config", "user.email", "t@t"]);
        git(&d, &["config", "user.name", "t"]);
        std::fs::write(d.join("a.txt"), "a\n").unwrap();
        git(&d, &["add", "."]);
        git(&d, &["commit", "-q", "-m", "a"]);
        git(&d, &["checkout", "-q", "--orphan", "other"]);
        std::fs::write(d.join("b.txt"), "b\n").unwrap();
        git(&d, &["add", "."]);
        git(&d, &["commit", "-q", "-m", "b"]);
        assert!(merge_base(&d, "main", "other").unwrap().is_none());
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn worktree_list_parses_spaces_in_paths() {
        let d = init_repo();
        let wt = d.join("with space");
        git(
            &d,
            &["worktree", "add", "-q", "-b", "wt1", &wt.to_string_lossy()],
        );
        let list = worktree_list(&d).unwrap();
        assert_eq!(list.len(), 2);
        assert!(list
            .iter()
            .any(|(p, _, b)| p == &wt.to_string_lossy().to_string() && b == "refs/heads/wt1"));
        git(
            &d,
            &["worktree", "remove", "--force", &wt.to_string_lossy()],
        );
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn worktree_list_reports_detached_head() {
        let d = init_repo();
        git(&d, &["checkout", "-q", "--detach"]);
        let list = worktree_list(&d).unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].2, "");
        let _ = std::fs::remove_dir_all(&d);
    }
}
