//! Persistent stores: seen rows, pending queue, repo-import map, evidence
//! memo, and last-wake stamp, mirroring bin/todos.py and stores.txt.
//!
//! Byte-faithful JSON notes (matching Python `json.dumps` defaults):
//! - compact output uses `", "` and `": "` separators and `ensure_ascii`
//!   escaping (`\uXXXX`, surrogate pairs above BMP).
//! - pretty output (`save_pending`) reproduces `json.dumps(items, indent=1)`:
//!   one space of indentation per nesting level, `"key": value`, `,\n`
//!   item separators, `[]`/`{}` for empty collections.
//! - Object keys are emitted in Python dict insertion order. serde_json's
//!   default `Map` sorts keys (BTreeMap), so objects are never written
//!   straight from a `Value`; every object writer here orders its keys.

use crate::core::paths::state_dir;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

/// Canonical key order of a pending queue row, as the ferry emits it
/// (`bin/todos.py`'s `valid` set, minus the display-only `state`).
const PENDING_KEYS: [&str; 9] = [
    "op", "line", "evidence", "repo", "reason", "queued", "seen", "kind", "source",
];

/// Keys of a `value` object with the canonical pending keys first (in
/// canonical order), any extra keys after them in sorted order — a
/// deterministic byte-faithful ordering for every row Python writes.
fn ordered_pairs(m: &serde_json::Map<String, Value>) -> Vec<(String, Value)> {
    let mut out: Vec<(String, Value)> = Vec::with_capacity(m.len());
    for k in PENDING_KEYS {
        if let Some(v) = m.get(k) {
            out.push((k.to_string(), v.clone()));
        }
    }
    let mut extra: Vec<(&String, &Value)> = m
        .iter()
        .filter(|(k, _)| !PENDING_KEYS.contains(&k.as_str()))
        .collect();
    extra.sort_by(|a, b| a.0.cmp(b.0));
    for (k, v) in extra {
        out.push((k.clone(), v.clone()));
    }
    out
}

/// Python `json.dumps` `ensure_ascii=True` string escaping: `\uXXXX` for
/// every code point at or above DEL, surrogate pairs above the BMP.
fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) < 0x7f => out.push(c),
            c if (c as u32) < 0x10000 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => {
                let u = c as u32 - 0x10000;
                out.push_str(&format!(
                    "\\u{:04x}\\u{:04x}",
                    0xd800 + (u >> 10),
                    0xdc00 + (u & 0x3ff)
                ));
            }
        }
    }
    out.push('"');
    out
}

fn indent(level: usize, out: &mut String) {
    for _ in 0..level {
        out.push(' ');
    }
}

/// Pretty JSON matching Python `json.dumps(v, indent=1)`: one space per
/// nesting level, `"key": value`, no spaces after `,`.
fn dump_indent1(v: &Value) -> String {
    let mut out = String::new();
    write_indent1(v, 0, &mut out);
    out
}

fn write_indent1(v: &Value, level: usize, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(&b.to_string()),
        Value::Number(n) => out.push_str(&n.to_string()),
        Value::String(s) => out.push_str(&esc(s)),
        Value::Array(a) => {
            out.push('[');
            if !a.is_empty() {
                out.push('\n');
                for (i, item) in a.iter().enumerate() {
                    indent(level + 1, out);
                    write_indent1(item, level + 1, out);
                    if i + 1 < a.len() {
                        out.push(',');
                    }
                    out.push('\n');
                }
                indent(level, out);
            }
            out.push(']');
        }
        Value::Object(m) => {
            out.push('{');
            if !m.is_empty() {
                out.push('\n');
                for (i, (k, val)) in ordered_pairs(m).iter().enumerate() {
                    indent(level + 1, out);
                    out.push_str(&esc(k));
                    out.push_str(": ");
                    write_indent1(val, level + 1, out);
                    if i + 1 < m.len() {
                        out.push(',');
                    }
                    out.push('\n');
                }
                indent(level, out);
            }
            out.push('}');
        }
    }
}

/// One seen row: the six fields the Python original writes, in the same
/// declaration order so serialization is byte-identical.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SeenRow {
    pub hash: String,
    pub line: String,
    pub first_seen: String,
    pub last_seen: String,
    pub count: u64,
    pub status: String,
}

