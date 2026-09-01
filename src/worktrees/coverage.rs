//! Content-coverage scorer: how much of a branch's net text lines are already
//! in base, mirroring ref/lib.sh's `coverage_score` (lib.sh:180-248) so the
//! verdict strings are byte-for-byte identical to the reference CLI.
//!
//! Verdict contract (single line on stdout, exit 0):
//!   SCORED <0-100>   - percent of the branch's net text lines in base.
//!   UNSCORED <why>   - binary/mode-only row, O==0, or empty patch.
//!   UNKNOWN <why>    - criss-cross history, merge conflict, merge-tree error.

use std::path::Path;
use std::process::Command;

use crate::core::error::CliError;
use crate::core::git;

/// Sums net text lines from numstat input (added-minus-deleted), mirroring
/// ref/lib.sh:164-174. Returns `Ok(net)` for a normal text diff, or the
/// documented `UNSCORED <why>` verdict when a binary (`- -`) or mode-only
/// (`0 0`) row appears. Rows are read TAB-aware: numstat emits
/// `add<TAB>del<TAB>path`, so the pathname must never be parsed as a count.
fn numstat_net(input: &str) -> Result<i64, String> {
    let mut total: i64 = 0;
    for row in input.lines() {
        if row.is_empty() {
            continue;
        }
        let mut fields = row.split('\t');
        let a = fields.next().unwrap_or("");
        let d = fields.next().unwrap_or("");
        if a == "-" && d == "-" {
            return Err("UNSCORED binary".to_string());
        }
        if a == "0" && d == "0" {
            return Err("UNSCORED mode-only".to_string());
        }
        let a: i64 = a.parse().unwrap_or(0);
        let d: i64 = d.parse().unwrap_or(0);
        total += a - d;
    }
    Ok(total)
}

/// Runs `git diff --numstat` between two refs, mirroring the reference's
/// `git -C repo diff --no-ext-diff --no-textconv --numstat --no-renames`
/// (lib.sh:186). Returns `Ok(None)` when git exits nonzero (bad ref, ...),
/// which the reference maps to `UNKNOWN branch-diff-failed`.
fn numstat_diff(repo: &Path, a: &str, b: &str) -> Result<Option<String>, CliError> {
    let out = Command::new("git")
        .current_dir(repo)
        .args([
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--numstat",
            "--no-renames",
            a,
            b,
        ])
        .output()
        .map_err(|e| CliError::refusal(format!("git diff failed: {e}")))?;
    if !out.status.success() {
        return Ok(None);
    }
    Ok(Some(String::from_utf8_lossy(&out.stdout).into_owned()))
}

