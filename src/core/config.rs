//! Config resolution, mirroring bin/jeeves_lib.py:44-74.
//!
//! Precedence: CLI flag > env (canonical `JEEVES_*` first, then legacy
//! aliases) > config file key > default. The config file lives at
//! `state_dir()/config`, parsed as `KEY=VALUE` lines; `#` comments and blank
//! lines are ignored and the value splits on the FIRST `=`.

use std::collections::HashMap;
use std::path::Path;

use crate::core::paths::state_dir;

/// Resolves a setting: flag, then canonical env, then legacy env aliases in
/// order, then config key, then the built-in default.
pub fn resolve(
    flag: &Option<String>,
    env: &[&str],
    config: &HashMap<String, String>,
    default: &str,
) -> String {
    if let Some(value) = flag {
        return value.clone();
    }
    for var in env {
        if let Ok(value) = std::env::var(var) {
            if !value.is_empty() {
                return value;
            }
        }
    }
    if let Some(value) = config.get(env[0]) {
        return value.clone();
    }
    default.to_string()
}

/// Parses `KEY=VALUE` lines from `text`. Skips blank lines and lines whose
/// first non-whitespace character is `#`; splits on the FIRST `=`, so values
/// may contain `=` (e.g. URLs). Lines without `=` are skipped, mirroring
/// jeeves_lib.py's `"=" not in line` guard.
pub fn parse_config(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some(eq) = line.find('=') else {
            continue;
        };
        let (key, value) = line.split_at(eq);
        let key = key.trim();
        let value = value[1..].trim();
        if key.is_empty() {
            continue;
        }
        out.insert(key.to_string(), value.to_string());
    }
    out
}

/// Reads `state_dir()/config` if it exists; returns an empty map otherwise.
pub fn read_config(state_dir_override: Option<&Path>) -> HashMap<String, String> {
    let dir = match state_dir_override {
        Some(d) => d.to_path_buf(),
        None => state_dir(),
    };
    let file = dir.join("config");
    match std::fs::read_to_string(&file) {
        Ok(text) => parse_config(&text),
        Err(_) => HashMap::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::{parse_config, resolve};
    use std::collections::HashMap;

    fn env_lock() -> crate::core::tests::EnvGuard {
        crate::core::tests::env_lock()
    }

    fn cfg(entries: &[(&str, &str)]) -> HashMap<String, String> {
        entries
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn flag_beats_everything() {
        let _g = env_lock();
        assert_eq!(
            resolve(
                &Some("flag".into()),
                &["JEEVES_X", "LEGACY_X"],
                &cfg(&[]),
                "d"
            ),
            "flag"
        );
        assert_eq!(
            resolve(
                &Some("flag".into()),
                &["JEEVES_X", "LEGACY_X"],
                &cfg(&[("JEEVES_X", "c")]),
                "d"
            ),
            "flag"
        );
    }

    #[test]
    fn canonical_env_beats_alias() {
        let _g = env_lock();
        let env = vec![("JEEVES_X", "canon"), ("LEGACY_X", "alias")];
        for (k, v) in &env {
            unsafe { std::env::set_var(k, v) };
        }
        assert_eq!(
            resolve(&None, &["JEEVES_X", "LEGACY_X"], &cfg(&[]), "d"),
            "canon"
        );
    }

    #[test]
    fn alias_used_when_canonical_unset() {
        let _g = env_lock();
        unsafe {
            std::env::remove_var("JEEVES_X");
            std::env::set_var("LEGACY_X", "alias");
        }
        assert_eq!(
            resolve(&None, &["JEEVES_X", "LEGACY_X"], &cfg(&[]), "d"),
            "alias"
        );
    }

    #[test]
    fn env_beats_config_and_default() {
        let _g = env_lock();
        unsafe {
            std::env::set_var("JEEVES_X", "envval");
        }
        assert_eq!(
            resolve(&None, &["JEEVES_X"], &cfg(&[("JEEVES_X", "conf")]), "d"),
            "envval"
        );
    }

    #[test]
    fn empty_env_is_skipped() {
        let _g = env_lock();
        unsafe {
            std::env::set_var("JEEVES_X", "");
        }
        assert_eq!(
            resolve(&None, &["JEEVES_X"], &cfg(&[("JEEVES_X", "conf")]), "d"),
            "conf"
        );
    }

    #[test]
    fn config_beats_default() {
        let _g = env_lock();
        unsafe {
            std::env::remove_var("JEEVES_X");
        }
        assert_eq!(
            resolve(&None, &["JEEVES_X"], &cfg(&[("JEEVES_X", "conf")]), "d"),
            "conf"
        );
    }

    #[test]
    fn default_when_nothing_sets_it() {
        let _g = env_lock();
        unsafe {
            std::env::remove_var("JEEVES_X");
        }
        assert_eq!(resolve(&None, &["JEEVES_X"], &cfg(&[]), "d"), "d");
    }

    #[test]
    fn parses_key_value_and_skips_junk() {
        let text = "# comment\n\nfoo=bar\nbaz = qux = more\nnoequals\n# second comment\n";
        let m = parse_config(text);
        assert_eq!(m.get("foo").map(String::as_str), Some("bar"));
        assert_eq!(m.get("baz").map(String::as_str), Some("qux = more"));
        assert_eq!(m.len(), 2);
    }

    #[test]
    fn parses_url_with_equals() {
        let m = parse_config("url=https://x.example/y?a=1&b=2\n");
        assert_eq!(
            m.get("url").map(String::as_str),
            Some("https://x.example/y?a=1&b=2")
        );
    }

    #[test]
    fn empty_input_yields_empty_map() {
        assert!(parse_config("").is_empty());
        assert!(parse_config("# only comment\n\n").is_empty());
    }
}
