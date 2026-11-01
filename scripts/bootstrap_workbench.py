"""下载精选演示论文并重建全局工作台索引。

PDF 不提交到 Git。部署后运行一次：
    python scripts/bootstrap_workbench.py

只重建已有 PDF 的索引：
    python scripts/bootstrap_workbench.py --skip-download
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.chunker import chunk_documents  # noqa: E402
from litrag.loader import load_dir  # noqa: E402
from litrag.retriever import Retriever  # noqa: E402
from scripts.fetch_papers import OUT_DIR, download, load_manifest  # noqa: E402

DEFAULT_INDEX = ROOT / ".index" / "index.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="下载演示论文并构建工作台索引")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--overlap", type=int, default=60)
    args = parser.parse_args()

    manifest = load_manifest()
    if not args.skip_download:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        for item in manifest:
            if download(item["id"], item["title"]):
                downloaded += 1
            time.sleep(1.5)
        print(f"演示论文可用：{downloaded}/{len(manifest)}")
        if downloaded == 0:
            print(
                "没有成功获得任何精选论文。请先运行单篇诊断：\n"
                "  python scripts/fetch_papers.py --ids 2312.10997 --retry 1",
                file=sys.stderr,
            )

    docs = load_dir(ROOT / "data")
    if not docs:
        print("没有找到可索引的文档", file=sys.stderr)
        return 1
    metadata = {item["id"]: item for item in manifest}
    for doc in docs:
        if doc.doc_id in metadata:
            doc.title = metadata[doc.doc_id].get("title") or doc.title
    chunks = chunk_documents(docs, target_size=args.chunk_size, overlap_size=args.overlap)
    retriever = Retriever(chunks)
    retriever.save(args.index)
    print(f"已建立工作台索引：{len(docs)} 篇文档，{len(chunks)} 块")
    print(f"索引文件：{args.index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
