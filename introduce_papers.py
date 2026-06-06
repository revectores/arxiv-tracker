#!/usr/bin/env python3
"""
Generate markdown paper introductions from a collected result JSON using GPT-5.4.

The script:
1. Loads a JSON array of paper objects.
2. Keeps only papers whose `confidence` is >= a given threshold.
3. For each paper, asks GPT-5.4 to introduce the paper using the arXiv URL and web search.
4. Writes one markdown file per paper into an output directory.

Usage:
  export OPENAI_API_KEY="..."
  python introduce_papers.py \
      --input ./collected.json \
      --threshold 0.98
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from openai import OpenAI


DEFAULT_INPUT = "collected.json"
DEFAULT_OUTPUT_DIR = "paper-intros"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_SLEEP = 0.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to the collected result JSON file")
    parser.add_argument("--threshold", type=float, required=True, help="Keep papers with confidence >= threshold")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory to write markdown files into")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model to use")
    parser.add_argument(
        "--reasoning-effort",
        default=DEFAULT_REASONING_EFFORT,
        choices=["none", "low", "medium", "high", "xhigh"],
        help="Reasoning effort for GPT-5.4",
    )
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="Sleep between API calls")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing markdown outputs instead of skipping them",
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


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "paper"


def safe_filename(arxiv_id: str, title: str) -> str:
    return f"{slugify(arxiv_id)}-{slugify(title)[:80]}.md"


def extract_text(response: Any) -> str:
    text = (getattr(response, "output_text", None) or "").strip()
    if text:
        return text

    output = getattr(response, "output", None) or []
    chunks: List[str] = []
    for item in output:
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", "") == "output_text":
                chunk = getattr(content, "text", "") or ""
                if chunk:
                    chunks.append(chunk)
    return "\n\n".join(chunks).strip()


def extract_citations(response: Any) -> List[Dict[str, str]]:
    citations: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    output = getattr(response, "output", None) or []
    for item in output:
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", []) or []:
            annotations = getattr(content, "annotations", None) or []
            for annotation in annotations:
                if getattr(annotation, "type", "") != "url_citation":
                    continue
                url = getattr(annotation, "url", "") or ""
                title = getattr(annotation, "title", "") or url
                key = (title, url)
                if not url or key in seen:
                    continue
                seen.add(key)
                citations.append({"title": title, "url": url})
    return citations


def build_prompt(paper: Dict[str, Any]) -> str:
    arxiv_id = str(paper.get("arxiv_id") or "").strip()
    title = str(paper.get("title") or "").strip()
    link = str(paper.get("link") or f"https://arxiv.org/abs/{arxiv_id}").strip()
    abstract = str(paper.get("abstract_concise") or "").strip()

    return f"""你是一位专注于系统/数据库/AI 基础设施方向的技术读者，请为以下论文写一篇详细的中文介绍，面向同领域的技术读者。

请先访问论文的 arXiv 页面获取完整信息：
{link}

论文元数据（供参考，以 arXiv 页面内容为准）：
- arXiv ID: {arxiv_id}
- 标题: {title}
- 摘要摘录: {abstract}

请按以下结构输出（全部用中文，直接输出正文，不要用 markdown 代码块包裹）：

**[开头段落，无标题]** 2-3 句话，点出论文核心贡献、论文名称（含 arXiv ID）、提交日期，以及作者所在机构（如能从 arXiv 页面找到）。

**一句话理解：** 用一句话抓住方法最关键的设计取舍或洞察，让读者在看细节之前先建立直觉。

**1. 这篇论文要解决什么问题**
问题背景和动机。列出现有方法的主要痛点（可分条），说明为什么这些痛点值得解决、在什么场景下会遇到。

**2. 方法核心**
介绍核心方法/系统/算法，用具体名词（模型名、模块名、关键数据结构）。解释关键设计决策背后的权衡（trade-off）。如果有特别巧妙或反直觉的地方，单独点出来。

**3. [根据论文内容自拟标题]**
深入分析关键机制或流程。可以是"查询时为什么快"、"训练流程"、"推理优化"、"系统执行路径"等，视论文内容决定。