impl SeenRow {
    /// Compact JSON with keys in insertion order: hash, line, first_seen,
    /// last_seen, count, status.
    fn compact(&self) -> String {
        let mut out = String::from("{");
        out.push_str("\"hash\": ");
        out.push_str(&esc(&self.hash));
        out.push_str(", \"line\": ");
        out.push_str(&esc(&self.line));
        out.push_str(", \"first_seen\": ");
        out.push_str(&esc(&self.first_seen));
        out.push_str(", \"last_seen\": ");
        out.push_str(&esc(&self.last_seen));
        out.push_str(", \"count\": ");
        out.push_str(&self.count.to_string());
        out.push_str(", \"status\": ");
        out.push_str(&esc(&self.status));
        out.push('}');
        out
    }
}

/// `seen.ndjson`: one compact JSON row per line, insertion-ordered.
pub struct SeenStore {
    pub path: std::path::PathBuf,
    pub rows: Vec<(String, SeenRow)>,
}

impl SeenStore {
    /// Loads `state_dir()/seen.ndjson`, skipping blank and unparsable lines.
    pub fn load() -> Self {
        let path = state_dir().join("seen.ndjson");
        let mut rows = Vec::new();
        if let Ok(text) = std::fs::read_to_string(&path) {
            for line in text.lines() {
                if line.trim().is_empty() {
                    continue;
                }
                if let Ok(r) = serde_json::from_str::<SeenRow>(line) {
                    rows.push((r.hash.clone(), r));
                }
            }
        }
        Self { path, rows }
    }

    pub fn check(&self, h: &str) -> Option<&SeenRow> {
        self.rows
            .iter()
            .find(|(k, _)| k.as_str() == h)
            .map(|(_, r)| r)
    }

    pub fn upsert(&mut self, h: &str, line: &str, status: &str, now: &str) -> SeenRow {
        match self.rows.iter_mut().find(|(k, _)| k.as_str() == h) {
            Some((_, r)) => {
                r.last_seen = now.to_string();
                r.count += 1;
                r.status = status.to_string();
                r.clone()
            }
            None => {
                let r = SeenRow {
                    hash: h.to_string(),
                    line: line.to_string(),
                    first_seen: now.to_string(),
                    last_seen: now.to_string(),
                    count: 1,
                    status: status.to_string(),
                };
                self.rows.push((h.to_string(), r.clone()));
                r
            }
        }
    }

    pub fn set_status(&mut self, h: &str, status: &str) {
        if let Some((_, r)) = self.rows.iter_mut().find(|(k, _)| k.as_str() == h) {
            r.status = status.to_string();
        }
    }

    pub fn by_status(&self, status: &str) -> Vec<&SeenRow> {
        self.rows
            .iter()
            .filter(|(_, r)| r.status == status)
            .map(|(_, r)| r)
            .collect()
    }

    /// Overwrites the file atomically (`.tmp` + rename) with one compact
    /// JSON row per line, trailing newline — exactly the Python shape.
    pub fn save(&self) -> std::io::Result<()> {
        let tmp = self.path.with_extension("tmp");
        let lines: Vec<String> = self.rows.iter().map(|(_, r)| r.compact()).collect();
        std::fs::write(&tmp, format!("{}\n", lines.join("\n")))?;
        std::fs::rename(&tmp, &self.path)
    }
}

/// TTL for the evidence memo, mirroring `_MEMO_TTL = 300` in bin/todos.py.
pub const MEMO_TTL: u64 = 300;

/// `state_dir()/evidence_memo.json`: `"repo\x1fevidence"` -> `{"verdict", "t"}`.
/// Disposable cache: a missing, unreadable, or malformed file starts empty.
/// Entries are an insertion-ordered pair list (Python dicts iterate in
/// insertion order; serde_json's default Map would sort keys on save).
pub struct EvidenceMemo {
    pub entries: Vec<(String, Value)>,
}

