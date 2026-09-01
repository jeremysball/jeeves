//! TOON string escaping, mirroring ref/scan-active.sh:97-100.
//!
//! Quote the field, escape embedded backslashes first (so the quotes we add
//! next are not themselves backslash-escaped by the doubling), then escape
//! embedded double quotes. Order matters and is pinned by the script:
//! `s="${1//\\/\\\\}"` then `"${s//\"/\\\"}"`.

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
    use super::toon_str;

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
}
