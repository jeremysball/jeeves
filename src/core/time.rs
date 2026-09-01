//! Age formatting and activity measurement, mirroring ref/lib.sh:105-141.

use std::path::Path;
use std::process::Command;

/// Formats a duration in seconds as `Nm`, `Nh`, or `Nd`, mirroring
/// ref/lib.sh:135-141 exactly: <3600 -> minutes, <86400 -> hours, else days;
/// integer division with no remainder component.
pub fn human_age(secs: u64) -> String {
    if secs < 3600 {
        format!("{}m", secs / 60)
    } else if secs < 86400 {
        format!("{}h", secs / 3600)
    } else {
        format!("{}d", secs / 86400)
    }
}

/// Seconds since ANY activity on this repo, committed or not:
/// max(last commit time, newest touched-file mtime) subtracted from now.
///
/// Mirrors ref/lib.sh:105-133: the newest mtime among files listed by
/// `git status --porcelain` (which includes untracked), via
/// `ls-files -z --modified --others --exclude-standard` plus
/// `diff --cached --name-only -z`. Returns `None` when the worktree is clean
/// (no mtimes at all).  activity then comes from the last commit alone, and
/// the caller decides what to do with a None.
pub fn activity_age_secs(repo: &Path) -> Option<u64> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs();

    let commit_ts = git(repo, &["log", "-1", "--format=%ct"]).ok()?;
    let commit_ts: u64 = commit_ts.trim().parse().unwrap_or(0);

    let newest = newest_change_mtime(repo)?;
    Some(now.saturating_sub(commit_ts.max(newest)))
}

/// Newest mtime (unix seconds) among files the user has actually touched,
/// per ref/lib.sh:105-113. `None` when the repo is clean or unreadable.
fn newest_change_mtime(repo: &Path) -> Option<u64> {
    let mut paths: Vec<String> = Vec::new();
    for args in [
        vec![
            "ls-files",
            "-z",
            "--modified",
            "--others",
            "--exclude-standard",
        ],
        vec!["diff", "--cached", "--name-only", "-z"],
    ] {
        let out = git(repo, &args).ok()?;
        paths.extend(
            out.split('\0')
                .filter(|p| !p.is_empty())
                .map(str::to_string),
        );
    }
    let mut newest: Option<u64> = None;
    for p in &paths {
        if let Ok(md) = std::fs::metadata(repo.join(p)) {
            if let Ok(m) = md.modified() {
                if let Ok(secs) = m.duration_since(std::time::UNIX_EPOCH) {
                    let secs = secs.as_secs();
                    newest = Some(newest.map_or(secs, |n| n.max(secs)));
                }
            }
        }
    }
    newest
}

fn git(repo: &Path, args: &[&str]) -> Result<String, std::io::Error> {
    Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
}

#[cfg(test)]
mod tests {
    use super::{activity_age_secs, human_age};

    #[test]
    fn minutes_below_one_hour() {
        assert_eq!(human_age(0), "0m");
        assert_eq!(human_age(30), "0m");
        assert_eq!(human_age(59), "0m");
        assert_eq!(human_age(60), "1m");
        assert_eq!(human_age(3599), "59m");
    }

    #[test]
    fn hours_below_one_day() {
        assert_eq!(human_age(3600), "1h");
        assert_eq!(human_age(7199), "1h");
        assert_eq!(human_age(7200), "2h");
        assert_eq!(human_age(86399), "23h");
    }

    #[test]
    fn days_at_or_above_one_day() {
        assert_eq!(human_age(86400), "1d");
        assert_eq!(human_age(172800), "2d");
        assert_eq!(human_age(86400 * 28), "28d");
        assert_eq!(human_age(86400 * 365 + 1), "365d");
    }

    #[test]
    fn boundary_values() {
        assert_eq!(human_age(3599), "59m");
        assert_eq!(human_age(3600), "1h");
        assert_eq!(human_age(86399), "23h");
        assert_eq!(human_age(86400), "1d");
    }

    #[test]
    fn activity_age_is_none_when_clean() {
        // A path that is not a git repo cannot produce any mtimes.
        let tmp = std::env::temp_dir().join("not-a-repo-jeeves");
        let _ = std::fs::create_dir_all(&tmp);
        assert!(activity_age_secs(&tmp).is_none());
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
