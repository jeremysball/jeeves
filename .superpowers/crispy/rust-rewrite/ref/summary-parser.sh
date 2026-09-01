#!/usr/bin/env bash
# Sources, don't execute. summary_parse "$report" collapses a multi-repo audit
# report into one line per repo with per-bucket counts. Bucket headings are
# matched structurally (two-space indent + name + colon) so a branch description
# that mentions a bucket name can never set the current bucket.
#
# Usage: summary=$(summary_parse "$out")

summary_parse() {
  printf '%s\n' "$1" | awk '
    /^=== / { if (repo != "") emit(); repo=$2; s=t=a=h=cm=u=0; m=""; next }
    /^  safe-to-clean[^:]*:/           { m="s"; next }
    /^  needs-triage[^:]*:/            { m="t"; next }
    /^  archaeology[^:]*:/             { m="a"; next }
    /^  hands-off[^:]*:/               { m="h"; next }
    /^  likely-content-merged[^:]*:/   { m="cm"; next }
    /^  couldn.t determine merge state[^:]*:/ { m="u"; next }
    # Every other two-space heading (currently the dangling-worktree list) has
    # no counter, but it MUST still clear the current bucket: an unmatched
    # heading used to leave the previous bucket active, so its indented lines
    # were tallied under whichever bucket happened to precede it.
    /^  [^ ][^:]*:/              { m=""; next }
    /^  \(/                      { m="" }
    /^    / { if (m=="s") s++; else if (m=="t") t++; else if (m=="a") a++; else if (m=="h") h++; else if (m=="cm") cm++; else if (m=="u") u++ }
    function emit(   parts) {
      parts=""
      if (t) parts = parts sprintf("%d triage, ", t)
      if (cm) parts = parts sprintf("%d content-merged, ", cm)
      if (u) parts = parts sprintf("%d unknown-merge, ", u)
      if (a) parts = parts sprintf("%d archaeology, ", a)
      if (s) parts = parts sprintf("%d safe-to-clean, ", s)
      if (h) parts = parts sprintf("%d hands-off, ", h)
      if (parts != "") { sub(/, $/, "", parts); printf "  %s: %s\n", repo, parts }
    }
    END { if (repo != "") emit() }
  '
}
