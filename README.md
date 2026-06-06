# arxiv-tracker

A pipeline for tracking arXiv papers on specific research topics. It harvests paper metadata via the OAI-PMH API, applies a two-stage LLM filter (coarse + refine) to identify relevant papers, and provides tools to download, translate, and generate summaries.

## Pipeline

```
fetch.py  →  paper_collect.py  →  introduce_papers.py
                                →  batch_download_translate.py
```

1. **Fetch** — `fetch.py` pulls arXiv metadata into `papers.db` (SQLite) via OAI-PMH.
2. **Collect** — `paper_collect.py` reads `papers.db`, applies keyword pre-filter, coarse LLM pass, then refine LLM pass, and writes matches to `collections/NAME.json`. Runs are resumable.
3. **Introduce** — `introduce_papers.py` generates markdown summaries for matched papers into `paper-intros/`.
4. **Download + Translate** — `batch_download_translate.py` / `download.py` fetch PDFs and produce Chinese translations via `translate.py`.

A daily cron job (`daily_run.sh`) runs fetch + collect for all active filter configs and generates a markdown report.

## Usage

```bash
# 1. Fetch arXiv metadata
python fetch.py --sets cs.AI cs.CL --from-date 2026-05-01 --until-date 2026-05-31 --db papers.db

# 2. Filter papers with LLM
export OPENAI_API_KEY="..."
python paper_collect.py --filter ./collections/LLM_MEMORY.filter.json

# 3. Browse results
python serve.py          # then open http://localhost:8765/collections/LLM_MEMORY.html
python render_html.py    # regenerate HTML from current JSON

# 4. Generate intro summaries
python introduce_papers.py --input ./collections/LLM_MEMORY.json --threshold 0.98

# 5. Download + translate a paper
python download.py 2504.04310
```

## Collections

Each research topic lives under `collections/` as a pair of files:

| File | Purpose |
|---|---|
| `NAME.filter.json` | Filter config + run progress (tracked in git) |
| `NAME.json` | Collected paper matches (generated, gitignored) |
| `NAME.html` | Interactive viewer UI (generated, gitignored) |

Current collections: `LLM_MEMORY`, `LLM_PSYCHOLOGY`, `MMaDB`, `SEMANTIC_OPS`, `VECTOR_DB`.

### Filter config fields

```json
{
  "from_date": "2026-01-01",
  "to_date": "2026-05-31",
  "categories": ["cs.AI", "cs.CL"],
  "focus": "Detailed description of what's in/out of scope...",
  "abstract_keywords": ["memory", "retrieval", "RAG"],
  "coarse_model": "gpt-5.4-mini",
  "refine_model": "gpt-5.4",
  "coarse_min_confidence": 0.5
}
```

Progress fields (`checked_base_ids`, `missing_base_ids`) are appended automatically, making runs resumable after interruption.

## Requirements

- Python 3.10+
- `openai` package (`pip install openai`)
- `OPENAI_API_KEY` environment variable set for `paper_collect.py` and `introduce_papers.py`
- macOS with Xcode CLI tools for `download.py` (uses `/usr/bin/SetFile`)
- Local HTTP proxy at `http://127.0.0.1:7890` for arXiv PDF downloads
- `hjfy_config.py` (not committed) with cookies for the Chinese translation service

## Files

| File | Description |
|---|---|
| `fetch.py` | Harvest arXiv metadata into SQLite via OAI-PMH |
| `paper_collect.py` | Two-stage LLM filter (coarse + refine) |
| `introduce_papers.py` | Generate markdown paper summaries with web search |
| `download.py` | Download arXiv PDF and produce Chinese translation |
| `batch_download_translate.py` | Parallel batch version of `download.py` |
| `translate.py` | Chinese translation via hjfy.top |
| `render_html.py` | Build interactive HTML viewer from collected JSON |
| `serve.py` | Local HTTP server with PUT support for read-state persistence |
| `generate_report.py` | Generate daily markdown report from fetch/collect logs |
| `refine_collected.py` | Re-run refine pass on existing coarse matches |
| `daily_run.sh` | Orchestrates daily fetch + collect + report |
