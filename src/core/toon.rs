//! TOON string escaping, mirroring ref/scan-active.sh:97-100.
//!
//! Quote the field, escape embedded backslashes first (so the quotes we add
//! next are not themselves backslash-escaped by the doubling), then escape
//! embedded double quotes. Order matters and is pinned by the script:
//! `s="${1//\\/\\\\}"` then `"${s//\"/\\\"}"`.

/// A table cell: `Str` is quoted via `toon_str`, `Bare` is emitted verbatim
/// (integers, clean states, ...), mirroring scan-active.sh's mixed usage such
/// as `echo "  $(toon_str "$repo"),$(toon_str "${R_BRANCH[$repo]}"),${R_TREE[$repo]},${R_ALERTS[$repo]}"`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Cell {
    Str(String),
    Bare(String),
}

/// Renders a TOON table in the exact shape scan-active.sh prints: a header
/// line `name[N]{col1,col2}:` followed by one 2-space-indented row per record,
/// cells joined with commas. `N` is the number of rows.
pub fn toon_table(name: &str, header: &[&str], rows: &[Vec<Cell>]) -> String {
    let mut out = String::new();
    out.push_str(&format!("{name}[{}]{{{}}}:", rows.len(), header.join(",")));
    for row in rows {
        out.push('\n');
        out.push_str("  ");
        let cells: Vec<String> = row
            .iter()
            .map(|c| match c {
                Cell::Str(s) => toon_str(s),
                Cell::Bare(s) => s.clone(),
            })
            .collect();
        out.push_str(&cells.join(","));
    }
    out
}

/// Quotes `s` for TOON output: wraps in double quotes, doubles every existing
/// backslash, then backslash-escapes embedded double quotes.
pub fn toon_str(s: &str) -> String {
    let mut escaped = String::with_capacity(s.len() + 2);
    for c in s.chars() {
        match c {
            '\\' => escaped.push_str("\\\\"),
            '"' => escaped.push_str("\\\""),
            other => escaped.push(other),
        }
    }
    format!("\"{escaped}\"")
}

#[cfg(test)]
mod tests {
    use super::{toon_str, toon_table, Cell};

    #[test]
    fn empty_string() {
        assert_eq!(toon_str(""), "\"\"");
    }

    #[test]
    fn plain_string() {
        assert_eq!(toon_str("hello"), "\"hello\"");
    }

    #[test]
    fn path_with_spaces() {
        assert_eq!(toon_str("/workspace/my repo"), "\"/workspace/my repo\"");
    }

    #[test]
    fn embedded_quote() {
        assert_eq!(toon_str("say \"hi\""), "\"say \\\"hi\\\"\"");
    }

    #[test]
    fn embedded_backslash() {
        assert_eq!(toon_str(r"a\b"), "\"a\\\\b\"");
    }

    #[test]
    fn quote_after_backslash_is_escaped_not_doubled() {
        // The backslash is doubled first, then the quote gets a new escape:
        //  \" -> \\\"
        assert_eq!(toon_str("\\\""), "\"\\\\\\\"\"");
    }

    #[test]
    fn trailing_backslash() {
        assert_eq!(toon_str("path\\"), "\"path\\\\\"");
    }

    #[test]
    fn matches_reference_implementation_shape() {
        let cases = [
            "plain",
            "with space",
            "quote\"inside",
            "back\\slash",
            "mix \\\"both",
            "üñïçödé",
            "tab\there",
        ];
        for case in cases {
            let got = toon_str(case);
            assert!(got.starts_with('"'));
            assert!(got.ends_with('"'));
            assert_eq!(
                got.len(),
                case.len() + 2 + case.matches('"').count() + case.matches('\\').count()
            );
        }
    }

    #[test]
    fn table_header_shape() {
        let out = toon_table("repos", &["path", "branch", "tree", "alerts"], &[]);
        assert_eq!(out, "repos[0]{path,branch,tree,alerts}:");
    }

    #[test]
    fn table_mixed_quoted_and_bare_cells() {
        // Mirrors scan-active.sh:439: `echo "  $(toon_str "$repo"),$(toon_str "${R_BRANCH[$repo]}"),${R_TREE[$repo]},${R_ALERTS[$repo]}"`
        let rows = vec![vec![
            Cell::Str("/workspace/my repo".to_string()),
            Cell::Str("main".to_string()),
            Cell::Bare("3".to_string()),
            Cell::Bare("clean".to_string()),
        ]];
        let out = toon_table("repos", &["path", "branch", "tree", "alerts"], &rows);
        assert_eq!(
            out,
            "repos[1]{path,branch,tree,alerts}:\n  \"/workspace/my repo\",\"main\",3,clean"
        );
    }

    #[test]
    fn table_escapes_quotes_and_backslashes_in_string_cells() {
        let rows = vec![vec![
            Cell::Str("say \"hi\"".to_string()),
            Cell::Str(r"a\b".to_string()),
        ]];
        let out = toon_table("branches", &["name", "detail"], &rows);
        assert_eq!(
            out,
            "branches[1]{name,detail}:\n  \"say \\\"hi\\\"\",\"a\\\\b\""
        );
    }

    #[test]
    fn table_multiple_rows_each_indented() {
        let rows = vec![
            vec![Cell::Str("one".to_string()), Cell::Bare("1".to_string())],
            vec![Cell::Str("two".to_string()), Cell::Bare("2".to_string())],
        ];
        let out = toon_table("commits", &["sha", "n"], &rows);
        assert_eq!(out, "commits[2]{sha,n}:\n  \"one\",1\n  \"two\",2");
    }

    #[test]
    fn table_empty_string_cell_stays_quoted() {
        let rows = vec![vec![Cell::Str(String::new()), Cell::Bare("0".to_string())]];
        let out = toon_table("repos", &["path", "n"], &rows);
        assert_eq!(out, "repos[1]{path,n}:\n  \"\",0");
    }
}
