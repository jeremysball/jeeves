//! Todo ledger core: normalization, line hashing, parsing, and matching.
//!
//! Port of bin/jeeves_lib.py:152-174 (`normalize`/`line_hash`) and
//! bin/todos.py:56-107 (SKELETON, `parse_ledger`, `render`, `_write`,
//! `find_match`). The normalize vectors in
//! tests/fixtures/normalize_vectors.txt are the ground truth; the
//! lstrip-character-set quirk and the stacked-provenance loop are
//! replicated exactly (see `normalize`).
//!
//! Deliberate deviations from the Python original:
//! - `find_match` returns `Option<usize>` (the line index within the
//!   section) instead of a `(section, index)` tuple; the section is
//!   implied by the caller's `only` filter. Ambiguity (more than one
//!   match) returns `None`, mirroring the Python `AmbiguousMatch` raise.
//! - `Ledger::write` takes the target path explicitly instead of
//!   resolving `data_dir()/todo.md`; the caller owns path resolution.
//! - The provenance regex is hand-rolled (no `regex` dependency): the
//!   Python pattern `\s*\(jeeves:[^)]*\)\s*$|\s*\(dismissed[^)]*\)\s*$`
//!   is matched by scanning from the end of the line, which is
//!   equivalent for `$`-anchored alternatives (see `strip_provenance`).
//! - Python's `str.casefold` is full case folding; the
//!   `unicode-case-mapping` crate only exposes simple folding, so the
//!   full-fold exceptions (ß→ss, İ→i̇, ẛ→ṡ, ...) are layered on top from
//!   Unicode 16.0.0 CaseFolding.txt, verified byte-identical to Python
//!   for every entry.

use std::fs;
use std::io::Write;
use std::path::Path;

use sha2::{Digest, Sha256};
use unicode_normalization::UnicodeNormalization;

/// Byte-identical to bin/todos.py:56.
pub const SKELETON: &str = "# jeeves todo ledger\n\n## open\n\n## done\n\n## dismissed\n";

/// Section names in render order, mirroring bin/todos.py:57.
pub const SECTIONS: [&str; 3] = ["open", "done", "dismissed"];

/// The Python provenance regex, `\s*\(jeeves:[^)]*\)\s*$` or
/// `\s*\(dismissed[^)]*\)\s*$`, applied in a while-changed loop so that
/// stacked tags are all removed (bin/jeeves_lib.py:152-167).
fn strip_provenance(s: &str) -> String {
    let mut out = s.to_owned();
    loop {
        let stripped = strip_one_provenance(&out);
        if stripped == out {
            return out;
        }
        out = stripped;
    }
}

/// One left-to-right pass of the Python `re.sub` over the two
/// `$`-anchored alternatives. Because both alternatives are anchored at
/// the end of the string, a single pass removes at most the outermost
/// tag; scanning from the end reproduces the sub() semantics exactly.
fn strip_one_provenance(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut i = bytes.len();
    while i > 0 && (bytes[i - 1] == b' ' || bytes[i - 1] == b'\t') {
        i -= 1;
    }
    if i == 0 || bytes[i - 1] != b')' {
        return s.to_owned();
    }
    let mut j = i - 1;
    while j > 0 {
        j -= 1;
        if bytes[j] == b'(' {
            break;
        }
    }
    if j == 0 {
        return s.to_owned();
    }
    let open = j;
    let tag = &s[open..i];
    if tag.starts_with("(jeeves:") || tag.starts_with("(dismissed") {
        if tag[1..].find(')') != Some(tag.len() - 2) {
            return s.to_owned();
        }
        let mut ws = open;
        while ws > 0 && (bytes[ws - 1] == b' ' || bytes[ws - 1] == b'\t') {
            ws -= 1;
        }
        return s[..ws].to_owned();
    }
    s.to_owned()
}

