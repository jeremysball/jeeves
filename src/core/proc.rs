//! Worktree lock liveness, mirroring ref/lib.sh:32-77.
//!
//! A lock file written by jeeves contains `pid <n>` and `start <n>` (the
//! process starttime in clock ticks, field 22 of /proc/<pid>/stat as read by
//! the shell's `read` after stripping the comm field). A lock is Live when
//! the pid is alive AND its recorded start matches the current starttime.
//! Anything we cannot positively prove stale.  including a lock whose reason
//! is a plain human string.  is Unknown (treated as live by callers).

use std::path::Path;

/// Result of inspecting a git worktree lock file.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockStatus {
    /// pid alive and recorded start time matches /proc.
    Live,
    /// pid dead, or alive with a different start time.
    Stale,
    /// no lock file, unparseable reason, or /proc unusable.
    Unknown,
}

impl std::fmt::Display for LockStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Lowercase, matching the reference's `lock_status` echoes
        // (ref/lib.sh:64-77) which audit-worktrees.sh prints verbatim.
        let s = match self {
            LockStatus::Live => "live",
            LockStatus::Stale => "stale",
            LockStatus::Unknown => "unknown",
        };
        write!(f, "{s}")
    }
}

/// Reads `/proc/<pid>/stat` and extracts the starttime (field 22; the shell
/// original reads field 20 after stripping `(comm)` via `${stat##*) }`).
/// Returns `None` when procfs is unusable or the stat is malformed.
fn proc_start_ticks(pid: u64) -> Option<u64> {
    // Probe procfs itself first: in a container/chroot without /proc every
    // pid looks absent, which would turn every live lock into stale.  i.e.
    // into a deletion. Unknown, not missing.
    if !std::path::Path::new("/proc/self").is_dir() {
        return None;
    }
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    // comm can itself contain spaces and `)`; find the LAST `) `.
    let (_, rest) = stat.rsplit_once(") ")?;
    let mut fields = rest.split_whitespace();
    // After `) `, field 3 is the first one; field 22 is the 20th after it.
    fields.nth(19)?.parse::<u64>().ok()
}

/// Mirrors ref/lib.sh:64-77 `lock_status`.
pub fn lock_status(gitdir_lock_path: &Path) -> LockStatus {
    if !gitdir_lock_path.is_file() {
        return LockStatus::Unknown;
    }
    let reason = match std::fs::read_to_string(gitdir_lock_path) {
        Ok(r) => r,
        Err(_) => return LockStatus::Unknown,
    };
    let pid = capture_u64(&reason, "pid ");
    let start = capture_u64(&reason, "start ");
    let (Some(pid), Some(start)) = (pid, start) else {
        return LockStatus::Unknown;
    };
    // A missing /proc/<pid> only proves the process is gone when procfs
    // itself is mounted (lib.sh:34-38). Probe procfs first, then the pid's
    // own directory: missing => provably gone => stale; only a read or parse
    // failure is unknown.
    if !Path::new("/proc/self").is_dir() {
        return LockStatus::Unknown;
    }
    if !Path::new(&format!("/proc/{pid}")).is_dir() {
        return LockStatus::Stale;
    }
    match proc_start_ticks(pid) {
        Some(live) if live == start => LockStatus::Live,
        Some(_) => LockStatus::Stale,
        None => LockStatus::Unknown,
    }
}

/// `pid <n>` / `start <n>` values as recorded by jeeves locks.
fn capture_u64(text: &str, needle: &str) -> Option<u64> {
    text.find(needle).and_then(|i| {
        let rest = &text[i + needle.len()..];
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        if digits.is_empty() {
            None
        } else {
            digits.parse().ok()
        }
    })
}

#[cfg(test)]
mod tests {
    use super::{lock_status, LockStatus};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn scratch() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let d = std::env::temp_dir().join(format!("jeeves-lock-{}-{n}", std::process::id()));
        let _ = std::fs::create_dir_all(&d);
        d
    }

    fn write_lock(dir: &Path, body: &str) -> PathBuf {
        let p = dir.join("locked");
        std::fs::write(&p, body).unwrap();
        p
    }

    #[test]
    fn own_pid_is_live() {
        let dir = scratch();
        let my_pid = std::process::id();
        let start =
            super::proc_start_ticks(my_pid as u64).expect("procfs must be readable in test env");
        let lock = write_lock(&dir, &format!("pid {my_pid}\nstart {start}\n"));
        assert_eq!(lock_status(&lock), LockStatus::Live);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn dead_pid_is_stale() {
        let dir = scratch();
        let lock = write_lock(&dir, "pid 9999999\nstart 1\n");
        assert_eq!(lock_status(&lock), LockStatus::Stale);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn garbage_file_is_unknown() {
        let dir = scratch();
        let lock = write_lock(&dir, "this is not a pid record\n");
        assert_eq!(lock_status(&lock), LockStatus::Unknown);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_file_is_unknown() {
        let dir = scratch();
        assert_eq!(lock_status(&dir.join("nope")), LockStatus::Unknown);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn partial_record_is_unknown() {
        let dir = scratch();
        let lock = write_lock(&dir, "pid 1234\n");
        assert_eq!(lock_status(&lock), LockStatus::Unknown);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn start_mismatch_is_stale() {
        let dir = scratch();
        let my_pid = std::process::id();
        let lock = write_lock(&dir, &format!("pid {my_pid}\nstart 1\n"));
        assert_eq!(lock_status(&lock), LockStatus::Stale);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn human_reason_is_unknown() {
        let dir = scratch();
        let lock = write_lock(&dir, "for my own reasons\n");
        assert_eq!(lock_status(&lock), LockStatus::Unknown);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
