import argparse
import re
import time
from pathlib import Path
from urllib.parse import quote
import subprocess

import requests

from translate import translate_from_arxiv_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download an arXiv PDF and translate it.")
    parser.add_argument("arxiv_id", help="arXiv ID to download and translate")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the translated file after download/translation",
    )
    return parser.parse_args()


def sanitize_filename(name: str) -> str:
    name = name.replace('\n', ' ').replace(' : ', ' - ').replace(': ', ' - ').replace(':', ' - ')
    name = re.sub(r"\s+", " ", name).strip()
    return name


def extract_title_from_abs_html(html: str) -> str | None:
    """
    从 arXiv abs 页面 HTML 中提取标题
    常见格式:
      <title>[xxxx.xxxxx] Title Here - arXiv</title>
    或页面正文里:
      <h1 class="title mathjax">Title: ...</h1>
    """
    # 优先从 <title> 提取
    m = re.search(r"<title>\s*\[[^\]]+\]\s*(.*?)\s*-\s*arXiv\s*</title>", html, re.I | re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    # 备用：从 h1.title 提取
    m = re.search(
        r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>\s*(?:<span[^>]*>\s*Title:\s*</span>)?\s*(.*?)\s*</h1>',
        html,
        re.I | re.S,
    )
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1))  # 去掉可能残留的标签
        return re.sub(r"\s+", " ", title).strip()

    return None


def download_arxiv_pdf_by_html(
    arxiv_id: str,
    out_dir: str = "/Users/rex/Library/Mobile Documents/com~apple~CloudDocs/reference/Reference Unclassified",
    timeout: int = 30,
    sleep_sec: float = 1.0,
) -> Path:
    """
    通过 arXiv 的 HTML 页面确认论文信息，再下载 PDF。
    不使用 arXiv API。
    """
    session = requests.Session()
    session.proxies = {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        }
    )

    abs_url = f"https://arxiv.org/abs/{quote(arxiv_id)}"
    pdf_url = f"https://arxiv.org/pdf/{quote(arxiv_id)}.pdf"

    # 1) 先请求 abs HTML 页面
    resp = session.get(abs_url, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    # 2) 提取标题
    title = extract_title_from_abs_html(html)
    if not title:
        title = arxiv_id

    safe_title = sanitize_filename(title)
    safe_id = sanitize_filename(arxiv_id)

    filename = f"{safe_id} {safe_title}.pdf"

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    pdf_path = out_path / filename

    # 稍微停一下，避免太激进
    time.sleep(sleep_sec)

    # 3) 下载 PDF
    with session.get(pdf_url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "").lower()
        if "pdf" not in content_type and not pdf_url.endswith(".pdf"):
            raise ValueError(f"返回内容看起来不是 PDF: {content_type}")

        with open(pdf_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    return pdf_path


if __name__ == "__main__":
    args = parse_args()
    arxiv_id = args.arxiv_id
    path = download_arxiv_pdf_by_html(arxiv_id)
    print(f"Download Complete: {path}")
    translate_from_arxiv_id(arxiv_id, open_file=not args.no_open)
    print(f"Translate Complete: {path}")
    subprocess.run(['/usr/bin/SetFile', '-a', 'E', path])