/// Replicates bin/jeeves_lib.py:155-170 exactly.
///
/// The two `lstrip` calls are CHARACTER-SET strips, not prefix strips:
/// `"- [x] ".lstrip` removes any leading run of characters from the set
/// `{'-', ' ', '[', 'x', ']'}`, and the second removes any leading run
/// from `{'-', ' ', '[', ']'}` (the `x` is absent). A prefix-based
/// implementation diverges on inputs like `- [xxx] ...`; the
/// character-set semantics are what the vectors pin.
pub fn normalize(s: &str) -> String {
    let s = strip_provenance(s);
    let s = s.trim_start_matches(|c| "- [x] ".contains(c));
    let s = s.trim_start_matches(|c| "- [ ] ".contains(c));
    let s = s.trim();
    let s = s.split_whitespace().collect::<Vec<_>>().join(" ");
    let s: String = s.nfkc().collect();
    full_casefold(&s)
}

/// sha256 hex of the normalized bytes (bin/jeeves_lib.py:173-174).
pub fn line_hash(s: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(normalize(s).as_bytes());
    hex(&hasher.finalize())
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

/// Full case folding, matching Python's `str.casefold`.
///
/// `unicode_case_mapping::case_folded` implements *simple* folding
/// (single codepoint → single codepoint); Python's casefold is *full*
/// folding, where 104 codepoints expand to multi-codepoint sequences
/// (ß→ss, İ→i̇, ẛ→ṡ, ...). The exceptions below come from Unicode
/// 16.0.0 CaseFolding.txt status F, verified byte-identical to Python
/// 3.13's `str.casefold` for every entry.
fn full_casefold(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\u{00DF}' => out.push_str("ss"),
            '\u{0130}' => out.push_str("i\u{0307}"),
            '\u{0149}' => out.push_str("\u{02BC}n"),
            '\u{01F0}' => out.push_str("j\u{030C}"),
            '\u{0390}' => out.push_str("\u{03B9}\u{0308}\u{0301}"),
            '\u{03B0}' => out.push_str("\u{03C5}\u{0308}\u{0301}"),
            '\u{03C2}' => out.push('\u{03C3}'),
            '\u{0587}' => out.push_str("\u{0565}\u{0582}"),
            '\u{1E96}' => out.push_str("h\u{0331}"),
            '\u{1E97}' => out.push_str("t\u{0308}"),
            '\u{1E98}' => out.push_str("w\u{030A}"),
            '\u{1E99}' => out.push_str("y\u{030A}"),
            '\u{1E9A}' => out.push_str("a\u{02BE}"),
            '\u{1E9B}' => out.push('\u{1E61}'),
            '\u{1F50}' => out.push_str("\u{03C5}\u{0313}"),
            '\u{1F52}' => out.push_str("\u{03C5}\u{0313}\u{0300}"),
            '\u{1F54}' => out.push_str("\u{03C5}\u{0313}\u{0301}"),
            '\u{1F56}' => out.push_str("\u{03C5}\u{0313}\u{0342}"),
            '\u{1F80}' => out.push_str("\u{1F00}\u{03B9}"),
            '\u{1F81}' => out.push_str("\u{1F01}\u{03B9}"),
            '\u{1F82}' => out.push_str("\u{1F02}\u{03B9}"),
            '\u{1F83}' => out.push_str("\u{1F03}\u{03B9}"),
            '\u{1F84}' => out.push_str("\u{1F04}\u{03B9}"),
            '\u{1F85}' => out.push_str("\u{1F05}\u{03B9}"),
            '\u{1F86}' => out.push_str("\u{1F06}\u{03B9}"),
            '\u{1F87}' => out.push_str("\u{1F07}\u{03B9}"),
            '\u{1F88}' => out.push_str("\u{1F00}\u{03B9}"),
            '\u{1F89}' => out.push_str("\u{1F01}\u{03B9}"),
            '\u{1F8A}' => out.push_str("\u{1F02}\u{03B9}"),
            '\u{1F8B}' => out.push_str("\u{1F03}\u{03B9}"),
            '\u{1F8C}' => out.push_str("\u{1F04}\u{03B9}"),
            '\u{1F8D}' => out.push_str("\u{1F05}\u{03B9}"),
            '\u{1F8E}' => out.push_str("\u{1F06}\u{03B9}"),
            '\u{1F8F}' => out.push_str("\u{1F07}\u{03B9}"),
            '\u{1F90}' => out.push_str("\u{1F10}\u{03B9}"),
            '\u{1F91}' => out.push_str("\u{1F11}\u{03B9}"),
            '\u{1F92}' => out.push_str("\u{1F12}\u{03B9}"),
            '\u{1F93}' => out.push_str("\u{1F13}\u{03B9}"),
            '\u{1F94}' => out.push_str("\u{1F14}\u{03B9}"),
            '\u{1F95}' => out.push_str("\u{1F15}\u{03B9}"),
            '\u{1F96}' => out.push_str("\u{1F16}\u{03B9}"),
            '\u{1F97}' => out.push_str("\u{1F17}\u{03B9}"),
            '\u{1F98}' => out.push_str("\u{1F10}\u{03B9}"),
            '\u{1F99}' => out.push_str("\u{1F11}\u{03B9}"),
            '\u{1F9A}' => out.push_str("\u{1F12}\u{03B9}"),
            '\u{1F9B}' => out.push_str("\u{1F13}\u{03B9}"),
            '\u{1F9C}' => out.push_str("\u{1F14}\u{03B9}"),
            '\u{1F9D}' => out.push_str("\u{1F15}\u{03B9}"),
            '\u{1F9E}' => out.push_str("\u{1F16}\u{03B9}"),
            '\u{1F9F}' => out.push_str("\u{1F17}\u{03B9}"),
            '\u{1FA0}' => out.push_str("\u{1F20}\u{03B9}"),
            '\u{1FA1}' => out.push_str("\u{1F21}\u{03B9}"),
            '\u{1FA2}' => out.push_str("\u{1F22}\u{03B9}"),
            '\u{1FA3}' => out.push_str("\u{1F23}\u{03B9}"),
            '\u{1FA4}' => out.push_str("\u{1F24}\u{03B9}"),
            '\u{1FA5}' => out.push_str("\u{1F25}\u{03B9}"),
            '\u{1FA6}' => out.push_str("\u{1F26}\u{03B9}"),
            '\u{1FA7}' => out.push_str("\u{1F27}\u{03B9}"),
            '\u{1FA8}' => out.push_str("\u{1F20}\u{03B9}"),
            '\u{1FA9}' => out.push_str("\u{1F21}\u{03B9}"),
            '\u{1FAA}' => out.push_str("\u{1F22}\u{03B9}"),
            '\u{1FAB}' => out.push_str("\u{1F23}\u{03B9}"),
            '\u{1FAC}' => out.push_str("\u{1F24}\u{03B9}"),
            '\u{1FAD}' => out.push_str("\u{1F25}\u{03B9}"),
            '\u{1FAE}' => out.push_str("\u{1F26}\u{03B9}"),
            '\u{1FAF}' => out.push_str("\u{1F27}\u{03B9}"),
            '\u{1FB2}' => out.push_str("\u{1F70}\u{03B9}"),
            '\u{1FB3}' => out.push_str("\u{03B1}\u{03B9}"),
            '\u{1FB4}' => out.push_str("\u{03AC}\u{03B9}"),
            '\u{1FB6}' => out.push_str("\u{03B1}\u{0342}"),
            '\u{1FB7}' => out.push_str("\u{03B1}\u{0342}\u{03B9}"),
            '\u{1FBC}' => out.push_str("\u{03B1}\u{03B9}"),
            '\u{1FC2}' => out.push_str("\u{1F74}\u{03B9}"),
            '\u{1FC3}' => out.push_str("\u{03B7}\u{03B9}"),
            '\u{1FC4}' => out.push_str("\u{03AE}\u{03B9}"),
            '\u{1FC6}' => out.push_str("\u{03B7}\u{0342}"),
            '\u{1FC7}' => out.push_str("\u{03B7}\u{0342}\u{03B9}"),
            '\u{1FCC}' => out.push_str("\u{03B7}\u{03B9}"),
            '\u{1FD2}' => out.push_str("\u{03B9}\u{0308}\u{0300}"),
            '\u{1FD3}' => out.push_str("\u{03B9}\u{0308}\u{0301}"),
            '\u{1FD6}' => out.push_str("\u{03B9}\u{0342}"),
            '\u{1FD7}' => out.push_str("\u{03B9}\u{0308}\u{0342}"),
            '\u{1FE2}' => out.push_str("\u{03C5}\u{0308}\u{0300}"),
            '\u{1FE3}' => out.push_str("\u{03C5}\u{0308}\u{0301}"),
            '\u{1FE4}' => out.push_str("\u{03C1}\u{0313}"),
            '\u{1FE6}' => out.push_str("\u{03C5}\u{0342}"),
            '\u{1FE7}' => out.push_str("\u{03C5}\u{0308}\u{0342}"),
            '\u{1FF2}' => out.push_str("\u{1F7C}\u{03B9}"),
            '\u{1FF3}' => out.push_str("\u{03C9}\u{03B9}"),
            '\u{1FF4}' => out.push_str("\u{03CE}\u{03B9}"),
            '\u{1FF6}' => out.push_str("\u{03C9}\u{0342}"),
            '\u{1FF7}' => out.push_str("\u{03C9}\u{0342}\u{03B9}"),
            '\u{1FFC}' => out.push_str("\u{03C9}\u{03B9}"),
            '\u{212A}' => out.push('k'),
            '\u{212B}' => out.push('\u{00E5}'),
            '\u{2ADC}' => out.push_str("\u{2ADD}\u{0338}"),
            '\u{AB70}' => out.push('\u{13A0}'),
            '\u{AB71}' => out.push('\u{13A1}'),
            '\u{AB72}' => out.push('\u{13A2}'),
            '\u{AB73}' => out.push('\u{13A3}'),
            '\u{AB74}' => out.push('\u{13A4}'),
            '\u{AB75}' => out.push('\u{13A5}'),
            '\u{AB76}' => out.push('\u{13A6}'),
            '\u{AB77}' => out.push('\u{13A7}'),
            '\u{AB78}' => out.push('\u{13A8}'),
            '\u{AB79}' => out.push('\u{13A9}'),
            '\u{AB7A}' => out.push('\u{13AA}'),
            '\u{AB7B}' => out.push('\u{13AB}'),
            '\u{AB7C}' => out.push('\u{13AC}'),
            '\u{AB7D}' => out.push('\u{13AD}'),
            '\u{AB7E}' => out.push('\u{13AE}'),
            '\u{AB7F}' => out.push('\u{13AF}'),
            '\u{AB80}' => out.push('\u{13B0}'),
            '\u{AB81}' => out.push('\u{13B1}'),
            '\u{AB82}' => out.push('\u{13B2}'),
            '\u{AB83}' => out.push('\u{13B3}'),
            '\u{AB84}' => out.push('\u{13B4}'),
            '\u{AB85}' => out.push('\u{13B5}'),
            '\u{AB86}' => out.push('\u{13B6}'),
            '\u{AB87}' => out.push('\u{13B7}'),
            '\u{AB88}' => out.push('\u{13B8}'),
            '\u{AB89}' => out.push('\u{13B9}'),
            '\u{AB8A}' => out.push('\u{13BA}'),
            '\u{AB8B}' => out.push('\u{13BB}'),
            '\u{AB8C}' => out.push('\u{13BC}'),
            '\u{AB8D}' => out.push('\u{13BD}'),
            '\u{AB8E}' => out.push('\u{13BE}'),
            '\u{AB8F}' => out.push('\u{13BF}'),
            '\u{AB90}' => out.push('\u{13C0}'),
            '\u{AB91}' => out.push('\u{13C1}'),
            '\u{AB92}' => out.push('\u{13C2}'),
            '\u{AB93}' => out.push('\u{13C3}'),
            '\u{AB94}' => out.push('\u{13C4}'),
            '\u{AB95}' => out.push('\u{13C5}'),
            '\u{AB96}' => out.push('\u{13C6}'),
            '\u{AB97}' => out.push('\u{13C7}'),
            '\u{AB98}' => out.push('\u{13C8}'),
            '\u{AB99}' => out.push('\u{13C9}'),
            '\u{AB9A}' => out.push('\u{13CA}'),
            '\u{AB9B}' => out.push('\u{13CB}'),
            '\u{AB9C}' => out.push('\u{13CC}'),
            '\u{AB9D}' => out.push('\u{13CD}'),
            '\u{AB9E}' => out.push('\u{13CE}'),
            '\u{AB9F}' => out.push('\u{13CF}'),
            '\u{ABA0}' => out.push('\u{13D0}'),
            '\u{ABA1}' => out.push('\u{13D1}'),
            '\u{ABA2}' => out.push('\u{13D2}'),
            '\u{ABA3}' => out.push('\u{13D3}'),
            '\u{ABA4}' => out.push('\u{13D4}'),
            '\u{ABA5}' => out.push('\u{13D5}'),
            '\u{ABA6}' => out.push('\u{13D6}'),
            '\u{ABA7}' => out.push('\u{13D7}'),
            '\u{ABA8}' => out.push('\u{13D8}'),
            '\u{ABA9}' => out.push('\u{13D9}'),
            '\u{ABAA}' => out.push('\u{13DA}'),
            '\u{ABAB}' => out.push('\u{13DB}'),
            '\u{ABAC}' => out.push('\u{13DC}'),
            '\u{ABAD}' => out.push('\u{13DD}'),
            '\u{ABAE}' => out.push('\u{13DE}'),
            '\u{ABAF}' => out.push('\u{13DF}'),
            '\u{ABB0}' => out.push('\u{13E0}'),
            '\u{ABB1}' => out.push('\u{13E1}'),
            '\u{ABB2}' => out.push('\u{13E2}'),
            '\u{ABB3}' => out.push('\u{13E3}'),
            '\u{ABB4}' => out.push('\u{13E4}'),
            '\u{ABB5}' => out.push('\u{13E5}'),
            '\u{ABB6}' => out.push('\u{13E6}'),
            '\u{ABB7}' => out.push('\u{13E7}'),
            '\u{ABB8}' => out.push('\u{13E8}'),
            '\u{ABB9}' => out.push('\u{13E9}'),
            '\u{ABBA}' => out.push('\u{13EA}'),
            '\u{ABBB}' => out.push('\u{13EB}'),
            '\u{ABBC}' => out.push('\u{13EC}'),
            '\u{ABBD}' => out.push('\u{13ED}'),
            '\u{ABBE}' => out.push('\u{13EE}'),
            '\u{ABBF}' => out.push('\u{13EF}'),
            '\u{FB00}' => out.push_str("ff"),
            '\u{FB01}' => out.push_str("fi"),
            '\u{FB02}' => out.push_str("fl"),
            '\u{FB03}' => out.push_str("ffi"),
            '\u{FB04}' => out.push_str("ffl"),
            '\u{FB05}' => out.push_str("st"),
            '\u{FB06}' => out.push_str("st"),
            '\u{FB13}' => out.push_str("\u{0574}\u{0576}"),
            '\u{FB14}' => out.push_str("\u{0574}\u{0565}"),
            '\u{FB15}' => out.push_str("\u{0574}\u{056B}"),
            '\u{FB16}' => out.push_str("\u{057E}\u{0576}"),
            '\u{FB17}' => out.push_str("\u{0574}\u{056D}"),
            '\u{1D79}' => out.push('\u{2C66}'),
            '\u{1D7D}' => out.push('\u{2C6C}'),
            '\u{2C65}' => out.push('\u{023A}'),
            '\u{2C66}' => out.push('\u{023E}'),
            '\u{2C7E}' => out.push('\u{023F}'),
            '\u{2C7F}' => out.push('\u{0240}'),
            '\u{1E9E}' => out.push_str("ss"),
            _ => match unicode_case_mapping::case_folded(c) {
                Some(folded) => out.push(char::from_u32(folded.get()).unwrap()),
                None => out.push(c),
            },
        }
    }
    out
}

