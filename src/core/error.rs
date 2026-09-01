//! CLI error type and exit-code mapping.
//!
//! Mirrors the Python/SDD error families: 0 success, 1 refusal or
//! verification failure, 2 usage/flag error.

use std::fmt;

/// A user-facing error with an associated process exit code.
#[derive(Debug)]
pub struct CliError {
    pub kind: ErrorKind,
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// Refusal or verification failure -> exit 1.
    Refusal,
    /// Usage / flag error -> exit 2.
    Usage,
}

impl CliError {
    pub fn refusal(msg: impl Into<String>) -> Self {
        CliError {
            kind: ErrorKind::Refusal,
            message: msg.into(),
        }
    }

    pub fn usage(msg: impl Into<String>) -> Self {
        CliError {
            kind: ErrorKind::Usage,
            message: msg.into(),
        }
    }

    /// Process exit code for this error: 1 = refusal/verify-fail, 2 = usage.
    pub fn exit_code(&self) -> u8 {
        match self.kind {
            ErrorKind::Refusal => 1,
            ErrorKind::Usage => 2,
        }
    }
}

impl fmt::Display for CliError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for CliError {}

#[cfg(test)]
mod tests {
    use super::{CliError, ErrorKind};

    #[test]
    fn exit_codes_map_correctly() {
        assert_eq!(CliError::refusal("nope").exit_code(), 1);
        assert_eq!(CliError::usage("bad flag").exit_code(), 2);
    }

    #[test]
    fn kinds_are_distinct() {
        assert_eq!(CliError::refusal("x").kind, ErrorKind::Refusal);
        assert_eq!(CliError::usage("x").kind, ErrorKind::Usage);
    }
}