**4. [根据论文内容自拟标题，内容不足时可省略此节]**
如论文有重要的第二个技术组件（如大规模场景、关键设计分析、并行/分布式策略等），在此展开；内容不充分时可省略。

**5. 实验结果怎么读**
- 列出数据集，注明是真实数据还是合成/半合成数据
- 列出对比方法，说明它们各自代表哪种技术路线
- 给出关键性能数字，同时提供解读这些数字所需的背景（baseline 的代表性、实验设定的边界）
- 如有消融实验或组件分析，提炼核心结论

**6. 这篇论文真正有价值的地方**
用"第一层、第二层、第三层"结构总结贡献，要有个人判断，不只是重复摘要。说明为什么这些贡献在当前领域背景下是有意义的。

**7. 这篇论文的局限，也要一起看**
诚实地列出至少 2-3 个局限：实验设定的局限、问题定义的边界条件、对读者实际使用场景的适用性说明。

**8. 适合怎么读这篇论文**
给出 3-4 个具体的阅读抓手，每个点说明为什么值得关注、大致对应论文的哪个部分。

---

写作要求：
- 全程中文；技术术语可保留英文（如 GPU、Transformer、HNSW 等）
- 有个人视角，可以说"我觉得"、"值得注意的是"
- 数字要具体，不要说"大幅提升"，要说具体倍数或数值
- 对实验结果批判性解读：什么数字可以直接信，什么数字需要结合背景理解
- 总字数大约 800-1200 字
- 文中引用 web 来源时，直接用 inline 链接（markdown 格式），不要单独列参考文献节；引用会由调用方单独提取
"""


def generate_intro(
    client: OpenAI,
    model: str,
    reasoning_effort: str,
    paper: Dict[str, Any],
) -> tuple[str, List[Dict[str, str]]]:
    response = client.responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        tools=[
            {
                "type": "web_search",
                "filters": {"allowed_domains": ["arxiv.org"]},
            }
        ],
        tool_choice="auto",
        include=["web_search_call.action.sources"],
        input=build_prompt(paper),
    )
    text = extract_text(response)
    citations = extract_citations(response)
    if not text:
        raise RuntimeError(f"No text returned for paper {paper.get('arxiv_id')}")
    return text, citations


def render_markdown(paper: Dict[str, Any], body: str, citations: List[Dict[str, str]]) -> str:
    lines = [
        f"# {paper.get('title', 'Untitled Paper')}",
        "",
        f"- arXiv: [{paper.get('arxiv_id', '')}]({paper.get('link', '')})",
        f"- Confidence: {paper.get('confidence', '')}",
        "",
        body.strip(),
    ]

    if citations:
        lines.extend(["", "## Sources", ""])
        for citation in citations:
            lines.append(f"- [{citation['title']}]({citation['url']})")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    papers = load_results(input_path)
    filtered = sort_papers(
        paper for paper in papers if float(paper.get("confidence") or 0.0) >= args.threshold
    )

    if not filtered:
        print(f"No papers matched threshold >= {args.threshold:.2f}")
        return

    client = OpenAI()

    for index, paper in enumerate(filtered, start=1):
        arxiv_id = str(paper.get("arxiv_id") or "unknown")
        title = str(paper.get("title") or "untitled")
        out_path = output_dir / safe_filename(arxiv_id, title)

        if out_path.exists() and not args.overwrite:
            print(f"[{index}/{len(filtered)}] Skipping existing {out_path.name}")
            continue

        print(f"[{index}/{len(filtered)}] Generating intro for {arxiv_id} {title}")
        body, citations = generate_intro(
            client=client,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            paper=paper,
        )
        rendered = render_markdown(paper, body, citations)

        out_path.write_text(rendered, encoding="utf-8")
        print(f"[{index}/{len(filtered)}] Wrote {out_path}")

        if index < len(filtered) and args.sleep > 0:
            time.sleep(args.sleep)


if __name__ == "__main__":
    main()