/// Parsed ledger: the three known sections, each a list of raw lines.
///
/// Mirrors bin/todos.py:71-80: a `## name` header switches the current
/// section (unknown names fall back to no section), and every non-blank
/// line under a known section is collected verbatim. Stray lines
/// outside any section are dropped, exactly like the Python original.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct Ledger {
    pub open: Vec<String>,
    pub done: Vec<String>,
    pub dismissed: Vec<String>,
}

impl Ledger {
    /// Parses ledger text (bin/todos.py:71-80).
    pub fn parse(text: &str) -> Ledger {
        let mut ledger = Ledger::default();
        let mut current: Option<usize> = None;
        for line in text.lines() {
            if let Some(name) = line.strip_prefix("## ") {
                current = SECTIONS.iter().position(|s| *s == name.trim());
            } else if let Some(idx) = current {
                if !line.trim().is_empty() {
                    ledger.section_mut(idx).push(line.to_owned());
                }
            }
        }
        ledger
    }

    /// Renders the ledger back to text (bin/todos.py:83-89).
    pub fn render(&self) -> String {
        let mut out = String::from("# jeeves todo ledger\n\n");
        for (i, name) in SECTIONS.iter().enumerate() {
            out.push_str("## ");
            out.push_str(name);
            out.push('\n');
            for line in self.section(i) {
                out.push_str(line);
                out.push('\n');
            }
            if i + 1 < SECTIONS.len() {
                out.push('\n');
            }
        }
        out
    }

