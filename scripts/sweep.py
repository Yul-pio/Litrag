"""参数扫描：一次跑一组参数，直接打印对比表。

用法：
    python scripts/sweep.py --param overlap --values 0 60 120
    python scripts/sweep.py --param size --values 150 200 320

说明：
    - 直接在内存里重建索引并评测，不写 .index，所以很快，也不会打乱你当前的索引
    - 每组参数跑完输出 Recall@1 / Recall@3 / Recall@5 / MRR
    - 记得看「一道题值多少个百分点」：N 道题时，一道题 = 100/N 个百分点
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.chunker import chunk_documents  # noqa: E402
from litrag.evaluate import load_eval_set, run_eval  # noqa: E402
from litrag.loader import load_dir  # noqa: E402
from litrag.retriever import Retriever  # noqa: E402

EVAL_SET = ROOT / "data" / "eval_set_papers.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="参数扫描")
    parser.add_argument("--param", choices=["size", "overlap"], required=True)
    parser.add_argument("--values", nargs="+", type=int, required=True)
    parser.add_argument("--size", type=int, default=200, help="size 扫描时固定 overlap 用")
    parser.add_argument("--overlap", type=int, default=60, help="overlap 扫描时固定 size 用")
    args = parser.parse_args()

    docs = load_dir(ROOT / "data")
    items = load_eval_set(EVAL_SET)
    total = len(items)

    print(f"语料：{len(docs)} 篇文档   评测：{total} 题")
    print(f"一道题 = {100 / total:.1f} 个百分点\n")

    header = f"{'size':>6} {'overlap':>8} {'chunks':>8} {'R@1':>7} {'R@3':>7} {'R@5':>7} {'MRR':>7}"
    print(header)
    print("-" * len(header))

    best = None
    for value in args.values:
        size = value if args.param == "size" else args.size
        overlap = value if args.param == "overlap" else args.overlap

        chunks = chunk_documents(docs, target_size=size, overlap_size=overlap)
        report = run_eval(Retriever(chunks), items)
        recall = report["recall"]

        print(
            f"{size:>6} {overlap:>8} {len(chunks):>8} "
            f"{recall[1]:>6.1%} {recall[3]:>6.1%} {recall[5]:>6.1%} {report['mrr']:>6.1%}"
        )

        if best is None or recall[1] > best[2]:
            best = (size, overlap, recall[1])

    print(f"\n本组最优（按 Recall@1）：size={best[0]} overlap={best[1]}  R@1={best[2]:.1%}")
    print("落差小于 1 道题时，不要断言显著，只能记为「在噪声范围内」。")


if __name__ == "__main__":
    main()
