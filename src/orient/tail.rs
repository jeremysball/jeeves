//! Human-readable tails of Claude Code JSONL sessions.

use serde_json::Value;

/// Runs `session-tail` with the reference-compatible positional arguments.
pub fn run(args: &[String]) -> u8 {
    let Some(file_arg) = args.first() else {
        println!("usage: session-tail.sh <jsonl> [since-iso] [max]");
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
    let Ok(max) = max_arg.parse::<i64>() else {
        return 1;
    };

    let mut entries = Vec::new();
    for line in input.lines() {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
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
        let text = content_text(content);
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

    let entries = tail_entries(entries, max);
    if !entries.is_empty() {
        println!("{}", entries.join("\n"));
    }
    0
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

fn content_text(content: Option<&Value>) -> String {
    let Some(content) = content.filter(|value| !value.is_null()) else {
        return String::new();
    };
    match content {
        Value::Array(parts) => parts
            .iter()
            .filter(|part| part.get("type") == Some(&Value::String("text".to_string())))
            .map(|part| part.get("text").map_or_else(String::new, jq_join_text))
            .collect::<Vec<_>>()
            .join(" "),
        other => jq_text(other),
    }
}

fn jq_join_text(value: &Value) -> String {
    match value {
        Value::Null => String::new(),
        _ => jq_text(value),
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

fn tail_entries(entries: Vec<String>, max: i64) -> Vec<String> {
    if max >= 0 {
        let keep = max as usize;
        let start = entries.len().saturating_sub(keep);
        entries[start..].to_vec()
    } else {
        let skip = max.unsigned_abs() as usize;
        entries[skip.min(entries.len())..].to_vec()
    }
}