    /// Atomic write: tmp file in the same directory, then rename
    /// (bin/todos.py:92-97).
    pub fn write(&self, path: &Path) -> std::io::Result<()> {
        let tmp = path.with_extension("tmp");
        {
            let mut f = fs::File::create(&tmp)?;
            f.write_all(self.render().as_bytes())?;
        }
        fs::rename(&tmp, path)
    }

    /// Creates the SKELETON at `path` if it does not exist, then parses
    /// it (bin/todos.py:64-68, 113).
    pub fn load_or_create(path: &Path) -> std::io::Result<Ledger> {
        if !path.exists() {
            fs::write(path, SKELETON)?;
        }
        Ok(Ledger::parse(&fs::read_to_string(path)?))
    }

    /// Unique match for `query` within `only` (or all sections when
    /// `None`), by normalize+line_hash equality. Returns the line index
    /// within the matched section; `None` when nothing matches or when
    /// more than one line matches (ambiguous), mirroring
    /// bin/todos.py:99-107.
    pub fn find_match(&self, query: &str, only: Option<&str>) -> Option<usize> {
        let nq = normalize(query);
        let mut hit: Option<usize> = None;
        for (i, name) in SECTIONS.iter().enumerate() {
            if only.is_some_and(|o| o != *name) {
                continue;
            }
            for (j, line) in self.section(i).iter().enumerate() {
                if normalize(line) == nq {
                    if hit.is_some() {
                        return None;
                    }
                    hit = Some(j);
                }
            }
        }
        hit
    }

