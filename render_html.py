#!/usr/bin/env python3
"""Generate a self-contained reactive HTML viewer for each paper collection JSON.

Checkbox state is stored as a "read" field directly in the JSON file.
Run alongside serve.py so the HTML can read/write the JSON via fetch().
Falls back to embedded data + localStorage when the server is not running.
"""

import argparse
import json
from pathlib import Path


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --bg: #f8f8f8; --surface: #fff; --border: #e0e0e0;
  --text: #1a1a1a; --muted: #666; --accent: #2563eb;
  --read-bg: #f0fdf4; --read-border: #bbf7d0;
  --badge-high: #d1fae5; --badge-high-text: #065f46;
  --badge-mid:  #fef3c7; --badge-mid-text:  #92400e;
  --badge-low:  #fee2e2; --badge-low-text:  #991b1b;
}}

body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg); color: var(--text);
  font-size: 14px; line-height: 1.5;
}}

.header {{
  position: sticky; top: 0; z-index: 10;
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 10px 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
}}
.header h1 {{ font-size: 15px; font-weight: 700; flex-shrink: 0; }}
.stats {{ color: var(--muted); font-size: 12px; flex-shrink: 0; }}

.mode {{
  font-size: 11px; padding: 2px 7px; border-radius: 4px;
  font-weight: 600; flex-shrink: 0;
}}
.mode.live   {{ background: #d1fae5; color: #065f46; }}
.mode.offline {{ background: #f3f4f6; color: #6b7280; }}

.search {{
  flex: 1; min-width: 140px; padding: 5px 10px;
  border: 1px solid var(--border); border-radius: 6px;
  font-size: 13px; outline: none;
}}
.search:focus {{ border-color: var(--accent); }}

.tabs {{ display: flex; gap: 3px; flex-shrink: 0; }}
.tab {{
  padding: 4px 11px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--surface);
  cursor: pointer; font-size: 12px; color: var(--muted);
}}
.tab.active {{ background: var(--accent); border-color: var(--accent); color: #fff; }}

.btn {{
  padding: 4px 11px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--surface);
  cursor: pointer; font-size: 12px; color: var(--muted); flex-shrink: 0;
}}
.btn:hover {{ border-color: var(--accent); color: var(--accent); }}

.list {{
  max-width: 900px; margin: 14px auto; padding: 0 16px;
  display: flex; flex-direction: column; gap: 8px;
}}

.paper {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 11px 13px; display: flex; gap: 11px;
  transition: border-color 0.12s, background 0.12s;
}}
.paper.read {{ background: var(--read-bg); border-color: var(--read-border); }}
.paper.hidden {{ display: none; }}

.paper-check {{
  margin-top: 3px; width: 15px; height: 15px;
  flex-shrink: 0; cursor: pointer; accent-color: var(--accent);
}}
.paper-body {{ flex: 1; min-width: 0; }}
.paper-title {{
  font-weight: 600; font-size: 14px;
  color: var(--accent); text-decoration: none; line-height: 1.4;
}}
.paper-title:hover {{ text-decoration: underline; }}
.paper-meta {{ display: flex; flex-wrap: wrap; gap: 7px; align-items: center; margin-top: 3px; }}
.paper-date {{ font-size: 12px; color: var(--muted); }}
.badge {{ font-size: 11px; padding: 1px 5px; border-radius: 4px; font-weight: 600; }}
.badge-high {{ background: var(--badge-high); color: var(--badge-high-text); }}
.badge-mid  {{ background: var(--badge-mid);  color: var(--badge-mid-text);  }}
.badge-low  {{ background: var(--badge-low);  color: var(--badge-low-text);  }}
.paper-abstract {{ margin-top: 5px; font-size: 13px; color: #333; }}

.empty {{ text-align: center; color: var(--muted); padding: 60px 0; }}
</style>
</head>
<body>

<div class="header">
  <h1>{title}</h1>
  <span class="mode offline" id="mode">offline</span>
  <span class="stats" id="stats"></span>
  <input class="search" id="search" type="search" placeholder="Search titles &amp; abstracts…">
  <div class="tabs">
    <button class="tab active" data-tab="all"    onclick="setTab('all')">All</button>
    <button class="tab"        data-tab="unread" onclick="setTab('unread')">Unread</button>
    <button class="tab"        data-tab="read"   onclick="setTab('read')">Read</button>
  </div>
  <button class="btn" onclick="markAllVisible()">Mark visible read</button>
</div>

<div class="list" id="list"></div>
<div class="empty" id="empty" style="display:none">No papers match your filter.</div>

<script>
const JSON_URL = {json_url};
const LS_KEY   = 'paper-tracker-' + {filter_name_json};

// Embedded fallback (baked in at render time, includes current "read" states)
const EMBEDDED = {papers_json};

let papers = [];
let live = false;   // true when server is reachable
let currentTab = 'all';
let searchText = '';

// ── localStorage helpers (offline fallback) ────────────────────────────────
function lsLoad() {{
  try {{ return JSON.parse(localStorage.getItem(LS_KEY) || '{{}}'); }}
  catch {{ return {{}}; }}
}}
function lsSave() {{
  const s = {{}};
  papers.forEach(p => {{ if (p.read) s[p.arxiv_id] = 1; }});
  localStorage.setItem(LS_KEY, JSON.stringify(s));
}}

// ── Server I/O ─────────────────────────────────────────────────────────────
async function fetchPapers() {{
  const r = await fetch(JSON_URL, {{ cache: 'no-store' }});
  if (!r.ok) throw new Error(r.status);
  return r.json();
}}
async function putPapers() {{
  const r = await fetch(JSON_URL, {{
    method: 'PUT',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(papers, null, 2),
  }});
  if (!r.ok) throw new Error('PUT failed: ' + r.status);
}}

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {{
  try {{
    papers = await fetchPapers();
    live = true;
    document.getElementById('mode').textContent = 'live';
    document.getElementById('mode').className = 'mode live';
  }} catch (_) {{
    // Server not running — use embedded data merged with localStorage
    papers = EMBEDDED.map(p => ({{...p}}));
    const s = lsLoad();
    papers.forEach(p => {{ if (s[p.arxiv_id]) p.read = true; }});
  }}
  buildList();
  renderList();
}}

// ── Toggle ─────────────────────────────────────────────────────────────────
async function toggle(id) {{
  const p = papers.find(x => x.arxiv_id === id);
  if (!p) return;
  if (p.read) delete p.read; else p.read = true;
  if (live) {{
    try {{ await putPapers(); }} catch (e) {{ console.error('Save failed', e); }}
  }} else {{
    lsSave();
  }}
  renderList();
}}

async function markAllVisible() {{
  document.querySelectorAll('.paper:not(.hidden)').forEach(el => {{
    const p = papers.find(x => x.arxiv_id === el.dataset.id);
    if (p) p.read = true;
  }});
  if (live) {{
    try {{ await putPapers(); }} catch (e) {{ console.error('Save failed', e); }}
  }} else {{
    lsSave();
  }}
  renderList();
}}

// ── Render ─────────────────────────────────────────────────────────────────
function confBadge(conf) {{
  const pct = Math.round((conf || 0) * 100);
  const cls = pct >= 95 ? 'badge-high' : pct >= 80 ? 'badge-mid' : 'badge-low';
  return `<span class="badge ${{cls}}">${{pct}}%</span>`;
}}

function buildList() {{
  const list = document.getElementById('list');
  list.innerHTML = '';
  papers.forEach(p => {{
    const div = document.createElement('div');
    div.className = 'paper';
    div.dataset.id = p.arxiv_id;
    div.dataset.title = (p.title || '').toLowerCase();
    div.dataset.abstract = (p.abstract_concise || '').toLowerCase();
    div.innerHTML = `
      <input type="checkbox" class="paper-check" onclick="toggle('${{p.arxiv_id}}')">
      <div class="paper-body">
        <a class="paper-title" href="${{p.link || 'https://arxiv.org/abs/' + p.arxiv_id}}"
           target="_blank" rel="noopener">${{p.title || p.arxiv_id}}</a>
        <div class="paper-meta">
          <span class="paper-date">${{p.date || p.arxiv_id}}</span>
          ${{confBadge(p.confidence)}}
        </div>
        ${{p.abstract_concise ? `<div class="paper-abstract">${{p.abstract_concise}}</div>` : ''}}
      </div>`;
    list.appendChild(div);
  }});
}}

function renderList() {{
  const q = searchText.toLowerCase();
  let visible = 0, totalRead = 0;
  document.querySelectorAll('.paper').forEach(el => {{
    const p = papers.find(x => x.arxiv_id === el.dataset.id);
    const read = !!(p && p.read);
    if (read) totalRead++;
    const matchTab = currentTab === 'all' ||
      (currentTab === 'read' && read) || (currentTab === 'unread' && !read);
    const matchSearch = !q ||
      el.dataset.title.includes(q) || el.dataset.abstract.includes(q);
    const show = matchTab && matchSearch;
    el.classList.toggle('hidden', !show);
    el.classList.toggle('read', read);
    el.querySelector('.paper-check').checked = read;
    if (show) visible++;
  }});
  document.getElementById('stats').textContent =
    `${{totalRead}} / ${{papers.length}} read · ${{visible}} shown`;
  document.getElementById('empty').style.display = visible === 0 ? '' : 'none';
}}

function setTab(tab) {{
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === tab));
  renderList();
}}

document.getElementById('search').addEventListener('input', e => {{
  searchText = e.target.value;
  renderList();
}});

init();
</script>
</body>
</html>
"""


def generate(json_path: Path, out_path: Path) -> None:
    entries = json.loads(json_path.read_text(encoding="utf-8"))
    name = json_path.stem
    # JSON_URL is relative so it works regardless of port
    json_url = json.dumps(f"./{json_path.name}")
    html = HTML_TEMPLATE.format(
        title=name,
        json_url=json_url,
        filter_name_json=json.dumps(name),
        papers_json=json.dumps(entries, ensure_ascii=False),
    )
    out_path.write_text(html, encoding="utf-8")
    read_count = sum(1 for e in entries if e.get("read"))
    print(f"Written: {out_path}  ({len(entries)} papers, {read_count} read)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path,
                        help="JSON files to render (default: all *.json in current dir)")
    parser.add_argument("--out-dir", type=Path, default=Path("collections"),
                        help="Output directory for HTML files (default: collections/)")
    args = parser.parse_args()

    inputs = args.inputs or sorted(Path("collections").glob("*.json"))
    inputs = [p for p in inputs if not p.stem.endswith(".filter")
              and p.stem not in ("package", "package-lock")]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for json_path in inputs:
        out_path = args.out_dir / (json_path.stem + ".html")
        generate(json_path, out_path)


if __name__ == "__main__":
    main()
