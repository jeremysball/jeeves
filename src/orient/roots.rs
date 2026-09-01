//! Canonical git root discovery, matching ref/discover-roots.sh.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

const DESCRIPTION: &str = "Canonical git repo roots, deduplicated by remote URL";

#[derive(Debug)]
struct Candidate {
    path: String,
    newest_timestamp: i64,
    primary: bool,
}

/// Runs `roots`, including the reference's help and unknown-flag handling.
pub fn run(args: &[String]) -> u8 {
    for arg in args {
        match arg.as_str() {
            "--help" => {
                print_usage();
                return 0;
            }
            value if value.starts_with("--") => {
                println!("error: unknown flag {value}");
                return 2;
            }
            _ => {}
        }
    }

    let scan_roots = if args.is_empty() {
        candidate_roots_from_env()
    } else {
        args.iter().map(PathBuf::from).collect()
    };

    let state_home = state_home();
    let default_file = state_home.join("jeeves/roots.txt");
    let roots_file = std::env::var_os("JEEVES_ROOTS_FILE")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| default_file.clone());

    let mut chosen: BTreeMap<String, Candidate> = BTreeMap::new();
    for root in scan_roots {
        if !root.is_dir() {
            continue;
        }
        let mut git_entries = Vec::new();
        collect_git_entries(&root, 0, &mut git_entries);
        git_entries.sort();
        for git_entry in git_entries {
            let Some(repo_path) = git_entry.parent() else {
                continue;
            };
            let Some(origin) = origin_of(repo_path) else {
                continue;
            };
            let key = normalize_url(&origin);
            if key.is_empty() {
                continue;
            }
            let path = repo_path.to_string_lossy().into_owned();
            let candidate = Candidate {
                primary: repo_path.join(".git").is_dir(),
                newest_timestamp: newest_timestamp(repo_path),
                path,
            };
            match chosen.get_mut(&key) {
                None => {
                    chosen.insert(key, candidate);
                }
                Some(current)
                    if candidate.primary && !current.primary
                        || candidate.primary == current.primary
                            && candidate.newest_timestamp > current.newest_timestamp =>
                {
                    *current = candidate;
                }
                Some(_) => {}
            }
        }
    }

    let mut paths: Vec<String> = chosen
        .values()
        .map(|candidate| candidate.path.clone())
        .collect();
    paths.sort();
    let roots_content = if paths.is_empty() {
        String::new()
    } else {
        format!("{}\n", paths.join("\n"))
    };
    write_file(&roots_file, &roots_content);

    // Keep the old orient location current only when the canonical jeeves
    // location is being used and an old file already exists.
    let legacy_file = state_home.join("orient/roots.txt");
    if roots_file == default_file && legacy_file.is_file() {
        write_file(&legacy_file, &roots_content);
    }

    let bin = self_display();
    println!("bin: {bin}");
    println!("description: {DESCRIPTION}");
    println!("roots_file: {}", roots_file.display());
    println!("count: {} distinct remotes", paths.len());
    println!();
    println!("roots[{}]{{path}}:", paths.len());
    for path in paths {
        println!("  {path}");
    }
    0
}

fn print_usage() {
    println!("bin: {}", self_display());
    println!("description: {DESCRIPTION}");
    println!();
    println!("usage: discover-roots.sh [root ...]");
    println!();
    println!("arguments:");
    println!("  [root]   dirs to scan for git repos; defaults to $ORIENT_ROOT_CANDIDATES");
    println!("           (space/colon separated), else /workspace $HOME/.claude $HOME/.dotfiles");
    println!();
    println!("flags:");
    println!("  --help   show this reference");
    println!();
    println!("environment:");
    println!("  ORIENT_ROOT_CANDIDATES   dirs to scan when no [root] is given");
    println!("  ORIENT_ROOTS_FILE        where to persist the discovered roots");
    println!("                           (default $XDG_STATE_HOME/orient/roots.txt)");
}

fn candidate_roots_from_env() -> Vec<PathBuf> {
    let value = nonempty_env("ORIENT_ROOT_CANDIDATES")
        .or_else(|| nonempty_env("JEEVES_ROOT_CANDIDATES"))
        .unwrap_or_else(|| {
            let home = std::env::var("HOME").unwrap_or_default();
            format!("/workspace {home}/.claude {home}/.dotfiles")
        });
    value
        .split([':', ' '])
        .filter(|part| !part.is_empty())
        .map(PathBuf::from)
        .collect()
}

fn nonempty_env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|value| !value.is_empty())
}

fn state_home() -> PathBuf {
    std::env::var_os("XDG_STATE_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let home = std::env::var_os("HOME").unwrap_or_default();
            PathBuf::from(home).join(".local/state")
        })
}

fn collect_git_entries(dir: &Path, depth: usize, output: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        let name = name.to_string_lossy();
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        if (name == "node_modules" || name == ".cache") && file_type.is_dir() {
            continue;
        }
        if name == ".git" && depth < 4 {
            if file_type.is_dir() || file_type.is_file() {
                output.push(path);
            }
            continue;
        }
        if file_type.is_dir() && depth < 3 {
            collect_git_entries(&path, depth + 1, output);
        }
    }
}

fn origin_of(repo: &Path) -> Option<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["remote", "get-url", "origin"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8_lossy(&output.stdout);
    let value = value.trim_end_matches('\n');
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

fn newest_timestamp(repo: &Path) -> i64 {
    let Ok(output) = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(["log", "-1", "--format=%ct"])
        .output()
    else {
        return 0;
    };
    if !output.status.success() {
        return 0;
    }
    String::from_utf8_lossy(&output.stdout)
        .trim_end_matches('\n')
        .parse()
        .unwrap_or(0)
}

fn normalize_url(url: &str) -> String {
    let mut normalized = url.to_string();
    for prefix in ["https://", "http://", "ssh://"] {
        if let Some(rest) = normalized.strip_prefix(prefix) {
            normalized = rest.to_string();
        }
    }
    if let Some(rest) = normalized.strip_prefix("git@") {
        normalized = rest.to_string();
    }
    if let Some(colon) = normalized.find(':') {
        normalized.replace_range(colon..=colon, "/");
    }
    if let Some(rest) = normalized.strip_suffix(".git") {
        normalized = rest.to_string();
    }
    normalized
}

fn write_file(path: &Path, content: &str) {
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, content);
}

fn self_display() -> String {
    let self_path = std::env::args()
        .next()
        .unwrap_or_else(|| "jeeves".to_string());
    let home = std::env::var("HOME").unwrap_or_default();
    if !home.is_empty() {
        if self_path == home {
            return "~".to_string();
        }
        if let Some(rest) = self_path.strip_prefix(&(home.clone() + "/")) {
            return format!("~/{rest}");
        }
    }
    self_path
}
