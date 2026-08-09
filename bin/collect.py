#!/usr/bin/env python3
"""jeeves cron entrypoint: collect transcript deltas, extract via ferries,
synthesize the daily digest, apply todo mutations. Stdlib only."""
import argparse
import fcntl
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import jeeves_lib as jl
import todos as td

PROMPTS = Path(__file__).parent.parent / "prompts"


def offsets_load() -> dict:
    f = jl.state_dir() / "offsets.tsv"
    if not f.exists():
        return {}
    out = {}
    for line in f.read_text().splitlines():
        if line.strip():
            p, off, size = line.split("\t")
            out[p] = {"offset": int(off), "size": int(size)}
    return out


def offsets_save(offs: dict) -> None:
    f = jl.state_dir() / "offsets.tsv"
    tmp = f.with_suffix(".tmp")
    tmp.write_text("".join(f"{p}\t{v['offset']}\t{v['size']}\n" for p, v in offs.items()))
    tmp.replace(f)


def discover_sessions():
    return sorted(jl.projects_root().glob("*/*.jsonl"))


def session_id_of(path: Path) -> str:
    return path.stem


def group_slices(slices, batch_under, batch_max):
    solo, small = [], defaultdict(list)
    for s in slices:
        if len(s["entries"]) < batch_under:
            small[s["dir"]].append(s)
        else:
            solo.append([s])
    groups = list(solo)
    for dir_slices in small.values():
        for i in range(0, len(dir_slices), batch_max):
            groups.append(dir_slices[i:i + batch_max])
    return groups


def _gh_json(args, timeout=45):
    # Plain `gh` (not gh-axi) because the collector needs --jq/JSON-structured
    # output and gh-axi api renders YAML with no --jq flag — the sanctioned
    # fallback per CLAUDE.md's gh-axi-first rule.
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            return None
        return json.loads(p.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, json.JSONDecodeError):
        return None


GH_SIGNAL_REASONS = {"review-requested", "mention", "team-mention", "comment",
                     "assign", "author"}


def _github_repos() -> list:
    """Distinct GitHub owner/name pairs behind the project dirs, in order."""
    dirs_file = jl.state_dir() / "project-dirs.txt"
    if not dirs_file.exists():
        return []
    repos, seen = [], set()
    for d in dirs_file.read_text().splitlines():
        if not d.strip():
            continue
        try:
            top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True, timeout=10)
            url = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"],
                                 capture_output=True, text=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        if top.returncode != 0 or url.returncode != 0:
            continue
        m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)", url.stdout)
        if not m:
            continue
        full = f"{m.group(1)}/{m.group(2)}"
        if full not in seen:
            seen.add(full)
            repos.append(full)
        if len(repos) >= 12:
            break
    return repos


