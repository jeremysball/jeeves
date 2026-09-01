//! Human-readable tails of Claude Code JSONL sessions.

use serde_json::Value;
use std::io::Write;
use std::process::{Command, Stdio};

/// Runs `session-tail` with the reference-compatible positional arguments.
pub fn run(args: &[String]) -> u8 {
    let Some(file_arg) = args.first() else {
        eprintln!("usage: session-tail.sh <jsonl> [since-iso] [max]");
        return 1;
    };
    if !std::path::Path::new(file_arg).is_file() {
        println!("error: no such file: {file_arg}");
        return 1;
    }
    let Ok(input) = std::fs::read_to_string(file_arg) else {
        return 1;
    };
    let since = args.get(1).map(String::as_str).unwrap_or("");
    let max_arg = args
        .get(2)
        .map(String::as_str)
        .filter(|value| !value.is_empty())
        .unwrap_or("40");

    let mut entries = Vec::new();
    let mut jq_failed = false;
    for line in input.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            jq_failed = true;
            break;
        };
        jq_failed = false;
        let Some(timestamp) = value.get("timestamp").filter(|value| !value.is_null()) else {
            continue;
        };
        let Some(role) = value
            .get("message")
            .and_then(|message| message.get("role"))
            .filter(|value| !value.is_null())
        else {
            continue;
        };
        if !since.is_empty() && !timestamp_is_at_or_after(timestamp, since) {
            continue;
        }

        let Some(message) = value.get("message") else {
            continue;
        };
        let content = message.get("content");
        let Ok(text) = content_text(content) else {
            jq_failed = true;
            continue;
        };
        if text.is_empty() {
            continue;
        }

        let text: String = text.chars().take(800).collect();
        entries.push(format!(
            "[{}] {}: {text}",
            jq_text(timestamp),
            jq_text(role)
        ));
    }

    // The reference's jq-unavailable branch cannot occur here: serde_json is
    // the parser replacement, so the fallback line is unreachable by design.
    let rendered = if entries.is_empty() {
        String::new()
    } else {
        format!("{}\n", entries.join("\n"))
    };
    let Some(tail_status) = run_tail(&rendered, max_arg) else {
        return 1;
    };
    if tail_status != 0 {
        return tail_status;
    }
    if jq_failed {
        5
    } else {
        0
    }
}

fn timestamp_is_at_or_after(timestamp: &Value, since: &str) -> bool {
    // Session timestamps are ISO strings, so jq's comparison is a lexical
    // comparison in the normal and contractually relevant case.
    match timestamp {
        Value::String(timestamp) => timestamp.as_str() >= since,
        Value::Array(_) | Value::Object(_) => true,
        Value::Null | Value::Bool(_) | Value::Number(_) => false,
    }
}

fn content_text(content: Option<&Value>) -> Result<String, ()> {
    let Some(content) = content else {
        return Ok(String::new());
    };
    match content {
        Value::Null | Value::Bool(false) => Ok(String::new()),
        Value::Array(parts) => {
            let mut text_parts = Vec::new();
            for part in parts {
                if part.get("type") != Some(&Value::String("text".to_string())) {
                    continue;
                }
                let text = part.get("text").unwrap_or(&Value::Null);
                text_parts.push(jq_join_text(text)?);
            }
            Ok(text_parts.join(" "))
        }
        other => Ok(jq_text(other)),
    }
}

fn jq_join_text(value: &Value) -> Result<String, ()> {
    match value {
        Value::Null => Ok(String::new()),
        Value::Array(_) | Value::Object(_) => Err(()),
        _ => Ok(jq_text(value)),
    }
}

fn jq_text(value: &Value) -> String {
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => value.clone(),
        Value::Array(_) | Value::Object(_) => serde_json::to_string(value).unwrap_or_default(),
    }
}

fn run_tail(input: &str, max: &str) -> Option<u8> {
    let mut child = Command::new("tail")
        .args(["-n", max])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .ok()?;
    let write_failed = child
        .stdin
        .take()
        .map(|mut stdin| stdin.write_all(input.as_bytes()).is_err())
        .unwrap_or(false);
    let output = child.wait_with_output().ok()?;
    print!("{}", String::from_utf8_lossy(&output.stdout));
    let status = output.status.code().unwrap_or(1);
    Some(
        if status == 0 && (write_failed || is_zero_tail_limit(max) && !input.is_empty()) {
            // GNU tail -n 0 closes the pipe before jq can finish writing, so the
            // reference pipeline reports jq's SIGPIPE status (141).
            141
        } else {
            status as u8
        },
    )
}

fn is_zero_tail_limit(max: &str) -> bool {
    let max = max.trim();
    let digits = max.strip_prefix('-').unwrap_or(max);
    !digits.is_empty() && digits.chars().all(|character| character == '0')
}
