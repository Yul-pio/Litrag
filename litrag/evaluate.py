"""评测：用固定题集衡量检索质量。

这是整个项目里最重要、也最容易被复试老师认可的部分：
有评测，你才能说「改动 A 让 Recall@3 从 0.58 涨到 0.83」；
没有评测，你只能靠感觉，那叫玄学。

评测集格式（data/eval_set.jsonl，每行一个 JSON）：
    {"question": "为什么要分块？", "expected_doc": "02-chunking"}
"""

from __future__ import annotations

import json
from pathlib import Path

from .retriever import Retriever


def load_eval_set(path: str | Path) -> list[dict]:
    path = Path(path)
    items = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} 第 {line_no} 行不是合法 JSON：{exc}") from exc
    return items


def run_eval(
    retriever: Retriever,
    items: list[dict],
    k_values: tuple[int, ...] = (1, 3, 5),
) -> dict:
    max_k = max(k_values)
    hits_at = {k: 0 for k in k_values}
    reciprocal_ranks: list[float] = []
    rows: list[dict] = []

    for item in items:
        question = item["question"]
        expected = item.get("expected_doc")
        expected_section = item.get("expected_section")

        hits = retriever.search(question, k=max_k)

        # 命中判定支持两级：
        #   只标 expected_doc        → 文档级（宽松）
        #   同时标 expected_section  → 章节级（严格，更能反映真实检索质量）
        rank = None
        if expected:
            for i, hit in enumerate(hits, start=1):
                if hit.chunk.doc_id != expected:
                    continue
                if expected_section and hit.chunk.section != expected_section:
                    continue
                rank = i
                break

        for k in k_values:
            if rank is not None and rank <= k:
                hits_at[k] += 1
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)

        rows.append(
            {
                "question": question,
                "expected": f"{expected} / {expected_section}"
                if expected_section
                else expected,
                "rank": rank,
                "top": [f"{h.chunk.doc_id} / {h.chunk.section}" for h in hits[:3]],
            }
        )

    total = len(items) or 1
    return {
        "total": len(items),
        "recall": {k: hits_at[k] / total for k in k_values},
        "mrr": sum(reciprocal_ranks) / total,
        "rows": rows,
    }


def print_report(report: dict) -> None:
    print(f"\n评测题数：{report['total']}")
    print("-" * 56)
    for k, value in report["recall"].items():
        bar = "█" * int(value * 30)
        print(f"Recall@{k:<2} {value:6.1%}  {bar}")
    print(f"MRR       {report['mrr']:6.1%}")

    misses = [r for r in report["rows"] if r["rank"] is None]
    low = [r for r in report["rows"] if r["rank"] is not None and r["rank"] > 1]

    if low:
        print(f"\n召回到了、但没排第一的题（{len(low)} 题）：")
        for row in low:
            print(f"  · {row['question']}")
            print(f"      期望 {row['expected']}，实际第 {row['rank']} 位 → 首位是 {row['top'][0]}")

    if misses:
        print(f"\n完全没召回的题（{len(misses)} 题）：")
        for row in misses:
            print(f"  · {row['question']}")
            print(f"      期望 {row['expected']}，实际前三是 {row['top']}")
    elif not low:
        print("\n全部命中且都排第一。可以开始提高难度（同义改写、跨文档问题）。")