    fn section(&self, i: usize) -> &[String] {
        match i {
            0 => &self.open,
            1 => &self.done,
            2 => &self.dismissed,
            _ => unreachable!(),
        }
    }

    fn section_mut(&mut self, i: usize) -> &mut Vec<String> {
        match i {
            0 => &mut self.open,
            1 => &mut self.done,
            2 => &mut self.dismissed,
            _ => unreachable!(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skeleton_round_trips() {
        let ledger = Ledger::parse(SKELETON);
        assert_eq!(ledger.render(), SKELETON);
    }

    #[test]
    fn parse_ignores_stray_lines_and_unknown_sections() {
        let text = "# jeeves todo ledger\nstray line\n\n## open\n- [ ] a\n\n## unknown\n- [ ] b\n\n## done\n- [x] c\n";
        let ledger = Ledger::parse(text);
        assert_eq!(ledger.open, vec!["- [ ] a"]);
        assert_eq!(ledger.done, vec!["- [x] c"]);
        assert!(ledger.dismissed.is_empty());
    }

    #[test]
    fn render_normalizes_section_layout() {
        let ledger = Ledger {
            open: vec!["- [ ] a".to_owned()],
            done: vec![],
            dismissed: vec!["- [ ] b (dismissed 2026-08-31)".to_owned()],
        };
        assert_eq!(
            ledger.render(),
            "# jeeves todo ledger\n\n## open\n- [ ] a\n\n## done\n\n## dismissed\n- [ ] b (dismissed 2026-08-31)\n"
        );
    }

    #[test]
    fn write_is_atomic_and_round_trips() {
        let dir = std::env::temp_dir().join(format!("jeeves-ledger-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("todo.md");
        let ledger = Ledger {
            open: vec!["- [ ] fix the scan (jeeves: open, session abc, 2026-08-30)".to_owned()],
            done: vec![],
            dismissed: vec![],
        };
        ledger.write(&path).unwrap();
        assert_eq!(
            Ledger::parse(&std::fs::read_to_string(&path).unwrap()),
            ledger
        );
        assert!(!path.with_extension("tmp").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_or_create_writes_skeleton() {
        let dir = std::env::temp_dir().join(format!("jeeves-ledger-skel-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("todo.md");
        let ledger = Ledger::load_or_create(&path).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), SKELETON);
        assert!(ledger.open.is_empty() && ledger.done.is_empty() && ledger.dismissed.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn find_match_unique_and_ambiguous() {
        let ledger = Ledger {
            open: vec![
                "- [ ] fix the scan (jeeves: open, session abc, 2026-08-30)".to_owned(),
                "- [ ] other task".to_owned(),
            ],
            done: vec![],
            dismissed: vec![],
        };
        assert_eq!(ledger.find_match("fix the scan", Some("open")), Some(0));
        assert_eq!(ledger.find_match("FIX THE SCAN", Some("open")), Some(0));
        assert_eq!(ledger.find_match("no such line", Some("open")), None);
        assert_eq!(ledger.find_match("other task", Some("done")), None);
    }

    #[test]
    fn stacked_provenance_is_ambiguous() {
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
    fn find_match_searches_all_sections_when_only_is_none() {
        let ledger = Ledger {
            open: vec![],
            done: vec!["- [x] done thing".to_owned()],
            dismissed: vec![],
        };
        assert_eq!(ledger.find_match("done thing", None), Some(0));
    }

    #[test]
    fn lstrip_is_character_set_not_prefix() {
        assert_eq!(normalize("- [xxx] thing"), "thing");
        assert_eq!(normalize("- [x] thing"), "thing");
        assert_eq!(normalize("- [ ] thing"), "thing");
        assert_eq!(normalize("-- [x] thing"), "thing");
    }

    #[test]
    fn stacked_provenance_strips_fully() {
        assert_eq!(
            normalize(
                "- [ ] fix the scan (jeeves: open, session abc, 2026-08-30) (dismissed 2026-08-31)"
            ),
            "fix the scan"
        );
    }

    #[test]
    fn nested_parens_provenance_is_kept() {
        assert_eq!(
            normalize("- [ ] trailing (jeeves: nested (parens) tag)"),
            "trailing (jeeves: nested (parens) tag)"
        );
    }

    #[test]
    fn full_casefold_matches_python() {
        assert_eq!(full_casefold("ßẞ"), "ssss");
        assert_eq!(full_casefold("İ"), "i\u{0307}");
        assert_eq!(full_casefold("ẛ"), "ṡ");
        assert_eq!(full_casefold("Σς"), "σσ");
        assert_eq!(full_casefold("K"), "k");
        assert_eq!(full_casefold("ﬃ"), "ffi");
    }
}