def gh_review() -> str:
    """Live GitHub obligations feed for the synthesis ferry: human-signal
    notifications, open PRs split mine/external, recent issues."""
    user = _gh_json(["api", "user"])
    if not isinstance(user, dict) or not user.get("login"):
        jl.log("gh review: auth failed or unavailable")
        return "(github unavailable: gh auth failed)"
    login = user["login"]
    out = [f"(authenticated as {login})"]

    notifs = _gh_json(["api", "notifications"])
    if isinstance(notifs, list):
        signal, noise = [], 0
        for n in notifs:
            if n.get("reason") in GH_SIGNAL_REASONS:
                num = str(n.get("subject", {}).get("url", "")).rsplit("/", 1)[-1]
                signal.append(f"- [{n['reason']}] {n['subject'].get('type')} "
                              f"{n['repository']['full_name']}#{num}: "
                              f"{n['subject'].get('title', '')[:80]}")
            else:
                noise += 1
        out.append("\nNEEDS A RESPONSE (unread notifications, human signals):")
        out.extend(signal[:25] or ["(none)"])
        if noise:
            out.append(f"(plus {noise} unread CI/state notifications suppressed)")
    else:
        out.append("\nNEEDS A RESPONSE:\n(notifications unavailable)")

    for full in _github_repos():
        pulls = _gh_json(["api", f"repos/{full}/pulls?state=open&per_page=30"])
        issues = None
        since = (jl.now_et() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
        issues = _gh_json(["api", f"repos/{full}/issues?state=open&since={since}&per_page=50"])
        pr_lines, iss_lines = [], []
        if isinstance(pulls, list):
            for pr in pulls[:15]:
                who = pr.get("user", {}).get("login", "?")
                tag = " (EXTERNAL)" if who != login else ""
                draft = " [draft]" if pr.get("draft") else ""
                pr_lines.append(f"- #{pr['number']} by {who}{tag}{draft} "
                                f"{str(pr.get('updated_at', ''))[:10]}: "
                                f"{pr.get('title', '')[:80]}")
        if isinstance(issues, list):
            real = [i for i in issues if "pull_request" not in i]
            for i in real[:12]:
                who = i.get("user", {}).get("login", "?")
                tag = " (EXTERNAL)" if who != login else ""
                iss_lines.append(f"- #{i['number']} by {who}{tag} "
                                 f"{str(i.get('created_at', ''))[:10]}, "
                                 f"{i.get('comments', 0)} comments: "
                                 f"{i.get('title', '')[:80]}")
        if pr_lines or iss_lines:
            out.append(f"\n{full}:")
            if pr_lines:
                out.append(" open PRs:")
                out.extend(pr_lines)
            if iss_lines:
                out.append(" recent issues (updated last 7 days):")
                out.extend(iss_lines)
    return "\n".join(out)


def tf_diff() -> str:
    """Diff taskferry list against last snapshot; return human notes."""
    try:
        p = subprocess.run(["taskferry", "list", "--all", "--limit", "40"],
                           capture_output=True, text=True, timeout=60)
        current = p.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        jl.log(f"tf diff skipped: {e}")
        return "(taskferry list unavailable)"
    f = jl.state_dir() / "tf-state.json"
    prev = json.loads(f.read_text()) if f.exists() else {"lines": []}
    cur_lines = {l.strip() for l in current.splitlines() if "oc_" in l}
    prev_lines = set(prev.get("lines", []))
    new = sorted(cur_lines - prev_lines)
    f.write_text(json.dumps({"lines": sorted(cur_lines)}))
    return "\n".join(new) if new else "(no new task-queue activity)"


def git_state() -> str:
    """Cross-repo branch/commit state via orient's scan-active.sh (absorbed
    from the retired orient-global skill), persisted to state so invocation-
    time reads stay local-only. Honest about failure rather than skipping."""
    scan = Path.home() / ".claude/skills/orient/bin/scan-active.sh"
    since = os.environ.get("SINCE", "yesterday 00:00")
    out = "(git scan unavailable: scan-active.sh missing)"
    if scan.exists():
        try:
            p = subprocess.run(["bash", str(scan), since],
                               capture_output=True, text=True, timeout=180)
            out = (p.stdout.strip()
                   or f"(git scan exited {p.returncode} with no output)")
        except subprocess.TimeoutExpired:
            jl.log("git scan timed out")
            out = "(git scan unavailable: timed out)"
        except OSError as e:
            jl.log(f"git scan failed: {e}")
            out = f"(git scan unavailable: {e})"
    if not out.startswith("(git scan"):
        stamp = jl.now_et().isoformat(timespec="seconds")
        out = (f"(git scan taken {stamp} ET; commit ages below are "
               f"relative to this moment)\n{out}")
    (jl.state_dir() / "git-state.md").write_text(out + "\n")
    return out


def extract_prompt(group) -> str:
    template = (PROMPTS / "extract-rubric.md").read_text()
    blocks = []
    for s in group:
        blocks.append(f"[SESSION {s['sid']} — project {Path(s['dir']).name}]\n{s['rendered']}")
    return template.replace("{slices_block}", "\n\n---\n\n".join(blocks))


def write_summary(date, slug, sid, n, payload: dict) -> None:
    d = jl.state_dir() / "summaries" / date
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{slug}--{sid}--{n}.md"
    f.write_text(json.dumps(payload, indent=1))


def synthesis_prompt(date, github_block, git_state_block) -> str:
    template = (PROMPTS / "synthesis.md").read_text()
    summaries = []
    sd = jl.state_dir() / "summaries" / date
    if sd.is_dir():
        for f in sorted(sd.glob("*.md")):
            summaries.append(f"## {f.stem}\n```json\n{f.read_text()}\n```")
    ledger = "\n".join(td.parse_ledger(td.ledger_path().read_text())["open"]) or "(none)"
    return (template
            .replace("{date}", date)
            .replace("{summaries_block}", "\n\n".join(summaries) or "(none)")
            .replace("{open_ledger_block}", ledger)
            .replace("{tf_notes_block}", tf_diff())
            .replace("{github_block}", github_block)
            .replace("{git_state_block}", git_state_block))


REF_RE = re.compile(r"#(\d+)\b")


def _valid_refs(*sources) -> set:
    """Issue/PR numbers that genuinely appear in raw, unsynthesized source
    data (the GitHub API feed, git log), for cross-checking the synthesis
    ferry's claims against ground truth."""
    refs = set()
    for s in sources:
        refs.update(REF_RE.findall(s or ""))
    return refs


def _flag_unverified_refs(text: str, valid: set) -> tuple:
    """Mark any #NNN citation the synthesis ferry wrote that doesn't appear
    in the raw feed it was given — rather than trusting it silently. The
    ferry once invented a PR #238 / author "anakette" wholesale (2026-07-30
    digest); a plausible-looking wrong number is worse than a flagged one."""
    flagged = []

    def repl(m):
        num = m.group(1)
        if num not in valid:
            flagged.append(num)
            return f"#{num} [UNVERIFIED — not in raw GitHub/git feed, confirm before trusting]"
        return m.group(0)

    return REF_RE.sub(repl, text), flagged


def _mutation_refs_valid(mut: dict, valid: set) -> bool:
    return all(n in valid for n in REF_RE.findall(json.dumps(mut)))


def collect_cwds(lines) -> set:
    cwds = set()
    for ln in lines:
        try:
            e = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(e, dict) and e.get("cwd"):
            cwds.add(e["cwd"])
    return cwds


def run_once() -> dict:
    cfg = jl.load_config()
    if not cfg["model"]:
        jl.die("no model pinned — run the jeeves setup (see SKILL.md) first")
    offs = offsets_load()
    date = jl.today_et()
    counts = {"sessions": 0, "extracted": 0, "failed": 0, "digest": 0, "skipped": 0}
    slices = []
    all_cwds = set()
    pending_offsets = dict(offs)

    for path in discover_sessions():
        key = str(path)
        off = offs.get(key, {}).get("offset", 0)
        lines, new_off, status = jl.read_delta(path, off)
        if status == "rotated":
            jl.log(f"rotation detected, re-reading from 0: {key}")
        all_cwds |= collect_cwds(lines)
        entries = jl.denoise(lines, cfg["truncate"])
        if new_off == off and not entries:
            continue
        counts["sessions"] += 1
        if len(entries) < cfg["trivial_min"]:
            counts["skipped"] += 1
            pending_offsets[key] = {"offset": new_off, "size": path.stat().st_size}
            continue
        slices.append({"path": key, "dir": str(path.parent), "slug": path.parent.name,
                       "sid": session_id_of(path), "new_off": new_off,
                       "entries": entries, "rendered": jl.render_slice(entries)})

    for group in group_slices(slices, cfg["batch_under"], cfg["batch_max"]):
        res = jl.ferry(extract_prompt(group), cfg["model"])
        if not res["ok"]:
            counts["failed"] += len(group)
            jl.log(f"extraction failed ({res['error']}); offsets held for retry: "
                   f"{[s['sid'] for s in group]}")
            continue  # offsets NOT advanced — retry next run
        payload = jl.parse_fenced_json(res["message"])
        if not isinstance(payload, list):
            counts["failed"] += len(group)
            jl.log(f"extraction output unparseable (task {res['task_id']}); "
                   f"offsets held; first 300 chars: {res['message'][:300]!r}")
            continue
        by_session = {p.get("session"): p for p in payload if isinstance(p, dict)}
        # Lenient fallback: with exactly one slice and one returned entry, a
        # mislabeled session key is still obviously the answer.
        if len(group) == 1 and len(payload) == 1 and isinstance(payload[0], dict) \
                and group[0]["sid"] not in by_session:
            by_session[group[0]["sid"]] = {**payload[0], "session": group[0]["sid"]}
        for s in group:
            item = by_session.get(s["sid"], {"session": s["sid"], "note": "no block for session"})
            n = len(list((jl.state_dir() / "summaries" / date).glob(f"*--{s['sid']}--*.md"))) + 1
            write_summary(date, s["slug"], s["sid"], n, item)
            pending_offsets[s["path"]] = {"offset": s["new_off"],
                                          "size": Path(s["path"]).stat().st_size}
            counts["extracted"] += 1

    offsets_save(pending_offsets)

    dirs_file = jl.state_dir() / "project-dirs.txt"
    prev = set(dirs_file.read_text().splitlines()) if dirs_file.exists() else set()
    union = sorted(prev | all_cwds)
    dirs_file.write_text("\n".join(union) + ("\n" if union else ""))
    td.ingest_repo_todos(union)

    if counts["extracted"]:
        td.reconcile()
        github_block = gh_review()
        git_state_block = git_state()
        valid_refs = _valid_refs(github_block, git_state_block)
        res = jl.ferry(synthesis_prompt(date, github_block, git_state_block),
                        cfg["model"], wait_s=600)
        if not res["ok"]:
            jl.log(f"synthesis failed: {res['error']} — digest not refreshed")
        else:
            m = re.search(r"```markdown\s*\n(.*?)```", res["message"], re.S)
            digest_md = m.group(1) if m else None
            if digest_md is None:
                # Lenient fallback: on long outputs the model sometimes
                # drops the ```markdown fence but still emits the digest —
                # take everything from the digest heading to the next fence.
                m2 = re.search(r"(# jeeves digest\b.*?)(?=\n```|\Z)",
                               res["message"], re.S)
                if m2:
                    digest_md = m2.group(1).strip() + "\n"
                    jl.log(f"digest recovered without markdown fence")
            muts = jl.parse_fenced_json(res["message"])
            if digest_md is None or not isinstance(muts, list):
                jl.log("synthesis output malformed; digest not refreshed")
            else:
                digest_md, flagged = _flag_unverified_refs(digest_md, valid_refs)
                if flagged:
                    jl.log(f"digest: flagged unverified refs {sorted(set(flagged))}")
                safe_muts, dropped = [], []
                for mut in muts:
                    (safe_muts if _mutation_refs_valid(mut, valid_refs)
                     else dropped).append(mut)
                if dropped:
                    jl.log(f"todo mutations skipped — unverified refs: {dropped}")
                d = jl.state_dir() / "digests"
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{date}.md").write_text(digest_md)
                applied = td.apply_mutations(safe_muts)
                counts["digest"] = 1
                jl.log(f"digest written; mutations: {applied}")
    return counts


def seed_offsets() -> int:
    """Mark all current transcript bytes as seen, without dispatching.
    First-install behavior: jeeves starts from now, not from the beginning
    of history."""
    offs = offsets_load()
    n = 0
    for path in discover_sessions():
        key = str(path)
        if key in offs:
            continue
        lines, new_off, _ = jl.read_delta(path, 0)
        offs[key] = {"offset": new_off, "size": path.stat().st_size}
        n += 1
    offsets_save(offs)
    jl.log(f"seeded offsets for {n} transcripts")
    return n


def main() -> None:
    ap = argparse.ArgumentParser(prog="collect.py")
    ap.add_argument("--seed", action="store_true",
                    help="mark all current transcript bytes as seen and exit")
    a = ap.parse_args()
    if a.seed:
        print(json.dumps({"seeded": seed_offsets()}))
        return
    lock = (jl.state_dir() / "collect.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        jl.log("previous run still active; skipping")
        print("jeeves: previous run still active; skipping")
        return
    counts = run_once()
    jl.log(f"run complete: {counts}")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
