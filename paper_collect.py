#!/usr/bin/env python3
"""
Collect and strictly refine arXiv papers from `papers.db` into a single final output.

Workflow:
1. Load config and progress from a .filter.json file.
2. Load papers from `papers.db` by the date range and categories in the filter.
3. Skip papers whose abstracts do not match any keyword in the filter.
4. Run a coarse LLM filter, then refine only coarse matches above the threshold.
5. Write matches to <filter-stem>.json; update progress in the filter file after each paper.

Usage:
  export OPENAI_API_KEY="..."
  python paper_collect.py --filter ./LLM_MEMORY.filter.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

from openai import OpenAI


DB_PATH = "papers.db"


def parse_utc_date(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d")


def normalize_arxiv_id(arxiv_id: str) -> str:
    return arxiv_id.split("v")[0]


def sort_refined_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        entries,
        key=lambda item: normalize_arxiv_id(str(item.get("arxiv_id") or "")),
        reverse=True,
    )


def abstract_mentions_any_keyword(abstract: str, keywords: List[str]) -> bool:
    lowered = abstract.lower()
    for keyword in keywords:
        pattern = re.compile(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)")
        if pattern.search(lowered):
            return True
    return False


def format_author_name(author: Dict[str, Any]) -> str:
    parts = [str(author.get("forenames") or "").strip(), str(author.get("keyname") or "").strip()]
    return " ".join(part for part in parts if part)


def parse_authors(raw_authors: str | None) -> List[str]:
    if not raw_authors:
        return []
    try:
        authors = json.loads(raw_authors)
    except json.JSONDecodeError:
        return []
    if not isinstance(authors, list):
        return []
    return [name for name in (format_author_name(a) for a in authors if isinstance(a, dict)) if name]


def fetch_by_date_range(
    db_path: Path,
    categories: List[str],
    date_from: str,
    date_to: str,
) -> List[Dict[str, Any]]:
    category_placeholders = ",".join("?" for _ in categories)
    params = [date_from, date_to, *categories]
    query = f"""
        SELECT
            p.id,
            p.title,
            p.abstract,
            p.authors,
            p.created,
            GROUP_CONCAT(pc.category) AS categories
        FROM papers AS p
        JOIN paper_categories AS pc
            ON pc.paper_id = p.id
        WHERE p.created >= ?
          AND p.created <= ?
          AND p.id IN (
              SELECT DISTINCT paper_id
              FROM paper_categories
              WHERE category IN ({category_placeholders})
          )
        GROUP BY p.id, p.title, p.abstract, p.authors, p.created
        ORDER BY p.created DESC, p.id DESC
    """

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    papers: List[Dict[str, Any]] = []
    for row in rows:
        arxiv_id, title, abstract, raw_authors, created, raw_categories = row
        paper_categories = sorted({c.strip() for c in (raw_categories or "").split(",") if c.strip()})
        papers.append({
            "id": arxiv_id,
            "title": (title or "").strip().replace("\n", " "),
            "abstract": (abstract or "").strip().replace("\n", " "),
            "authors": parse_authors(raw_authors),
            "categories": paper_categories,
            "published": created,
            "created": created,
            "link": f"https://arxiv.org/abs/{normalize_arxiv_id(arxiv_id)}",
        })

    return papers


def build_prompt(paper: Dict[str, Any], focus: str) -> str:
    return f"""You are an expert research assistant filtering arXiv papers.

Below is the filtering criteria. It defines a focus area, included content (in scope), and excluded content (out of scope).

---
{focus}
---

Based on the criteria above, decide whether the paper below is in scope.
If and only if it is, produce a concise 1-3 sentence summary (<= 60 words) capturing the paper's core contribution in plain English.
If it is not in scope, leave abstract_concise empty.

Paper metadata:
Title: {paper["title"]}
Authors: {", ".join(paper["authors"])}
Categories: {", ".join(paper["categories"])}

Abstract:
{paper["abstract"]}

