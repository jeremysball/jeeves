//! Idempotent crontab entry for jeeves' hourly collection run.
//!
//! Port of bin/install-cron.py. Deliberate deviation: the Python original
//! writes the crontab by default (`--install`); this port only *prints* the
//! block by default and writes only when `--write` is passed, so a stray
//! invocation can never rewrite the user's crontab. A second deviation: the
//! log path resolves through `state_dir_path` (read-only) instead of
//! `state_dir` (which creates the directory), so `--print` never touches the
//! filesystem.

use std::path::{Path, PathBuf};

use crate::core::paths::state_dir_path;

pub const BEGIN: &str = "# BEGIN jeeves";
pub const END: &str = "# END jeeves";

/// Where mise keeps its shim farm. Honors MISE_DATA_DIR and XDG_DATA_HOME,
/// plus a JEEVES_MISE_SHIMS override for tests/odd layouts.
pub fn shims_dir() -> PathBuf {
    if let Some(ovr) = std::env::var_os("JEEVES_MISE_SHIMS") {
        if !ovr.is_empty() {
            return PathBuf::from(ovr);
        }
    }
    let data = match std::env::var_os("MISE_DATA_DIR") {
        Some(d) if !d.is_empty() => PathBuf::from(d),
        _ => {
            let xdg = std::env::var_os("XDG_DATA_HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| home().join(".local/share"));
            xdg.join("mise")
        }
    };
    data.join("shims")
}

/// PATH for the cron environment.
///
/// Every entry here must be a *stable directory*: one that survives tool
/// upgrades untouched. Resolving each tool through `shutil.which` at
/// install time instead bakes the mise shim's resolved *versioned* install
/// dir (e.g. `.../installs/fd/latest/fd-v10.4.2-.../`) into the crontab,
/// and the next `mise upgrade` deletes that directory out from under cron.
/// That is how the git-state scan silently reported `fd not found on PATH`
/// for days after fd moved 10.4.2 → 10.5.0 (and gh's versioned dir sat
/// stale in the same entry), unnoticed until a manual read of the
/// snapshot's error line.
///
/// The mise-native answer for a fixed `PATH` is the shims directory:
/// `fd`, `gh`, and `python3` resolve through it to whatever version is
/// current, forever, with no reinstall. ~/.local/bin carries the
/// non-mise user binaries taskferry and gh-axi (and mise itself, the
/// shims' symlink target); /usr/{local/,}bin and /bin are the fallback
/// floor for git, bash, and the system python3. `fd` belongs on PATH as
/// much as gh: scan-active.sh does all repo discovery through it, so a
/// PATH without it reports every workspace as empty.
pub fn cron_path() -> String {
    let mut dirs: Vec<PathBuf> = Vec::new();
    for d in [
        home().join(".local/bin"),
        shims_dir(),
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/usr/bin"),
        PathBuf::from("/bin"),
    ] {
        if !dirs.contains(&d) {
            dirs.push(d);
        }
    }
    dirs.iter()
        .map(|d| d.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(":")
}

/// The cron entry line: run `collect` at minute 13 of every hour with the
/// stable-dirs PATH, appending to the state dir's collect.log.
pub fn entry_line(binary: &Path) -> String {
    let log = state_dir_path().join("collect.log");
    format!(
        "13 * * * * PATH={} {} collect >> {} 2>&1",
        cron_path(),
        binary.display(),
        log.display()
    )
}

/// The full marker block: BEGIN, entry, END.
pub fn block(binary: &Path) -> String {
    format!("{BEGIN}\n{}\n{END}\n", entry_line(binary))
}

/// Strips an existing jeeves block (BEGIN..END inclusive) from `text`,
/// preserving every other line. Pure string-level merge helper.
pub fn strip_block(text: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    let mut dropping = false;
    for line in text.lines() {
        if line.trim() == BEGIN {
            dropping = true;
            continue;
        }
        if line.trim() == END {
            dropping = false;
            continue;
        }
        if !dropping {
            out.push(line);
        }
    }
    out.join("\n") + if out.is_empty() { "" } else { "\n" }
}

/// Idempotent merge: strip any existing jeeves block, then append the new
/// block. A second install of the same block is a no-op (block replaced,
/// never duplicated).
pub fn merge(text: &str, binary: &Path) -> String {
    let body = block(binary);
    let stripped = strip_block(text);
    if stripped.trim().is_empty() {
        body
    } else {
        format!("{}\n{}", stripped.trim_end_matches('\n'), body)
    }
}

pub fn has_jeeves(text: &str) -> bool {
    text.contains(BEGIN)
}

/// Reads the live crontab via `crontab -l`. Err carries the failure reason.
pub fn read_crontab() -> Result<String, String> {
    match std::process::Command::new("crontab").arg("-l").output() {
        Ok(out) if out.status.success() => Ok(String::from_utf8_lossy(&out.stdout).into_owned()),
        Ok(out) => Err(format!(
            "crontab -l failed (exit {}): {}",
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr).trim()
        )),
        Err(e) => Err(format!("cannot run crontab -l: {e}")),
    }
}

/// Pipes `text` back through `crontab -`.
pub fn write_crontab(text: &str) -> Result<(), String> {
    use std::io::Write;
    let mut child = std::process::Command::new("crontab")
        .arg("-")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot run crontab -: {e}"))?;
    child
        .stdin
        .take()
        .unwrap()
        .write_all(text.as_bytes())
        .map_err(|e| format!("cannot pipe crontab: {e}"))?;
    let out = child
        .wait_with_output()
        .map_err(|e| format!("crontab - failed: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!(
            "crontab - failed (exit {}): {}",
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stderr).trim()
        ))
    }
}

/// Runs `install-cron`. `--print` (the default) prints the block; `--write`
/// reads the live crontab, merges the block in, and pipes the result back
/// through `crontab -`. Returns the process exit code.
pub fn run(args: &[String]) -> u8 {
    let mut write = false;
    for arg in args {
        match arg.as_str() {
            "--print" => {}
            "--write" => write = true,
            "--help" => {
                print_usage();
                return 0;
            }
            value if value.starts_with("--") => {
                println!("error: unknown flag {value}");
                return 2;
            }
            value => {
                println!("error: unexpected argument {value}");
                return 2;
            }
        }
    }
    let binary = match std::env::current_exe() {
        Ok(p) => p,
        Err(e) => {
            eprintln!("cannot resolve the current executable: {e}");
            return 1;
        }
    };
    if !write {
        print!("{}", block(&binary));
        return 0;
    }
    let current = match read_crontab() {
        Ok(text) => text,
        Err(reason) => {
            eprintln!("refusing to write: {reason}");
            return 1;
        }
    };
    let merged = merge(&current, &binary);
    if let Err(e) = write_crontab(&merged) {
        eprintln!("{e}");
        return 1;
    }
    println!("jeeves cron installed (13 * * * *)");
    0
}

fn print_usage() {
    println!(
        "usage: jeeves install-cron [--print | --write]

Prints the jeeves cron block (default), or installs it into the live
crontab when --write is passed. The block runs `collect` at minute 13
of every hour with a stable-dirs PATH.

examples:
  jeeves install-cron            # print the block
  jeeves install-cron --write    # merge into the live crontab"
    );
}

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default()
}
