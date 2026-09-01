//! State/data directory resolution, mirroring bin/jeeves_lib.py:44-60.
//!
//! `state_dir`: `$JEEVES_STATE_DIR` else `$XDG_STATE_HOME/jeeves` else
//! `~/.local/state/jeeves`; created mode 0700 on demand.
//! `data_dir`: `$JEEVES_DATA_DIR` else `$XDG_DATA_HOME/jeeves` else
//! `~/.local/share/jeeves`; created on demand. The Python original chmods
//! only the state dir (0700), not the data dir.  mirrored exactly.

use std::path::PathBuf;

/// `$JEEVES_STATE_DIR` if set, else `$XDG_STATE_HOME/jeeves`, else
/// `~/.local/state/jeeves`. Creates the directory (mode 0700) on demand.
pub fn state_dir() -> PathBuf {
    let d = dir_override("JEEVES_STATE_DIR")
        .or_else(|| xdg_join("XDG_STATE_HOME", "jeeves"))
        .unwrap_or_else(|| home_join(".local/state/jeeves"));
    match std::fs::create_dir_all(&d) {
        Ok(()) => {}
        Err(e) => panic!("cannot create state dir {}: {e}", d.display()),
    }
    let _ = std::fs::set_permissions(&d, std::os::unix::fs::PermissionsExt::from_mode(0o700));
    d
}

/// `$JEEVES_DATA_DIR` if set, else `$XDG_DATA_HOME/jeeves`, else
/// `~/.local/share/jeeves`. Creates the directory on demand.
pub fn data_dir() -> PathBuf {
    let d = dir_override("JEEVES_DATA_DIR")
        .or_else(|| xdg_join("XDG_DATA_HOME", "jeeves"))
        .unwrap_or_else(|| home_join(".local/share/jeeves"));
    match std::fs::create_dir_all(&d) {
        Ok(()) => {}
        Err(e) => panic!("cannot create data dir {}: {e}", d.display()),
    }
    d
}

fn dir_override(var: &str) -> Option<PathBuf> {
    match std::env::var_os(var) {
        Some(v) if !v.is_empty() => Some(PathBuf::from(v)),
        _ => None,
    }
}

fn xdg_join(var: &str, leaf: &str) -> Option<PathBuf> {
    std::env::var_os(var)
        .map(PathBuf::from)
        .map(|p| p.join(leaf))
}

fn home_join(rel: &str) -> PathBuf {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(rel)
}

#[cfg(test)]
mod tests {
    use super::{data_dir, state_dir};
    use std::path::Path;

    #[test]
    fn state_dir_override_is_honored() {
        let _g = crate::core::tests::env_lock();
        let d = std::env::temp_dir().join(format!("jeeves-state-{}", std::process::id()));
        std::env::set_var("JEEVES_STATE_DIR", &d);
        std::env::remove_var("XDG_STATE_HOME");
        std::env::set_var("HOME", std::env::temp_dir());
        assert_eq!(state_dir(), d);
    }

    #[test]
    fn state_dir_falls_back_to_xdg() {
        let _g = crate::core::tests::env_lock();
        let xdg = std::env::temp_dir().join("jeeves-xdg-test");
        std::env::remove_var("JEEVES_STATE_DIR");
        std::env::set_var("XDG_STATE_HOME", &xdg);
        std::env::set_var("HOME", std::env::temp_dir());
        assert_eq!(state_dir(), xdg.join("jeeves"));
    }

    #[test]
    fn state_dir_falls_back_to_home() {
        let _g = crate::core::tests::env_lock();
        let home = std::env::temp_dir().join("jeeves-home-test");
        std::env::remove_var("JEEVES_STATE_DIR");
        std::env::remove_var("XDG_STATE_HOME");
        std::env::set_var("HOME", &home);
        assert_eq!(state_dir(), home.join(".local/state/jeeves"));
    }

    #[test]
    fn data_dir_override_is_honored() {
        let _g = crate::core::tests::env_lock();
        let d = std::env::temp_dir().join(format!("jeeves-data-{}", std::process::id()));
        std::env::set_var("JEEVES_DATA_DIR", &d);
        std::env::remove_var("XDG_DATA_HOME");
        std::env::set_var("HOME", std::env::temp_dir());
        assert_eq!(data_dir(), d);
    }

    #[test]
    fn data_dir_falls_back_to_xdg() {
        let _g = crate::core::tests::env_lock();
        let xdg = std::env::temp_dir().join("jeeves-xdg-test");
        std::env::remove_var("JEEVES_DATA_DIR");
        std::env::set_var("XDG_DATA_HOME", &xdg);
        std::env::set_var("HOME", std::env::temp_dir());
        assert_eq!(data_dir(), xdg.join("jeeves"));
    }

    #[test]
    fn data_dir_falls_back_to_home() {
        let _g = crate::core::tests::env_lock();
        let home = std::env::temp_dir().join("jeeves-home-test");
        std::env::remove_var("JEEVES_DATA_DIR");
        std::env::remove_var("XDG_DATA_HOME");
        std::env::set_var("HOME", &home);
        assert_eq!(data_dir(), home.join(".local/share/jeeves"));
    }

    #[test]
    fn state_dir_is_created_mode_0700() {
        let _g = crate::core::tests::env_lock();
        let d = std::env::temp_dir().join(format!("jeeves-mode-{}", std::process::id()));
        std::env::set_var("JEEVES_STATE_DIR", &d);
        std::env::set_var("HOME", std::env::temp_dir());
        state_dir();
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&d).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o700);
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn dirs_are_absolute_paths() {
        let d = state_dir();
        assert!(Path::is_absolute(&d));
        assert!(d.ends_with("jeeves"));
    }
}
