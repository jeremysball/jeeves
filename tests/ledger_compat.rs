//! Compatibility tests for the ledger core against the Python ground
//! truth: tests/fixtures/normalize_vectors.txt pins `normalize` and
//! `line_hash` byte-for-byte against bin/jeeves_lib.py:155-174, and the
//! behavioral tests pin parse/render/find_match semantics against
//! bin/todos.py:56-107.

use jeeves::digest::ledger::{line_hash, normalize, Ledger, SKELETON};

/// Parses the vectors file: comment lines (starting with `#`) are
/// skipped, records are separated by a line containing only `---`, and
/// each record is (python-repr line, normalized, sha256). The repr is
/// unquoted (the file stores `'...'` with `\t`-style escapes).
fn vectors() -> Vec<(String, String, String)> {
    let text = std::fs::read_to_string("tests/fixtures/normalize_vectors.txt").unwrap();
    let mut out = Vec::new();
    let mut record: Vec<String> = Vec::new();
    for line in text.lines() {
        if line.trim() == "---" {
            if record.len() == 3 {
                out.push((
                    unquote_repr(&record[0]),
                    record[1].clone(),
                    record[2].clone(),
                ));
            }
            record.clear();
        } else if !line.starts_with('#') {
            record.push(line.to_owned());
        }
    }
    if record.len() == 3 {
        out.push((
            unquote_repr(&record[0]),
            record[1].clone(),
            record[2].clone(),
        ));
    }
    out
}

/// Decodes a python single-quoted repr: strips the surrounding quotes
/// and expands the escapes the generator emits (`\t`, `\n`, `\\`, `\'`,
/// `\uXXXX`).
fn unquote_repr(repr: &str) -> String {
    let inner = repr
        .strip_prefix('\'')
        .and_then(|s| s.strip_suffix('\''))
        .unwrap_or(repr);
    let mut out = String::with_capacity(inner.len());
    let mut chars = inner.chars();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some('t') => out.push('\t'),
            Some('n') => out.push('\n'),
            Some('\\') => out.push('\\'),
            Some('\'') => out.push('\''),
            Some('"') => out.push('"'),
            Some('u') => {
                let hex: String = chars.by_ref().take(4).collect();
                let cp = u32::from_str_radix(&hex, 16).unwrap();
                out.push(char::from_u32(cp).unwrap());
            }
            Some(other) => out.push(other),
            None => {}
        }
    }
    out
}

#[test]
fn vectors_parse_ten_records() {
    assert_eq!(vectors().len(), 10);
}

#[test]
fn normalize_matches_python_on_all_vectors() {
    for (repr, expected, _) in vectors() {
        assert_eq!(normalize(&repr), expected, "normalize({repr:?})");
    }
}

#[test]
fn line_hash_matches_python_on_all_vectors() {
    for (repr, _, expected) in vectors() {
        assert_eq!(line_hash(&repr), expected, "line_hash({repr:?})");
    }
}

#[test]
fn vectors_one_and_two_normalize_equal() {
    let v = vectors();
    assert_eq!(normalize(&v[0].0), normalize(&v[1].0));
    assert_eq!(line_hash(&v[0].0), line_hash(&v[1].0));
}

#[test]
fn skeleton_is_byte_identical_to_python() {
    assert_eq!(
        SKELETON,
        "# jeeves todo ledger\n\n## open\n\n## done\n\n## dismissed\n"
    );
}

#[test]
fn parse_render_round_trip_preserves_lines() {
    let text = "# jeeves todo ledger\n\n## open\n- [ ] fix the scan (jeeves: open, session abc, 2026-08-30)\n- [ ] other task\n\n## done\n- [x] done thing (jeeves: evidence, 2026-08-31)\n\n## dismissed\n- [ ] old thing (dismissed 2026-08-30)\n";
    let ledger = Ledger::parse(text);
    assert_eq!(ledger.open.len(), 2);
    assert_eq!(ledger.done.len(), 1);
    assert_eq!(ledger.dismissed.len(), 1);
    assert_eq!(ledger.render(), text);
}

#[test]
fn find_match_returns_none_when_ambiguous() {
    let ledger = Ledger {
        open: vec![
            "- [ ] fix the scan (jeeves: open, session abc, 2026-08-30)".to_owned(),
            "- [ ] FIX the SCAN (jeeves: open, session abc, 2026-08-30) (dismissed 2026-08-31)"
                .to_owned(),
        ],
        done: vec![],
        dismissed: vec![],
    };
    assert_eq!(ledger.find_match("fix the scan", Some("open")), None);
}

#[test]
fn find_match_returns_index_for_unique_match() {
    let ledger = Ledger {
        open: vec![
            "- [ ] fix the scan (jeeves: open, session abc, 2026-08-30)".to_owned(),
            "- [ ] other task".to_owned(),
        ],
        done: vec![],
        dismissed: vec![],
    };
    assert_eq!(ledger.find_match("fix the scan", Some("open")), Some(0));
    assert_eq!(ledger.find_match("other task", Some("open")), Some(1));
    assert_eq!(ledger.find_match("no such line", Some("open")), None);
}

#[test]
fn find_match_respects_only_filter() {
    let ledger = Ledger {
        open: vec!["- [ ] open task".to_owned()],
        done: vec!["- [x] done task".to_owned()],
        dismissed: vec![],
    };
    assert_eq!(ledger.find_match("done task", Some("open")), None);
    assert_eq!(ledger.find_match("done task", Some("done")), Some(0));
    assert_eq!(ledger.find_match("done task", None), Some(0));
}
