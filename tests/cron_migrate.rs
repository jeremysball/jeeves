//! Golden and behavioral tests for `jeeves install-cron` and `jeeves migrate`.
//!
//! The entry-line golden pins the byte shape of the jeeves BEGIN/END block
//! with a stub binary path and a controlled environment (HOME, XDG_DATA_HOME,
//! JEEVES_MISE_SHIMS, JEEVES_STATE_DIR), so the PATH builder's stable-dirs
//! ordering and the markers are asserted exactly. Idempotency exercises the
//! pure string-level merge function directly. The migrate test parses a
//! fixture settings.json via the JEEVES_SETTINGS_FILE override.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use jeeves::core::paths::state_dir_path;
use jeeves::digest::cron::{self, BEGIN, END};
use jeeves::migrate;

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn scratch_dir() -> PathBuf {
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let d = std::env::temp_dir().join(format!("jeeves-cron-{}-{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// Serializes tests that mutate process env vars (shared mutable state).
fn env_lock() -> std::sync::MutexGuard<'static, ()> {
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

fn set_env(guard: &std::sync::MutexGuard<'static, ()>, key: &str, value: &str) {
    let _ = guard;
    unsafe { std::env::set_var(key, value) };
}

fn remove_env(guard: &std::sync::MutexGuard<'static, ()>, key: &str) {
    let _ = guard;
    unsafe { std::env::remove_var(key) };
}

#[test]
fn entry_line_golden() {
    let _g = env_lock();
    let home = scratch_dir();
    let state = home.join("state");
    let shims = home.join("shims");
    set_env(&_g, "HOME", home.to_str().unwrap());
    set_env(&_g, "JEEVES_MISE_SHIMS", shims.to_str().unwrap());
    set_env(&_g, "JEEVES_STATE_DIR", state.to_str().unwrap());
    remove_env(&_g, "MISE_DATA_DIR");
    remove_env(&_g, "XDG_DATA_HOME");

    let binary = Path::new("/opt/jeeves/bin/jeeves");
    let block = cron::block(binary);

    let expected = format!(
        "{BEGIN}\n13 * * * * PATH={}/.local/bin:{}/shims:/usr/local/bin:/usr/bin:/bin /opt/jeeves/bin/jeeves collect >> {}/collect.log 2>&1\n{END}\n",
        home.display(),
        home.display(),
        state.display()
    );
    assert_eq!(block, expected);
    assert_eq!(block.matches(BEGIN).count(), 1);
    assert_eq!(block.matches(END).count(), 1);
    assert!(block.ends_with(&format!("{END}\n")));
}

#[test]
fn cron_path_keeps_stable_dirs_ordering() {
    let _g = env_lock();
    let home = scratch_dir();
    set_env(&_g, "HOME", home.to_str().unwrap());
    set_env(
        &_g,
        "JEEVES_MISE_SHIMS",
        home.join("shims").to_str().unwrap(),
    );
    remove_env(&_g, "MISE_DATA_DIR");
    remove_env(&_g, "XDG_DATA_HOME");

    let path = cron::cron_path();
    let dirs: Vec<&str> = path.split(':').collect();
    assert_eq!(
        dirs,
        vec![
            home.join(".local/bin").to_str().unwrap(),
            home.join("shims").to_str().unwrap(),
            "/usr/local/bin",
            "/usr/bin",
            "/bin"
        ]
    );
    assert!(!path.contains("/installs/"));
}

#[test]
fn merge_replaces_block_not_duplicates() {
    let _g = env_lock();
    let home = scratch_dir();
    set_env(&_g, "HOME", home.to_str().unwrap());
    set_env(
        &_g,
        "JEEVES_MISE_SHIMS",
        home.join("shims").to_str().unwrap(),
    );
    set_env(
        &_g,
        "JEEVES_STATE_DIR",
        home.join("state").to_str().unwrap(),
    );
    remove_env(&_g, "MISE_DATA_DIR");
    remove_env(&_g, "XDG_DATA_HOME");
    let binary = Path::new("/opt/jeeves/bin/jeeves");
    let existing = "0 * * * * /usr/bin/other-job\n";
    let once = cron::merge(existing, binary);
    let twice = cron::merge(&once, binary);
    assert_eq!(once, twice);
    assert!(once.contains("other-job"));
    assert_eq!(once.matches(BEGIN).count(), 1);
    assert_eq!(once.matches(END).count(), 1);
    assert!(once.contains("13 * * * *"));
}

#[test]
fn merge_into_empty_is_just_the_block() {
    let _g = env_lock();
    let home = scratch_dir();
    set_env(&_g, "HOME", home.to_str().unwrap());
    set_env(
        &_g,
        "JEEVES_MISE_SHIMS",
        home.join("shims").to_str().unwrap(),
    );
    set_env(
        &_g,
        "JEEVES_STATE_DIR",
        home.join("state").to_str().unwrap(),
    );
    remove_env(&_g, "MISE_DATA_DIR");
    remove_env(&_g, "XDG_DATA_HOME");
    let binary = Path::new("/opt/jeeves/bin/jeeves");
    assert_eq!(cron::merge("", binary), cron::block(binary));
    assert_eq!(cron::merge("\n\n", binary), cron::block(binary));
}

#[test]
fn strip_block_keeps_surrounding_lines() {
    let binary = Path::new("/opt/jeeves/bin/jeeves");
    let text = "0 * * * * a\n# BEGIN jeeves\n13 * * * * old\n# END jeeves\n30 * * * * b\n";
    let stripped = cron::strip_block(text);
    assert!(!stripped.contains("jeeves"));
    assert!(stripped.contains("0 * * * * a"));
    assert!(stripped.contains("30 * * * * b"));
    let merged = cron::merge(text, binary);
    assert_eq!(merged.matches(BEGIN).count(), 1);
    assert!(merged.contains("0 * * * * a"));
    assert!(merged.contains("30 * * * * b"));
}

#[test]
fn migrate_plan_on_fixture_settings() {
    let _g = env_lock();
    let home = scratch_dir();
    let state = home.join("state");
    set_env(&_g, "HOME", home.to_str().unwrap());
    set_env(&_g, "JEEVES_STATE_DIR", state.to_str().unwrap());
    set_env(
        &_g,
        "JEEVES_MISE_SHIMS",
        home.join("shims").to_str().unwrap(),
    );
    remove_env(&_g, "MISE_DATA_DIR");
    remove_env(&_g, "XDG_DATA_HOME");

    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/settings.json");
    set_env(&_g, "JEEVES_SETTINGS_FILE", fixture.to_str().unwrap());

    let exe = Path::new("/opt/jeeves/bin/jeeves");
    let crontab = "0 * * * * /usr/bin/other-job\n# BEGIN jeeves\n13 * * * * PATH=/old/bin /old/collect.py >> /old/collect.log 2>&1\n# END jeeves\n";
    let settings = std::fs::read_to_string(&fixture).unwrap();

    let plan = format!(
        "{}{}{}",
        migrate::cron_plan(Ok(crontab), exe),
        migrate::settings_plan(Ok(&settings), exe),
        migrate::checklist()
    );

    assert!(plan.contains("(a) crontab"));
    assert!(plan.contains("current entry:"));
    assert!(plan.contains("/old/collect.py"));
    assert!(plan.contains("new entry:"));
    assert!(plan.contains("13 * * * *"));
    assert!(plan.contains("/opt/jeeves/bin/jeeves collect >>"));
    assert!(plan.contains(&format!("{}/collect.log", state.display())));

    assert!(plan.contains("(b) settings.json"));
    assert!(plan.contains("current command:"));
    assert!(plan.contains("auditing-worktrees/bin/session-hook.sh"));
    assert!(plan.contains("new command:"));
    assert!(plan.contains("\"/opt/jeeves/bin/jeeves\" session-hook"));

    assert!(plan.contains("remaining manual steps:"));
    assert!(plan.contains("crontab -e"));
    assert!(plan.contains("~/.local/bin/jeeves"));
}

#[test]
fn migrate_plan_says_unreadable_honestly() {
    let _g = env_lock();
    let home = scratch_dir();
    set_env(&_g, "HOME", home.to_str().unwrap());
    set_env(
        &_g,
        "JEEVES_STATE_DIR",
        home.join("state").to_str().unwrap(),
    );
    set_env(
        &_g,
        "JEEVES_MISE_SHIMS",
        home.join("shims").to_str().unwrap(),
    );
    remove_env(&_g, "MISE_DATA_DIR");
    remove_env(&_g, "XDG_DATA_HOME");
    set_env(
        &_g,
        "JEEVES_SETTINGS_FILE",
        home.join("missing.json").to_str().unwrap(),
    );

    let exe = Path::new("/opt/jeeves/bin/jeeves");
    let plan = format!(
        "{}{}",
        migrate::cron_plan(Err("crontab -l failed (exit 1): no crontab for user"), exe),
        migrate::settings_plan(Err("cannot read /nope/settings.json: No such file"), exe)
    );
    assert!(plan.contains("unreadable: crontab -l failed"));
    assert!(plan.contains("unreadable: cannot read /nope/settings.json"));
    assert!(plan.contains("new entry:"));
    assert!(plan.contains("13 * * * *"));
}

#[test]
fn state_dir_path_is_used_for_log_not_created() {
    let _g = env_lock();
    let home = scratch_dir();
    let state = home.join("state");
    set_env(&_g, "HOME", home.to_str().unwrap());
    set_env(&_g, "JEEVES_STATE_DIR", state.to_str().unwrap());
    set_env(
        &_g,
        "JEEVES_MISE_SHIMS",
        home.join("shims").to_str().unwrap(),
    );
    remove_env(&_g, "MISE_DATA_DIR");
    remove_env(&_g, "XDG_DATA_HOME");

    let line = cron::entry_line(Path::new("/opt/jeeves/bin/jeeves"));
    assert!(line.contains(&format!("{}/collect.log", state.display())));
    assert_eq!(state_dir_path(), state);
    assert!(
        !state.exists(),
        "printing the entry must not create the state dir"
    );
}
