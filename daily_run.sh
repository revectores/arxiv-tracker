#!/usr/bin/env bash
set -euo pipefail

REPO=/Users/rex/codebase/research-tracker/arxiv-tracker
PYTHON=/usr/local/bin/python3

# Env vars (OPENAI_API_KEY, http_proxy, https_proxy) come from caller —
# the LaunchAgent plist provides them; interactive shells inherit them.

mkdir -p "$REPO/logs"
cd "$REPO"

YESTERDAY=$(date -v-1d '+%Y-%m-%d')
COLLECT_FROM=$(date -v-7d '+%Y-%m-%d')
COLLECT_TO=$(date '+%Y-%m-%d')
LOGDATE=$(date '+%Y%m%d')

echo "[$(date)] Starting daily run for $YESTERDAY"

# 1. Fetch arXiv metadata for yesterday across all tracked categories
$PYTHON fetch.py \
    --sets cs.DB cs.IR cs.MM cs.CL cs.AI \
    --from-date "$YESTERDAY" \
    --until-date "$YESTERDAY" \
    --db papers.db \
    >> "$REPO/logs/fetch_$LOGDATE.log" 2>&1

echo "[$(date)] Fetch done. Running filters..."

# 2. For each filter.json, collect only yesterday's papers (filter file dates unchanged)
for filter_file in "$REPO/collections"/*.filter.json; do
    name=$(basename "$filter_file" .filter.json)
    echo "[$(date)] Collecting: $name"

    $PYTHON paper_collect.py \
        --filter "$filter_file" \
        --from-date "$COLLECT_FROM" \
        --to-date "$COLLECT_TO" \
        >> "$REPO/logs/collect_${name}_$LOGDATE.log" 2>&1

    echo "[$(date)] Done: $name"
done

echo "[$(date)] Generating report..."
$PYTHON generate_report.py >> "$REPO/logs/launchd.log" 2>&1

echo "[$(date)] Daily run complete."
