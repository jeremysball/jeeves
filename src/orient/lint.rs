//! Lint check-in bullets against the reference scannability rules.

use std::io::Read;

/// Runs `checkin-lint` with a file argument or stdin.
pub fn run(args: &[String]) -> u8 {
    let input = match args.first() {
        Some(path) => match std::fs::read_to_string(path) {
            Ok(input) => input,
            Err(error) => {
                eprintln!("{error}");
                return 1;
            }
        },
        None => {
            let mut input = String::new();
            if let Err(error) = std::io::stdin().read_to_string(&mut input) {
                eprintln!("{error}");
                return 1;
            }
            input
        }
    };

    // Python text streams apply universal-newline translation before
    // `readlines()` returns the input.
    let input = input.replace("\r\n", "\n").replace('\r', "\n");
    let mut failures = 0;
    for (line_number, line) in input.split_inclusive('\n').enumerate() {
        if !is_bullet(line) {
            continue;
        }
        let text = line.trim_end_matches('\n');
        let problems = lint_line(text);
        for problem in problems {
            println!("line {}: {problem}: {}", line_number + 1, line.trim_end());
            failures += 1;
        }
    }

    if failures > 0 {
        println!("{failures} violation(s) found.");
        1
    } else {
        println!("OK: all bullets pass.");
        0
    }
}

fn is_bullet(line: &str) -> bool {
    let mut chars = line
        .chars()
        .skip_while(|character| character.is_whitespace());
    let Some(marker) = chars.next() else {
        return false;
    };
    if marker != '-' && marker != '*' {
        return false;
    }
    chars.next().is_some_and(char::is_whitespace)
}

fn lint_line(line: &str) -> Vec<String> {
    let mut problems = Vec::new();
    let length = line.chars().count();
    if length > 120 {
        problems.push(format!("too long ({length} > 120 chars)"));
    }
    let commas = line.matches(',').count();
    if commas > 2 {
        problems.push(format!("too many commas ({commas} > 2)"));
    }
    if line.matches("**").count() / 2 > 1 {
        problems.push("more than one bold span".to_string());
    }
    problems
}