Output ONLY valid JSON:
{{
  "is_relevant": true | false,
  "confidence": float between 0 and 1,
  "reason": "short reason",
  "abstract_concise": "string (empty if not relevant)"
}}"""


def classify_json(client: OpenAI, model: str, prompt: str) -> Dict[str, Any]:
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": "You are a strict JSON generator. Output only valid JSON with the requested keys."},
            {"role": "user", "content": prompt},
        ],
    )
    text = (response.output_text or "").strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise RuntimeError(f"Invalid JSON output: {text}")
    return json.loads(text[start:end])


def atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_existing(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_filter(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in filter file {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"Filter file {path} must contain a JSON object.")
    required = ("from_date", "to_date", "categories", "focus", "abstract_keywords",
                "coarse_model", "refine_model", "coarse_min_confidence")
    missing = [k for k in required if k not in data]
    if missing:
        raise SystemExit(f"Filter file {path} is missing required keys: {missing}")
    return data


def save_filter(path: Path, data: Dict[str, Any], checked: Set[str], missing: Set[str]) -> None:
    updated = dict(data)
    updated["checked_base_ids"] = sorted(checked)
    updated["missing_base_ids"] = sorted(missing)
    atomic_write_json(path, updated)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--filter", required=True, type=Path, help="Path to .filter.json (config + progress)")
    parser.add_argument("--from-date", default=None, metavar="YYYY-MM-DD", help="Override from_date in filter file")
    parser.add_argument("--to-date", default=None, metavar="YYYY-MM-DD", help="Override to_date in filter file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set.")

    db_path = Path(DB_PATH)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    cfg = load_filter(args.filter)

    date_from = args.from_date or cfg["from_date"]
    date_to = args.to_date or cfg["to_date"]
    categories = cfg["categories"] if isinstance(cfg["categories"], list) else [c.strip() for c in cfg["categories"].split(",") if c.strip()]
    focus = cfg["focus"].strip()
    abstract_keywords = cfg["abstract_keywords"] if isinstance(cfg["abstract_keywords"], list) else [k.strip() for k in cfg["abstract_keywords"].split(",") if k.strip()]
    coarse_model = cfg["coarse_model"]
    refine_model = cfg["refine_model"]
    coarse_min_confidence = float(cfg["coarse_min_confidence"])

    parse_utc_date(date_from)
    parse_utc_date(date_to)
    if date_from > date_to:
        raise SystemExit("from_date must be earlier than or equal to to_date.")
    if not categories:
        raise SystemExit("categories must contain at least one entry.")
    if not abstract_keywords:
        raise SystemExit("abstract_keywords must contain at least one entry.")
    if not focus:
        raise SystemExit("focus is empty.")

    checked_base_ids: Set[str] = {str(x) for x in cfg.get("checked_base_ids") or []}
    missing_base_ids: Set[str] = {str(x) for x in cfg.get("missing_base_ids") or []}

    output_path = args.filter.with_name(args.filter.stem.removesuffix(".filter") + ".json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    papers = fetch_by_date_range(
        db_path=db_path,
        categories=categories,
        date_from=date_from,
        date_to=date_to,
    )
    print(f"Loaded {len(papers)} papers from {db_path} in [{date_from}, {date_to}] for categories {categories}")

    refined = sort_refined_entries(load_existing(output_path))

    existing_base_ids = {normalize_arxiv_id(str(item.get("arxiv_id") or "")) for item in refined if item.get("arxiv_id")}
    checked_base_ids.update(existing_base_ids)
    save_filter(args.filter, cfg, checked_base_ids, missing_base_ids)

    if refined:
        print(f"Resuming with {len(refined)} existing refined matches from {output_path}")

    client = OpenAI()
    total = len(papers)
    for index, paper in enumerate(papers, 1):
        base_id = normalize_arxiv_id(paper["id"])

        if base_id in checked_base_ids:
            print(f"[{index}/{total}] Skipping {paper['id']} (already checked)")
            continue
        if base_id in missing_base_ids:
            print(f"[{index}/{total}] Skipping {paper['id']} (metadata missing)")
            continue

        if not abstract_mentions_any_keyword(paper["abstract"], abstract_keywords):
            print(f"[{index}/{total}] Skipping {paper['id']} (no abstract keyword match)")
            checked_base_ids.add(base_id)
            save_filter(args.filter, cfg, checked_base_ids, missing_base_ids)
            continue

        print(f"[{index}/{total}] Coarse check {paper['id']}")
        try:
            coarse = classify_json(client, coarse_model, build_prompt(paper, focus))
        except Exception as exc:
            print(f"  COARSE ERROR: {exc}")
            continue

        is_coarse_relevant = bool(coarse.get("is_relevant"))
        coarse_confidence = float(coarse.get("confidence") or 0.0)
        coarse_summary = str(coarse.get("abstract_concise") or "").strip()

        if not is_coarse_relevant or not coarse_summary or coarse_confidence < coarse_min_confidence:
            print(f"  COARSE REJECT (conf={coarse_confidence:.2f})")
            checked_base_ids.add(base_id)
            save_filter(args.filter, cfg, checked_base_ids, missing_base_ids)
            continue

        print(f"  Coarse pass (conf={coarse_confidence:.2f}), refining")
        try:
            refined_decision = classify_json(client, refine_model, build_prompt(paper, focus))
        except Exception as exc:
            print(f"  REFINE ERROR: {exc}")
            continue

        is_refined_relevant = bool(refined_decision.get("is_relevant"))
        final_confidence = float(refined_decision.get("confidence") or 0.0)
        final_summary = str(refined_decision.get("abstract_concise") or "").strip()
        reason = str(refined_decision.get("reason") or "").strip()

        if is_refined_relevant and final_summary:
            refined.append({
                "arxiv_id": paper["id"],
                "title": paper["title"],
                "link": paper["link"],
                "date": paper["created"],
                "confidence": final_confidence,
                "abstract_concise": final_summary,
                "reason": reason,
            })
            refined = sort_refined_entries(refined)
            atomic_write_json(output_path, refined)
            print(f"  MATCH (conf={final_confidence:.2f})")
        else:
            print(f"  REFINE REJECT (conf={final_confidence:.2f})")

        checked_base_ids.add(base_id)
        save_filter(args.filter, cfg, checked_base_ids, missing_base_ids)

    refined = sort_refined_entries(refined)
    atomic_write_json(output_path, refined)
    print(f"\nDone. {len(refined)} refined entries written to {output_path}")


if __name__ == "__main__":
    main()
