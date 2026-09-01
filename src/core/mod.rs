pub mod config;
pub mod error;
pub mod git;
pub mod paths;
pub mod proc;
pub mod time;
pub mod toon;

#[cfg(test)]
pub(crate) mod tests {
    use std::sync::{Mutex, MutexGuard};

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    pub(crate) type EnvGuard = MutexGuard<'static, ()>;

    /// Serializes tests that mutate process env vars, which are otherwise
    /// shared mutable state across parallel test threads.
    pub(crate) fn env_lock() -> EnvGuard {
        ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }
}
