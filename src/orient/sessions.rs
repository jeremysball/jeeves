//! Discover the latest Claude and OpenCode sessions for a directory.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::UNIX_EPOCH;

/// Runs `sessions` with the reference-compatible positional arguments.
pub fn run(args: &[String]) -> u8 {
    let raw_dir = args
        .first()
        .filter(|value| !value.is_empty())
        .cloned()
        .unwrap_or_else(|| {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .to_string_lossy()
                .into_owned()
        });
    let dir = resolved_dir(&raw_dir);
    let projects = claude_projects_dir();
    let slug = path_slug(&dir);
    let project = projects.join(slug);

    let latest = newest_jsonl(&project).or_else(|| {
        let candidate = fallback_project(&projects, &dir)?;
        newest_jsonl(&candidate)
    });
    if let Some(path) = latest {
        println!("CLAUDE_JSONL={}", path.display());
    }

    emit_opencode_session(&dir);
    0
}

fn resolved_dir(raw_dir: &str) -> String {
    let original = std::env::current_dir().ok();
    let logical_cwd = logical_current_dir(original.as_deref());
    if std::env::set_current_dir(raw_dir).is_err() {
        raw_dir.to_string()
    } else {
        let path = if Path::new(raw_dir).is_absolute() {
            PathBuf::from(raw_dir)
        } else {
            logical_cwd.join(raw_dir)
        };
        let resolved = normalize_logical_path(path);
        if let Some(original) = original {
            let _ = std::env::set_current_dir(original);
        }
        resolved.to_string_lossy().into_owned()
    }
}

fn logical_current_dir(fallback: Option<&Path>) -> PathBuf {
    std::env::var_os("PWD")
        .map(PathBuf::from)
        .filter(|path| path.is_absolute() && path.is_dir())
        .or_else(|| fallback.map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn normalize_logical_path(path: PathBuf) -> PathBuf {
    let is_absolute = path.is_absolute();
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                if !normalized.pop() && !is_absolute {
                    normalized.push(component.as_os_str());
                }
            }
            _ => normalized.push(component.as_os_str()),
        }
    }
    normalized
}

fn claude_projects_dir() -> PathBuf {
    let home = std::env::var_os("HOME").unwrap_or_default();
    if home.is_empty() {
        PathBuf::from("/.claude/projects")
    } else {
        PathBuf::from(home).join(".claude/projects")
    }
}

fn path_slug(path: &str) -> String {
    path.bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() {
                byte as char
            } else {
                '-'
            }
        })
        .collect()
}

fn newest_jsonl(dir: &Path) -> Option<PathBuf> {
    let entries = std::fs::read_dir(dir).ok()?;
    let mut files = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        if name.to_string_lossy().starts_with('.')
            || path.extension().and_then(|value| value.to_str()) != Some("jsonl")
        {
            continue;
        }
        let Ok(metadata) = std::fs::metadata(&path) else {
            continue;
        };
        if metadata.is_file() {
            let modified = metadata.modified().unwrap_or(UNIX_EPOCH);
            files.push((modified, path));
        }
    }
    files.sort_by(|(left_time, left_path), (right_time, right_path)| {
        right_time
            .cmp(left_time)
            .then_with(|| left_path.cmp(right_path))
    });
    files.into_iter().next().map(|(_, path)| path)
}

fn fallback_project(projects: &Path, dir: &str) -> Option<PathBuf> {
    let tail = Path::new(dir).file_name()?.to_string_lossy();
    let pattern = format!(".*{}$", path_slug(&tail));
    let output = Command::new("fd")
        .args(["-t", "d", "-d", "1"])
        .arg(pattern)
        .arg(projects)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .next()
        .filter(|line| !line.is_empty())
        .map(PathBuf::from)
}

fn emit_opencode_session(dir: &str) {
    let Some(limit) = opencode_scan_limit() else {
        return;
    };
    if !executable_in_path("opencode") {
        return;
    }

    let Some(list) = timed_opencode("30", &["session", "list"]) else {
        return;
    };
    for id in session_ids(&list, limit) {
        let Some(export) = timed_opencode("20", &["export", id.as_str()]) else {
            continue;
        };
        if directory_from_export(&export).as_deref() == Some(dir) {
            println!("OPENCODE_SESSION={id}");
            break;
        }
    }
}

fn opencode_scan_limit() -> Option<usize> {
    let raw = std::env::var("JEEVES_ORIENT_OPENCODE_SCAN")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| {
            std::env::var("ORIENT_OPENCODE_SCAN")
                .ok()
                .filter(|value| !value.is_empty())
        })
        .unwrap_or_else(|| "12".to_string());
    let value = raw.parse::<i64>().ok()?;
    (value > 0).then_some(value as usize)
}

fn executable_in_path(name: &str) -> bool {
    let path = std::env::var_os("PATH").unwrap_or_default();
    std::env::split_paths(&path).any(|directory| {
        let candidate = if directory.as_os_str().is_empty() {
            PathBuf::from(name)
        } else {
            directory.join(name)
        };
        is_executable(&candidate)
    })
}

#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    std::fs::metadata(path)
        .map(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable(path: &Path) -> bool {
    path.is_file()
}

fn timed_opencode(seconds: &str, args: &[&str]) -> Option<Vec<u8>> {
    Command::new("timeout")
        .arg(seconds)
        .arg("opencode")
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()
        .map(|output| output.stdout)
}

fn session_ids(output: &[u8], limit: usize) -> Vec<String> {
    String::from_utf8_lossy(output)
        .lines()
        .filter_map(|line| {
            let suffix = line.strip_prefix("ses_")?;
            let length = suffix
                .bytes()
                .take_while(|byte| byte.is_ascii_alphanumeric())
                .count();
            (length > 0).then(|| format!("ses_{}", &suffix[..length]))
        })
        .take(limit)
        .collect()
}

fn directory_from_export(output: &[u8]) -> Option<String> {
    let prefix = &output[..output.len().min(800)];
    let needle = b"\"directory\"";
    let mut offset = 0;
    while offset + needle.len() <= prefix.len() {
        let relative = prefix[offset..]
            .windows(needle.len())
            .position(|window| window == needle)?;
        let start = offset + relative;
        let mut cursor = start + needle.len();
        while cursor < prefix.len() && is_json_space(prefix[cursor]) {
            cursor += 1;
        }
        if cursor >= prefix.len() || prefix[cursor] != b':' {
            offset = start + needle.len();
            continue;
        }
        cursor += 1;
        while cursor < prefix.len() && is_json_space(prefix[cursor]) {
            cursor += 1;
        }
        if cursor >= prefix.len() || prefix[cursor] != b'"' {
            offset = start + needle.len();
            continue;
        }
        cursor += 1;
        let value_start = cursor;
        while cursor < prefix.len() && prefix[cursor] != b'"' {
            cursor += 1;
        }
        if cursor < prefix.len() {
            return String::from_utf8(prefix[value_start..cursor].to_vec()).ok();
        }
        offset = start + needle.len();
    }
    None
}

fn is_json_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}