impl EvidenceMemo {
    pub fn load() -> Self {
        let entries = match std::fs::read_to_string(state_dir().join("evidence_memo.json")) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(Value::Object(m)) => m.into_iter().collect(),
                _ => Vec::new(),
            },
            Err(_) => Vec::new(),
        };
        Self { entries }
    }

    /// Memo hit only when `t` is numeric, `verdict` is one of the three
    /// verdicts, and `now - t < MEMO_TTL`. Malformed entries are misses.
    pub fn get(&self, key: &str, now: u64) -> Option<&str> {
        let hit = self.entries.iter().find(|(k, _)| k == key)?;
        let t = hit.1.get("t")?.as_f64()?;
        let verdict = hit.1.get("verdict")?.as_str()?;
        if !matches!(verdict, "landed" | "outstanding" | "unknown") {
            return None;
        }
        if now as f64 - t < MEMO_TTL as f64 {
            Some(verdict)
        } else {
            None
        }
    }

    /// `UNKNOWN` is never cached: it means "could not determine", not an
    /// answer.
    pub fn put(&mut self, key: &str, verdict: &str, now: u64) {
        if verdict == "unknown" {
            return;
        }
        let mut e = serde_json::Map::new();
        e.insert("verdict".to_string(), Value::String(verdict.to_string()));
        e.insert("t".to_string(), Value::from(now as f64));
        let entry = Value::Object(e);
        if let Some(slot) = self.entries.iter_mut().find(|(k, _)| k == key) {
            slot.1 = entry;
        } else {
            self.entries.push((key.to_string(), entry));
        }
    }

    /// Best-effort: a failed write must never crash the CLI over a cache
    /// write. Each entry is emitted as `{"verdict": V, "t": T}` — verdict
    /// before t, the insertion order Python writes.
    pub fn save(&self) {
        let p = state_dir().join("evidence_memo.json");
        let mut out = String::from("{");
        for (i, (k, v)) in self.entries.iter().enumerate() {
            if i > 0 {
                out.push_str(", ");
            }
            out.push_str(&esc(k));
            out.push_str(": {\"verdict\": ");
            out.push_str(&esc(v
                .get("verdict")
                .and_then(|x| x.as_str())
                .unwrap_or("")));
            out.push_str(", \"t\": ");
            out.push_str(&v.get("t").unwrap_or(&Value::Null).to_string());
            out.push('}');
        }
        out.push('}');
        let _ = std::fs::write(&p, out);
    }
}

/// `state_dir()/pending.json`: `[]` when missing or blank, else the parsed
/// array (malformed JSON is a hard error, mirroring `json.loads`).
pub fn load_pending() -> Vec<Value> {
    let p = state_dir().join("pending.json");
    let text = match std::fs::read_to_string(&p) {
        Ok(t) => t,
        Err(_) => return Vec::new(),
    };
    if text.trim().is_empty() {
        return Vec::new();
    }
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("pending.json malformed: {e}"))
}

/// Atomic (`.tmp` + rename) write of the queue in `json.dumps(items, indent=1)`
/// format, with row keys in canonical ferry order.
pub fn save_pending(items: &[Value]) -> std::io::Result<()> {
    let p = state_dir().join("pending.json");
    let tmp = p.with_extension("tmp");
    std::fs::write(&tmp, dump_indent1(&Value::Array(items.to_vec())))?;
    std::fs::rename(&tmp, p)
}

/// `state_dir()/imports.ndjson`: `{"path": p, "hash": v}` per line. Returns
/// the path -> hash map, skipping blank and unparsable lines.
pub fn load_imports() -> HashMap<String, String> {
    let mut known = HashMap::new();
    let p = state_dir().join("imports.ndjson");
    let text = match std::fs::read_to_string(&p) {
        Ok(t) => t,
        Err(_) => return known,
    };
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(v) = serde_json::from_str::<Value>(line) {
            let path = v.get("path").and_then(|x| x.as_str());
            let hash = v.get("hash").and_then(|x| x.as_str());
            if let (Some(path), Some(hash)) = (path, hash) {
                known.insert(path.to_string(), hash.to_string());
            }
        }
    }
    known
}

/// Atomically (`.tmp` + rename) rewrites the whole imports file, appending
/// any new entries carried by the map, one `{"path": ..., "hash": ...}`
/// line each (path before hash, the Python insertion order).
pub fn save_imports(known: &HashMap<String, String>) -> std::io::Result<()> {
    let p = state_dir().join("imports.ndjson");
    let tmp = p.with_extension("tmp");
    let mut out = String::new();
    for (path, hash) in known {
        out.push_str("{\"path\": ");
        out.push_str(&esc(path));
        out.push_str(", \"hash\": ");
        out.push_str(&esc(hash));
        out.push_str("}\n");
    }
    std::fs::write(&tmp, out)?;
    std::fs::rename(&tmp, p)
}

/// Writes `state_dir()/last_wake` as a single line: `now` + newline.
pub fn wake(now: &str) -> std::io::Result<()> {
    std::fs::write(state_dir().join("last_wake"), format!("{now}\n"))
}

/// Reads `state_dir()/last_wake`, stripped; `""` when missing.
pub fn last_wake() -> String {
    match std::fs::read_to_string(state_dir().join("last_wake")) {
        Ok(t) => t.trim().to_string(),
        Err(_) => String::new(),
    }
}

/// Attempt count of a queue row, robust to junk from hand-edited or
/// pre-fold files: a null/garbage `seen` reads as one attempt, never a
/// crash and never a silent zero.
pub fn seen_of(row: &Value) -> u64 {
    match row.get("seen").unwrap_or(&Value::from(1)).as_u64() {
        Some(n) if n > 0 => n,
        _ => 1,
    }
}
