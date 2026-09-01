//! `jeeves migrate` — prints a two-section diff-style plan for repointing
//! this machine from the Python tree to the Rust binary.
//!
//! Reads the LIVE machine paths (no hardcoding: HOME/XDG). Never mutates
//! anything; exits 0 always, even when a source is unreadable — it says so
//! honestly instead of guessing.

use std::path::{Path, PathBuf};

use crate::digest::cron::{self, BEGIN, END};

/// Settings file: `$JEEVES_SETTINGS_FILE` override (tests), else
/// `$HOME/.claude/settings.json`.
pub fn settings_file() -> PathBuf {
    match std::env::var_os("JEEVES_SETTINGS_FILE") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => home().join(".claude/settings.json"),
    }
}

/// Section (a): the current crontab's jeeves block vs the new entry line.
/// `crontab` is Err when the live crontab could not be read.
pub fn cron_plan(crontab: Result<&str, &str>, exe: &Path) -> String {
    let new_line = cron::entry_line(exe);
    let mut out = String::new();
    out.push_str("(a) crontab — hourly collect entry\n");
    match crontab {
        Err(reason) => {
            out.push_str(&format!("  unreadable: {reason}\n"));
            out.push_str(
                "  the current entry cannot be diffed; run `crontab -l` manually and\n  compare against the new entry below.\n",
            );
        }
        Ok(text) if !cron::has_jeeves(text) => {
            out.push_str("  no jeeves block in the current crontab; nothing to replace.\n");
        }
        Ok(text) => match block_entry_line(text) {
            Some(old) => {
                out.push_str("  current entry:\n");
                out.push_str(&format!("    {old}\n"));
            }
            None => {
                out.push_str("  jeeves block present but its entry line could not be parsed.\n")
            }
        },
    }
    out.push_str("  new entry:\n");
    out.push_str(&format!("    {new_line}\n"));
    out
}

/// Section (b): the settings.json SessionStart hook entry pointing at
/// auditing-worktrees/session-hook.sh, and its replacement. `settings` is
/// Err when the file could not be read.
pub fn settings_plan(settings: Result<&str, &str>, exe: &Path) -> String {
    let new_cmd = format!("\"{}\" session-hook", exe.display());
    let mut out = String::new();
    out.push_str("(b) settings.json — SessionStart hook\n");
    match settings {
        Err(reason) => {
            out.push_str(&format!("  unreadable: {reason}\n"));
            out.push_str(
                "  edit ~/.claude/settings.json manually: replace the command pointing\n  at auditing-worktrees/session-hook.sh with:\n",
            );
            out.push_str(&format!("    {new_cmd}\n"));
        }
        Ok(text) => match session_hook_command(text) {
            Some(old) => {
                out.push_str("  current command:\n");
                out.push_str(&format!("    {old}\n"));
                out.push_str("  new command:\n");
                out.push_str(&format!("    {new_cmd}\n"));
            }
            None => out.push_str(
                "  no SessionStart hook pointing at auditing-worktrees/session-hook.sh\n  found; nothing to replace.\n",
            ),
        },
    }
    out
}

/// Runs `migrate`: reads the live crontab and settings.json, prints the
/// two-section plan plus the manual checklist. Never mutates; always exits 0.
pub fn run() -> u8 {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("jeeves"));
    let crontab = cron::read_crontab();
    let settings = match std::fs::read_to_string(settings_file()) {
        Ok(text) => Ok(text),
        Err(e) => Err(format!("cannot read {}: {e}", settings_file().display())),
    };
    print!(
        "{}",
        cron_plan(crontab.as_deref().map_err(String::as_str), &exe)
    );
    print!(
        "{}",
        settings_plan(settings.as_deref().map_err(String::as_str), &exe)
    );
    print!("{}", checklist());
    0
}

/// The entry line inside a jeeves BEGIN/END block, if any.
fn block_entry_line(text: &str) -> Option<String> {
    let mut in_block = false;
    for line in text.lines() {
        if line.trim() == BEGIN {
            in_block = true;
            continue;
        }
        if line.trim() == END {
            break;
        }
        if in_block && !line.trim().is_empty() {
            return Some(line.to_string());
        }
    }
    None
}

/// The command string of the SessionStart hook pointing at
/// auditing-worktrees/session-hook.sh, if any.
fn session_hook_command(text: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(text).ok()?;
    let groups = value.get("hooks")?.get("SessionStart")?.as_array()?;
    for group in groups {
        let Some(hooks) = group.get("hooks").and_then(|h| h.as_array()) else {
            continue;
        };
        for hook in hooks {
            let Some(cmd) = hook.get("command").and_then(|c| c.as_str()) else {
                continue;
            };
            if cmd.contains("auditing-worktrees") && cmd.contains("session-hook.sh") {
                return Some(cmd.to_string());
            }
        }
    }
    None
}

pub fn checklist() -> String {
    let mut out = String::new();
    out.push_str("remaining manual steps:\n");
    out.push_str("  [ ] install the jeeves binary to ~/.local/bin/jeeves (stable PATH dir)\n");
    out.push_str(
        "  [ ] apply the crontab change: `crontab -e` (or `jeeves install-cron --write`)\n",
    );
    out.push_str("  [ ] apply the settings.json change above\n");
    out.push_str("  [ ] verify: `crontab -l` shows the new entry; a new session runs the hook\n");
    out.push_str("  [ ] follow-up PRs: fold the skill docs into jeeves, then delete\n");
    out.push_str("      orient/orient-quick/auditing-worktrees once cron+hook run green\n");
    out
}

fn home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default()
}
