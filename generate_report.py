#!/usr/bin/env python3
"""Generate a markdown daily run report from fetch + collect logs."""

import argparse
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).parent
LOGS = REPO / "logs"
REPORTS = REPO / "reports"


def parse_fetch_log(logdate: str) -> dict:
    path = LOGS / f"fetch_{logdate}.log"
    if not path.exists():
        return {}
    text = path.read_text()
    sets = {}
    for m in re.finditer(r"\[(\w+\.\w+)\] DONE — (\d+) inserted, (\d+) updated", text):
        sets[m.group(1)] = {"inserted": int(m.group(2)), "updated": int(m.group(3))}
    total = re.search(r"(\d+) new records", text)
    return {"sets": sets, "total_new": int(total.group(1)) if total else 0}


def parse_collect_log(logdate: str, name: str) -> dict:
    path = LOGS / f"collect_{name}_{logdate}.log"
    if not path.exists():
        return {}
    text = path.read_text()

    start_m = re.search(r"Resuming with (\d+) existing refined matches", text)
    done_m = re.search(r"Done\. (\d+) refined entries written", text)
    start_count = int(start_m.group(1)) if start_m else 0
    end_count = int(done_m.group(1)) if done_m else start_count

    # Pair each "Coarse check <id>" with the outcome that follows
    matches, refine_rejects = [], []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*\[[\d/]+\] Coarse check (\S+)", line)
        if not m:
            continue
        arxiv_id = m.group(1)
        for j in range(i + 1, min(i + 6, len(lines))):
            if "MATCH" in lines[j]:
                conf = re.search(r"conf=([\d.]+)", lines[j])
                matches.append((arxiv_id, float(conf.group(1)) if conf else 0.0))
                break
            if "REFINE REJECT" in lines[j]:
                conf = re.search(r"conf=([\d.]+)", lines[j])
                refine_rejects.append((arxiv_id, float(conf.group(1)) if conf else 0.0))
                break

    coarse_checked = len(re.findall(r"Coarse check", text))

    return {
        "start_count": start_count,
        "end_count": end_count,
        "new_matches": end_count - start_count,
        "coarse_checked": coarse_checked,
        "matches": matches,
        "refine_rejects": refine_rejects,
    }


def lookup_titles(arxiv_ids: list[str]) -> dict[str, str]:
    db = REPO / "papers.db"
    if not db.exists() or not arxiv_ids:
        return {}
    conn = sqlite3.connect(db)
    placeholders = ",".join("?" * len(arxiv_ids))
    rows = conn.execute(
        f"SELECT id, title FROM papers WHERE id IN ({placeholders})",
        arxiv_ids,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def render_report(logdate: str, filters: list[str]) -> str:
    # logdate is YYYYMMDD; the fetch covers the day before
    d = date(int(logdate[:4]), int(logdate[4:6]), int(logdate[6:8]))
    fetch_day = d - timedelta(days=1)

    fetch = parse_fetch_log(logdate)
    collects = {name: parse_collect_log(logdate, name) for name in filters}

    # collect all matched IDs for title lookup
    all_ids = [pid for c in collects.values() for pid, _ in c.get("matches", [])]
    all_ids += [pid for c in collects.values() for pid, _ in c.get("refine_rejects", [])]
    titles = lookup_titles(all_ids)

    lines = [f"# Daily Run Report — {d.strftime('%Y-%m-%d')}",
             f"*(covering arXiv submissions from {fetch_day})*", ""]

    # Fetch summary
    lines += ["## Fetch", ""]
    if fetch:
        total = fetch.get("total_new", 0)
        lines.append(f"**{total} new papers** harvested across {len(fetch['sets'])} categories.\n")
        lines.append("| Category | Inserted | Updated |")
        lines.append("|---|---|---|")
        for cat, s in sorted(fetch["sets"].items()):
            lines.append(f"| {cat} | {s['inserted']} | {s['updated']} |")
    else:
        lines.append("*(no fetch log found)*")
    lines.append("")

    # Per-filter collect summaries
    lines.append("## Collect")
    for name, c in collects.items():
        if not c:
            lines += ["", f"### {name}", "*(no log found)*"]
            continue
        new = c["new_matches"]
        lines += [
            "",
            f"### {name}",
            f"**{new} new match{'es' if new != 1 else ''}** "
            f"({c['coarse_checked']} newly checked, "
            f"{c['start_count']} → {c['end_count']} total)",
            "",
        ]
        if c["matches"]:
            lines.append("| arXiv ID | Conf | Title |")
            lines.append("|---|---|---|")
            for pid, conf in sorted(c["matches"], key=lambda x: -x[1]):
                title = titles.get(pid, "—")
                lines.append(f"| [{pid}](https://arxiv.org/abs/{pid}) | {conf:.0%} | {title} |")
        else:
            lines.append("*No new matches.*")
        if c["refine_rejects"]:
            lines.append("")
            lines.append("**Refine rejects:**")
            for pid, conf in c["refine_rejects"]:
                title = titles.get(pid, "—")
                lines.append(f"- [{pid}](https://arxiv.org/abs/{pid}) ({conf:.0%}) — {title}")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD (default: today's logs)")
    parser.add_argument("--all", action="store_true", help="Generate for all available log dates")
    default_filters = sorted(
        p.stem.removesuffix(".filter")
        for p in (REPO / "collections").glob("*.filter.json")
    )
    parser.add_argument("--filters", nargs="+", default=default_filters or ["LLM_MEMORY", "MMaDB", "VECTOR_DB"])
    args = parser.parse_args()

    REPORTS.mkdir(exist_ok=True)

    if args.all:
        dates = sorted({p.name[6:14] for p in LOGS.glob("fetch_*.log")})
    else:
        from datetime import datetime
        dates = [args.date or datetime.now().strftime("%Y%m%d")]

    for logdate in dates:
        report = render_report(logdate, args.filters)
        out = REPORTS / f"report_{logdate}.md"
        out.write_text(report)
        print(f"Written: {out}")


if __name__ == "__main__":
    main()
