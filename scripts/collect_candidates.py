"""运行单个学校的内容采集器，输出待审核候选。

用法：
    python scripts/collect_candidates.py --school pku --year 2027
    python scripts/collect_candidates.py --school pku --year 2027 --college 软件与微电子学院
    python scripts/collect_candidates.py --school pku --html tests/fixtures/pku_2027_catalog.html

采集器只写 data/candidates/，不修改正式 data/*.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collectors.pku import collect_pku_catalog  # noqa: E402

COLLECTORS = {"pku": collect_pku_catalog}


def main() -> int:
    parser = argparse.ArgumentParser(description="采集学校招生目录候选")
    parser.add_argument("--school", choices=sorted(COLLECTORS), required=True)
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--college", default=None, help="只保留包含该学院名称的链接")
    parser.add_argument("--html", type=Path, default=None, help="使用本地 HTML 文件进行离线测试")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    html = args.html.read_text(encoding="utf-8") if args.html else None
    collector = COLLECTORS[args.school]
    result = collector(year=args.year, html=html, college_filter=args.college)

    output = args.output or ROOT / "data" / "candidates" / f"{args.school}_{args.year}_catalog.json"
    output = output if output.is_absolute() else ROOT / output
    print(f"{args.school} {args.year}: {result['count']} 个候选")
    for item in result["items"][:10]:
        print(f"  - {item['college']}  {item['source_url']}")
    if result["count"] > 10:
        print(f"  ... 其余 {result['count'] - 10} 条")
    if args.dry_run:
        print("dry-run：未写入文件")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"候选文件：{output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())