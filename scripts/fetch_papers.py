"""批量下载 arXiv 论文 PDF 到 data/papers/。

用法：
    python scripts/fetch_papers.py                 # 下载 data/demo_papers.json 中的精选论文
    python scripts/fetch_papers.py --ids 2312.10997 2307.03172
    python scripts/fetch_papers.py --list          # 只看清单，不下载

说明：
    - 文件名统一用 arXiv 编号（如 2312.10997.pdf），因为 doc_id 就是文件名，
      评测集引用它、引用来源也显示它。
    - 已存在且大小正常的文件会跳过，可以反复执行。
    - 需要联网。离网环境请手动下载后放进 data/papers/。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "papers"
MANIFEST_PATH = ROOT / "data" / "demo_papers.json"


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"缺少演示论文清单：{MANIFEST_PATH}")
    items = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(items, list):
        raise ValueError("demo_papers.json 必须是列表")
    papers: list[dict] = []
    seen: set[str] = set()
    for item in items:
        arxiv_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not arxiv_id or not title or arxiv_id in seen:
            continue
        seen.add(arxiv_id)
        papers.append({**item, "id": arxiv_id, "title": title})
    papers.sort(key=lambda item: int(item.get("order") or 9999))
    return papers


def download(arxiv_id: str, title: str) -> bool:
    dest = OUT_DIR / f"{arxiv_id}.pdf"
    if dest.exists() and dest.stat().st_size > 10_000:
        print(f"跳过（已存在） {dest.name}")
        return True

    url = f"https://arxiv.org/pdf/{arxiv_id}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "litrag/0.1 (academic reading assistant)"}
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
    except Exception as exc:  # 网络不通、超时、404 都会到这里
        print(f"✗ {arxiv_id} 下载失败：{exc}")
        return False

    if not data.startswith(b"%PDF"):
        print(f"✗ {arxiv_id} 返回的不是 PDF（可能被重定向到网页）")
        return False

    dest.write_bytes(data)
    print(f"✓ {dest.name:20s} {len(data) / 1024:7.0f} KB  {title}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 arXiv 论文到 data/papers/")
    parser.add_argument("--ids", nargs="+", help="指定 arXiv 编号")
    parser.add_argument("--list", action="store_true", help="只列出默认清单")
    args = parser.parse_args()

    if args.list:
        for item in load_manifest():
            print(
                f"{item['id']:12s} {item.get('category', ''):12s} "
                f"{item.get('level', ''):6s} {item['title']}"
            )
        return

    manifest = {item["id"]: item for item in load_manifest()}
    if args.ids:
        targets = {
            arxiv_id: manifest.get(arxiv_id, {"id": arxiv_id, "title": ""})
            for arxiv_id in args.ids
        }
    else:
        targets = manifest

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ok = 0
    for arxiv_id, item in targets.items():
        if download(arxiv_id, item.get("title", "")):
            ok += 1

    print(f"\n完成 {ok}/{len(targets)}")
    if ok:
        print("下一步：")
        print("  python run.py check data/papers/<编号>.pdf")
        print("  python run.py ingest data")
    else:
        print("一篇都没下载成功。若本机不能联网，请手动下载 PDF 放进 data/papers/。")
        sys.exit(1)


if __name__ == "__main__":
    main()
