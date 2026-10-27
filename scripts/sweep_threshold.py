"""扫描拒答阈值，观察“能答”和“该拒答”之间的权衡。

用法：
    python scripts/sweep_threshold.py
    python scripts/sweep_threshold.py --values 0.1 0.2 0.3 0.4 0.5 0.6 0.7

注意：这是开发集分析工具。最终测试应固定阈值后再跑一次 hold-out，
不要反复根据 hold-out 调参。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.answerer import _coverage  # noqa: E402
from litrag.evaluate import load_eval_set  # noqa: E402
from litrag.retriever import Retriever  # noqa: E402

DEFAULT_INDEX = ROOT / ".index" / "index.json"
ANSWERABLE = ROOT / "data" / "eval_set_holdout.jsonl"
UNANSWERABLE = ROOT / "data" / "eval_set_unanswerable.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="拒答阈值扫描")
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--answerable", default=str(ANSWERABLE))
    parser.add_argument("--unanswerable", default=str(UNANSWERABLE))
    parser.add_argument(
        "--values",
        nargs="+",
        type=float,
        default=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    )
    args = parser.parse_args()

    retriever = Retriever.load(args.index)
    answerable = load_eval_set(args.answerable)
    unanswerable = load_eval_set(args.unanswerable)

    answerable_coverages = [
        _coverage(item["question"], retriever.search(item["question"], k=5))
        for item in answerable
    ]
    unanswerable_coverages = [
        _coverage(item["question"], retriever.search(item["question"], k=5))
        for item in unanswerable
    ]

    header = f"{'threshold':>10} {'answerable_pass':>16} {'unanswerable_false':>19} {'balanced_acc':>13}"
    print(f"answerable={len(answerable)}  unanswerable={len(unanswerable)}")
    print(header)
    print("-" * len(header))
    for threshold in args.values:
        answerable_pass = sum(v >= threshold for v in answerable_coverages) / len(
            answerable_coverages
        )
        false_answer = sum(v >= threshold for v in unanswerable_coverages) / len(
            unanswerable_coverages
        )
        balanced = (answerable_pass + (1 - false_answer)) / 2
        print(
            f"{threshold:>10.2f} {answerable_pass:>15.1%} "
            f"{false_answer:>18.1%} {balanced:>12.1%}"
        )

    print("\n选择规则：先保证答题不被大量误拒，再看误答率；不要只追求最低误答率。")
    print("阈值确定后，应只跑一次 hold-out，避免把测试集变成调参集。")


if __name__ == "__main__":
    main()
