#!/usr/bin/env python3
"""
Batch download and translate arXiv papers from a collected result JSON.

The script:
1. Loads a JSON array of paper objects.
2. Keeps only papers whose `confidence` is >= a given threshold.
3. Calls `download.py` once per matching `arxiv_id`.

Usage:
  python batch_download_translate.py \
      --input ./collected.json \
      --threshold 0.98
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


DEFAULT_INPUT = "collected.json"
DEFAULT_DOWNLOAD_SCRIPT = "download.py"
DEFAULT_MAX_WORKERS = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to the collected result JSON file")
    parser.add_argument("--threshold", type=float, required=True, help="Keep papers with confidence >= threshold")
    parser.add_argument(
        "--download-script",
        default=DEFAULT_DOWNLOAD_SCRIPT,
        help="Path to the existing download.py script",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="Maximum number of parallel download.py processes",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing later papers if one download.py run fails",
    )
    return parser.parse_args()


def load_results(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return [item for item in data if isinstance(item, dict)]


def sort_papers(papers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        papers,
        key=lambda item: (
            float(item.get("confidence") or 0.0),
            str(item.get("arxiv_id") or ""),
        ),
        reverse=True,
    )


def run_download(download_script: Path, arxiv_id: str, title: str) -> subprocess.CompletedProcess[str]:
    print(f"Running download.py for {arxiv_id} {title}")
    return subprocess.run(
        [sys.executable, str(download_script), "--no-open", arxiv_id],
        text=True,
        capture_output=True,
    )


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    download_script = Path(args.download_script)

    papers = load_results(input_path)
    filtered = sort_papers(
        paper for paper in papers if float(paper.get("confidence") or 0.0) >= args.threshold
    )

    if not filtered:
        print(f"No papers matched threshold >= {args.threshold:.2f}")
        return

    if not download_script.exists():
        raise FileNotFoundError(f"download script not found: {download_script}")

    jobs: List[tuple[str, str]] = []
    for index, paper in enumerate(filtered, start=1):
        arxiv_id = str(paper.get("arxiv_id") or "").strip()
        title = str(paper.get("title") or "").strip()
        if not arxiv_id:
            print(f"[{index}/{len(filtered)}] Skipping paper with missing arxiv_id: {title}")
            continue
        jobs.append((arxiv_id, title))

    if not jobs:
        print("No valid arXiv IDs found after filtering")
        return

    failures: List[str] = []
    with ThreadPoolExecutor(max_workers=max(args.max_workers, 1)) as executor:
        future_to_job = {
            executor.submit(run_download, download_script, arxiv_id, title): (arxiv_id, title)
            for arxiv_id, title in jobs
        }
        for index, future in enumerate(as_completed(future_to_job), start=1):
            arxiv_id, title = future_to_job[future]
            try:
                result = future.result()
            except Exception as exc:
                message = f"[{index}/{len(jobs)}] download.py crashed for {arxiv_id}: {exc}"
                if args.continue_on_error:
                    print(message)
                    failures.append(message)
                    continue
                raise RuntimeError(message) from exc

            if result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            if result.returncode == 0:
                print(f"[{index}/{len(jobs)}] Completed {arxiv_id} {title}")
                continue

            if result.stderr:
                print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
            message = f"[{index}/{len(jobs)}] download.py failed for {arxiv_id} with exit code {result.returncode}"
            if args.continue_on_error:
                print(message)
                failures.append(message)
                continue
            raise RuntimeError(message)

    if failures:
        if args.continue_on_error:
            print(f"Finished with {len(failures)} failed job(s)")
            return
        raise RuntimeError(f"{len(failures)} download jobs failed")


if __name__ == "__main__":
    main()
