// Phase 1 ships the shared core modules but no commands call them yet; the
// call sites land in later phases. Allow dead_code until then (the clippy
// gate runs with -D warnings, so this must be explicit, not incidental).
#![allow(dead_code)]

use std::process::ExitCode;

use clap::{Parser, Subcommand};

mod core;

#[derive(Parser)]
#[command(
    name = "jeeves",
    version,
    about = "Jeeves - worktree and digest automation"
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Print the jeeves version and exit
    Version,
}

fn main() -> ExitCode {
    // Phase 1 ships exactly one command: version. Bare invocation and
    // `-V/--version` (handled by clap before we get here) behave the same.
    match Cli::parse().command {
        None | Some(Command::Version) => {
            println!("jeeves {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
    }
}
