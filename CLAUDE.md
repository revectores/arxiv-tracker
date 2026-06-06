# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo does

An arXiv paper tracking pipeline focused on LLM memory systems. It harvests paper metadata from arXiv's OAI-PMH API into SQLite, then uses a two-stage LLM filter (coarse + refine) to identify relevant papers, and provides tools to download, translate (Chinese), and generate markdown introductions for matched papers.

## Pipeline: end-to-end flow

1. **Harvest** — `fetch.py` pulls arXiv metadata via OAI-PMH into `papers.db` (SQLite).
2. **Collect** — `paper_collect.py` reads `papers.db`, applies keyword pre-filter, coarse LLM pass, then refine LLM pass, and writes matches to `collected.json`. Runs are resumable via a `.state.json` checkpoint file.
3. **Introduce** — `introduce_papers.py` uses GPT-5.4 with web search to generate markdown summaries into `paper-intros/`.
4. **Download + Translate** — `batch_download_translate.py` drives `download.py` in parallel; `download.py` fetches the PDF via arXiv HTML page and calls `translate.py` to get a Chinese translation via hjfy.top.

## Running the scripts

### Fetch arXiv metadata into papers.db
```bash
python fetch.py --sets cs.AI cs.CL --from-date 2026-04-01 --until-date 2026-04-21 --db papers.db
```

### Collect (filter) papers from DB
```bash
export OPENAI_API_KEY="..."
python paper_collect.py --filter ./collections/LLM_MEMORY.filter.json
```
All filter config (date range, categories, focus, models, keywords) is read from the `.filter.json`. Output is written to `collections/LLM_MEMORY.json` (same directory, `.filter.json` → `.json`). Progress (`checked_base_ids`, `missing_base_ids`) is written back into the filter file after each paper, making runs resumable.

### Generate markdown introductions
```bash
export OPENAI_API_KEY="..."
python introduce_papers.py --input ./collections/LLM_MEMORY.json --threshold 0.98
```
Output goes to `paper-intros/`. Use `--overwrite` to regenerate existing files.

### Batch download + translate
```bash
python batch_download_translate.py --input ./collections/LLM_MEMORY.json --threshold 0.98
```

### Download + translate a single paper
```bash
python download.py 2504.04310
```

## Key files and config

- `collections/` — one subdirectory holds all per-topic files: `NAME.filter.json` (config + progress), `NAME.json` (collected papers), `NAME.html` (viewer UI). Only `*.filter.json` files are tracked in git.
- `collections/*.filter.json` — combines filter config (`from_date`, `to_date`, `categories`, `focus`, `abstract_keywords`, `coarse_model`, `refine_model`, `coarse_min_confidence`) and run progress (`checked_base_ids`, `missing_base_ids`) in a single file. Edit config fields to change scope; progress is appended automatically.
- `hjfy_config.py` — cookies for the hjfy.top Chinese translation service (not committed; create locally).
- `papers.db` — SQLite database; `papers` table has one row per paper, `paper_categories` is a many-to-many join table indexed on `category` and `created`.

## Environment assumptions

- macOS with iCloud Drive mounted at `~/Library/Mobile Documents/com~apple~CloudDocs/`.
- PDF output goes to `Reference Unclassified/` (originals) and `Reference Translations/` (Chinese).
- Local HTTP proxy expected at `http://127.0.0.1:7890` for arXiv PDF downloads (in `download.py`).
- `/usr/bin/SetFile` (Xcode CLI tools) is called after each download to hide file extensions.
- Python 3.14 (`__pycache__` confirms this).
- `OPENAI_API_KEY` must be set for `paper_collect.py` and `introduce_papers.py`.

## LLM model defaults

- Coarse filter: `gpt-5.4-mini` (fast, cheap)
- Refine filter: `gpt-5.4` (conservative, higher precision)
- Intro generation: `gpt-5.4` with `reasoning_effort=high` and `web_search` tool restricted to `arxiv.org`

## OpenAI API usage notes

The scripts use `client.responses.create(...)` (OpenAI Responses API), not `chat.completions.create`. Response text is accessed via `response.output_text`. The classify helper in `paper_collect.py` strips JSON from surrounding text to handle model preambles.
