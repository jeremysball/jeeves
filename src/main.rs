// Phase 1 ships the shared core modules but no commands call them yet; the
// call sites land in later phases. Allow dead_code until then (the clippy
// gate runs with -D warnings, so this must be explicit, not incidental).
#![allow(dead_code)]

use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};

mod core;
mod digest;
mod migrate;
mod orient;
mod worktrees;

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
    /// Clean branches that are safe to delete
    Clean {
        repo: PathBuf,
        #[arg(value_name = "BRANCH", num_args = 1..)]
        branches: Vec<String>,
    },
}

fn main() -> ExitCode {
    // `jeeves coverage` parses its own args: the verdict contract publishes
    // exact error strings on stdout (mirroring ref/coverage-score:43-69),
    // which clap's error rendering would not reproduce.
    if std::env::args().nth(1).as_deref() == Some("coverage") {
        return run_coverage();
    }
    // `jeeves audit` likewise parses its own args: --no-content is accepted
    // in ANY position (mirroring ref/audit-worktrees.sh:22-27), which clap
    // would reject as a misplaced flag.
    if std::env::args().nth(1).as_deref() == Some("audit") {
        return run_audit();
    }
    // `jeeves archive` follows the reference command's positional parsing:
    // --list and --strict are recognized only in the first argument slot.
    if std::env::args().nth(1).as_deref() == Some("archive") {
        return run_archive();
    }
    // `jeeves clean` has one reference usage form for both a missing repo and
    // a missing branch list; handle those before clap emits its own rc-2 text.
    if std::env::args().nth(1).as_deref() == Some("clean") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        if args.len() < 2 {
            eprintln!("{}", worktrees::clean::USAGE);
            return ExitCode::from(1);
        }
    }
    // `jeeves session-hook` reads a JSON payload from stdin and must remain a
    // silent, always-successful command when the hook cannot report anything.
    if std::env::args().nth(1).as_deref() == Some("session-hook") {
        return run_session_hook();
    }
    if std::env::args().nth(1).as_deref() == Some("sessions") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        return ExitCode::from(orient::sessions::run(&args));
    }
    if std::env::args().nth(1).as_deref() == Some("checkin-lint") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        return ExitCode::from(orient::lint::run(&args));
    }
    if std::env::args().nth(1).as_deref() == Some("git-state") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        return ExitCode::from(orient::gitstate::run(&args));
    }
    if std::env::args().nth(1).as_deref() == Some("roots") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        return ExitCode::from(orient::roots::run(&args));
    }
    if std::env::args().nth(1).as_deref() == Some("session-tail") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        return ExitCode::from(orient::tail::run(&args));
    }
    if std::env::args().nth(1).as_deref() == Some("scan-active") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        return ExitCode::from(orient::scan::run(&args));
    }
    if std::env::args().nth(1).as_deref() == Some("install-cron") {
        let args: Vec<String> = std::env::args().skip(2).collect();
        return ExitCode::from(digest::cron::run(&args));
    }
    if std::env::args().nth(1).as_deref() == Some("migrate") {
        return ExitCode::from(migrate::run());
    }
    match Cli::parse().command {
        None | Some(Command::Version) => {
            println!("jeeves {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        Some(Command::Clean { repo, branches }) => {
            if branches.is_empty() {
                eprintln!("{}", worktrees::clean::USAGE);
                ExitCode::from(1)
            } else {
                ExitCode::from(worktrees::clean::clean_branches(&repo, &branches))
            }
        }
    }
}

fn run_audit() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(2).collect();

    // --no-content is accepted in any position; everything else is a root
    // dir (ref/audit-worktrees.sh:22-27). The first positional wins, extra
    // positionals are ignored, and the default root is the cwd.
    let mut no_content = false;
    let mut root: Option<PathBuf> = None;
    for arg in &args {
        if arg == "--no-content" {
            no_content = true;
        } else if root.is_none() {
            root = Some(PathBuf::from(arg));
        }
    }
    let root =
        root.unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));

    let opts = worktrees::audit::resolve_opts(no_content);
    print!("{}", worktrees::audit::audit_sweep(&[root], &opts));
    ExitCode::SUCCESS
}

fn run_archive() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(2).collect();
    ExitCode::from(worktrees::archive::run(&args))
}

fn run_session_hook() -> ExitCode {
    worktrees::hook::run();
    ExitCode::SUCCESS
}

fn run_coverage() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(2).collect();

    // --help as the first argument prints the usage text and exits 0
    // (ref/coverage-score:43-46).
    if args.first().map(String::as_str) == Some("--help") {
        print!("{}", COVERAGE_USAGE);
        return ExitCode::SUCCESS;
    }

    // Any flag anywhere is an unknown flag, checked before the count
    // (ref/coverage-score:48-55).
    for arg in &args {
        if arg.starts_with('-') {
            println!("error: unknown flag {arg}");
            return ExitCode::from(2);
        }
    }

    // Exactly three positionals (ref/coverage-score:56-59).
    if args.len() != 3 {
        println!("error: usage: coverage-score <repo> <base> <branch>");
        return ExitCode::from(2);
    }

    // Resolve <repo> to an absolute path; a relative path is resolved against
    // the caller's cwd. The cd's own error is a usage error reported on
    // stdout (ref/coverage-score:65-69).
    let repo = match std::fs::canonicalize(&args[0]) {
        Ok(p) if p.is_dir() => p,
        _ => {
            println!("error: not a directory: {}", args[0]);
            return ExitCode::from(2);
        }
    };

    match worktrees::coverage::coverage_score(&repo, &args[1], &args[2]) {
        Ok(verdict) => {
            println!("{verdict}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("{e}");
            ExitCode::from(e.exit_code())
        }
    }
}

const COVERAGE_USAGE: &str = "\
usage: coverage-score <repo> <base> <branch>

Scores how much of <branch>'s work is already in <base>, printing one line:
  SCORED <0-100>   - percent of the branch's text lines already in base.
  UNSCORED <why>   - binary/mode-only row, O==0, or empty patch.
  UNKNOWN <why>    - criss-cross history, merge conflict, merge-tree error.
Exit 0 on every successful run (including UNSCORED/UNKNOWN verdicts);
exit 2 on usage error.

examples:
  coverage-score \"$PWD\" main feature
  coverage-score /path/to/repo origin/main topic-branch
";