/// Scores how much of `<branch>`'s work is already in `<base>` and returns
/// the verdict line, mirroring ref/lib.sh:180-248.
pub fn coverage_score(repo: &Path, base: &str, branch: &str) -> Result<String, CliError> {
    // Singular merge base; >1 base (criss-cross history) is an error, and no
    // base at all is UNKNOWN no-merge-base (lib.sh:182-183). The reference
    // maps every merge-base failure to the same verdict, so any Err here
    // (criss-cross, unreadable repo, ...) is UNKNOWN no-merge-base too.
    let mb = match git::merge_base(repo, base, branch) {
        Ok(Some(mb)) => mb,
        Ok(None) | Err(_) => return Ok("UNKNOWN no-merge-base".to_string()),
    };

    // O: net text lines on the branch from its merge base (lib.sh:186-195).
    let out = match numstat_diff(repo, &mb, branch)? {
        Some(out) => out,
        None => return Ok("UNKNOWN branch-diff-failed".to_string()),
    };
    let o = match numstat_net(&out) {
        Ok(o) => o,
        Err(verdict) => return Ok(verdict),
    };
    if o <= 0 {
        return Ok("UNSCORED no-text-rows".to_string());
    }

    // Simulated merge + residual diff in ONE subprocess so the synthesized
    // tree stays reachable (lib.sh:201-231). merge-tree rc 1 = conflict,
    // >1 = error -> UNKNOWN. An empty tree with rc 0 must NOT fail open to
    // SCORED 100: the reference exits 2 from the subshell in that case,
    // which surfaces as rc 2 -> UNKNOWN merge-tree-error (lib.sh:216-221).
    let obj = tempfile_dir()?;
    let alt = git::rev_parse_abs_git_dir(repo)?;
    let alt = format!("{alt}/objects");
    let base_oid = git::rev_parse(repo, base)?;
    let residual = merge_tree_residual(repo, &obj, &alt, base, branch, &base_oid)?;
    let _ = std::fs::remove_dir_all(&obj);

    let (mtrc, rc, residual) = residual;
    if mtrc == 1 {
        return Ok("UNKNOWN merge-conflict".to_string());
    }
    if mtrc > 1 || rc > 1 {
        return Ok("UNKNOWN merge-tree-error".to_string());
    }

    let r = match numstat_net(&residual) {
        Ok(r) => r,
        Err(verdict) => return Ok(verdict),
    };

    // R is a NET line count, so it goes negative when the branch's own
    // deletions are missing from base. Subtracting a negative R would inflate
    // the score past O - a branch whose deletion never shipped must not score
    // >100. Score against its magnitude and clamp to 0-100 (lib.sh:238-247).
    let abs_r = r.abs();
    let num = o - abs_r;
    let num = num.max(0);
    let pct = num * 100 / o;
    let pct = pct.min(100);
    Ok(format!("SCORED {pct}"))
}

/// `mktemp -d` equivalent (lib.sh:202). Failure -> `UNKNOWN no-temp-dir`.
fn tempfile_dir() -> Result<std::path::PathBuf, CliError> {
    let dir = std::env::temp_dir().join(format!(
        "jeeves-coverage-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    std::fs::create_dir_all(&dir).map_err(|e| CliError::refusal(format!("mktemp failed: {e}")))?;
    Ok(dir)
}

/// Runs merge-tree with the object dir wired to a temp dir backed by the
/// repo's object store, then diffs the synthesized tree against base
/// (lib.sh:211-224). Returns `(merge_tree_rc, diff_rc, residual_output)`.
fn merge_tree_residual(
    repo: &Path,
    obj: &Path,
    alt: &str,
    base: &str,
    branch: &str,
    base_oid: &str,
) -> Result<(i32, i32, String), CliError> {
    let obj = obj.to_string_lossy().into_owned();
    let tree = Command::new("git")
        .current_dir(repo)
        .env("GIT_OBJECT_DIRECTORY", &obj)
        .env("GIT_ALTERNATE_OBJECT_DIRECTORIES", alt)
        .args(["merge-tree", "--write-tree", base, branch])
        .output()
        .map_err(|e| CliError::refusal(format!("git merge-tree failed: {e}")))?;
    let mtrc = tree.status.code().unwrap_or(-1);
    let tree_out = String::from_utf8_lossy(&tree.stdout).into_owned();
    let tree_oid = tree_out.lines().next().unwrap_or("").trim().to_string();

    // Empty tree with rc 0 must NOT fall through: the reference exits 2 from
    // the subshell, which surfaces as rc 2 -> UNKNOWN merge-tree-error
    // (lib.sh:216-221).
    if mtrc != 0 || tree_oid.is_empty() {
        let rc = if mtrc != 0 { mtrc } else { 2 };
        return Ok((rc, 0, String::new()));
    }

    let diff = Command::new("git")
        .current_dir(repo)
        .env("GIT_OBJECT_DIRECTORY", &obj)
        .env("GIT_ALTERNATE_OBJECT_DIRECTORIES", alt)
        .args([
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--numstat",
            "--no-renames",
            base_oid,
            &tree_oid,
        ])
        .output()
        .map_err(|e| CliError::refusal(format!("git diff failed: {e}")))?;
    let rc = diff.status.code().unwrap_or(-1);
    Ok((mtrc, rc, String::from_utf8_lossy(&diff.stdout).into_owned()))
}
