"""命令行入口。

用法：
    python run.py ingest data/docs                    # 建立索引
    python run.py ask "为什么要分块？"                 # 提问
    python run.py ask "为什么要分块？" --no-llm        # 强制用抽取式（不花钱）
    python run.py eval                                # 跑检索评测
    python run.py eval-refusal                         # 跑拒答评测
    python run.py stats                               # 看索引统计
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows 控制台默认不是 UTF-8，这里强制一下，避免中文乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent

# 某些 Python 发行版默认开启 safe_path，不会把脚本目录加进 sys.path，
# 这里手动补上，保证 `from litrag import ...` 在任何环境下都能工作。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.answerer import answer
from litrag.chunker import chunk_documents
from litrag.evaluate import (
    load_eval_set,
    print_refusal_report,
    print_report,
    run_eval,
    run_refusal_eval,
)
from litrag.loader import load_dir
from litrag.retriever import Retriever

DEFAULT_INDEX = ROOT / ".index" / "index.json"


def cmd_ingest(args: argparse.Namespace) -> None:
    docs = load_dir(args.dir)
    if not docs:
        print(f"在 {args.dir} 下没有找到可读取的文档")
        return

    chunks = chunk_documents(docs, target_size=args.chunk_size, overlap_size=args.overlap)
    retriever = Retriever(chunks)
    retriever.save(args.index)

    print(f"已入库 {len(docs)} 篇文档，切成 {len(chunks)} 块")
    for doc in docs:
        print(f"  · {doc.doc_id}  （{sum(len(t) for _, t in doc.pages)} 字）")
    print(f"索引已保存到 {args.index}")


def _page_suffix(section: str, page: int | None) -> str:
    """页码后缀。PDF 来源的 chunk 已经把页码写进 section 了，避免重复显示。"""
    if not page:
        return ""
    if section == f"第 {page} 页":
        return ""
    return f"，第 {page} 页"


def cmd_ask(args: argparse.Namespace) -> None:
    retriever = Retriever.load(args.index)
    hits = retriever.search(args.question, k=args.k)

    print(f"\n问题：{args.question}\n")
    if not hits:
        print("没有召回任何内容。")
        return

    print("召回结果：")
    for i, hit in enumerate(hits, start=1):
        page = _page_suffix(hit.chunk.section, hit.chunk.page)
        print(f"  [{i}] {hit.chunk.doc_id} / {hit.chunk.section}{page}  分数={hit.score:.2f}")

    result = answer(args.question, hits, use_llm=not args.no_llm)

    print(f"\n回答（模式：{result.mode}）：\n")
    print(result.text)

    if result.citations:
        print("\n引用来源：")
        for c in result.citations:
            page = _page_suffix(c.section, c.page)
            print(f"  [{c.id}] {c.doc} / {c.section}{page}")
            print(f"      「{c.quote}」")

    if result.warning:
        print(f"\n⚠ {result.warning}")


def cmd_eval(args: argparse.Namespace) -> None:
    retriever = Retriever.load(args.index)
    items = load_eval_set(args.eval_set)
    report = run_eval(retriever, items)
    print_report(report)


def cmd_eval_refusal(args: argparse.Namespace) -> None:
    retriever = Retriever.load(args.index)
    items = load_eval_set(args.eval_set)
    report = run_refusal_eval(
        retriever,
        items,
        k=args.k,
        min_coverage=args.threshold,
        use_llm=args.llm,
    )
    print_refusal_report(report)


def cmd_stats(args: argparse.Namespace) -> None:
    retriever = Retriever.load(args.index)
    for key, value in retriever.stats().items():
        print(f"{key:>14}: {value}")


def cmd_check(args: argparse.Namespace) -> None:
    """体检一个文件：能不能读出文字、读出多少、内容长什么样。

    用途：下载一堆 PDF 之后，先用它逐个过一遍，把读不出来的挑掉，
    免得入库之后对着乱码查半天。
    """
    from litrag.loader import load_file

    try:
        doc = load_file(args.path)
    except Exception as exc:
        print(f"✗ 读不出来：{exc}")
        return

    total = sum(len(t) for _, t in doc.pages)
    print(f"文件: {doc.doc_id}")
    print(f"页数: {len(doc.pages)}   字符数: {total}")

    if total < 200:
        print("⚠ 字符数太少，多半是扫描件（整页是图片），没有文字层，换一个文件")
    elif total < 1000:
        print("⚠ 字符数偏少，可能只读出了部分内容，建议看一眼下面的预览")

    preview = next((t for _, t in doc.pages if t.strip()), "")
    print("\n前 300 字预览：")
    print(preview[:300])


def cmd_page(args: argparse.Namespace) -> None:
    """打印某一页的原文，用来直接读英文，不经过浏览器翻译。

    用法：
        python run.py page 2002.08909 6          # 按 arXiv 编号，读第 6 页
        python run.py page data/papers/x.pdf 3   # 也可以给完整路径
    """
    from litrag.loader import load_file

    target = Path(args.target)
    if not target.exists() and not args.target.lower().endswith(".pdf"):
        candidate = ROOT / "data" / "papers" / f"{args.target}.pdf"
        if candidate.exists():
            target = candidate

    if not target.exists():
        print(f"找不到文件：{args.target}")
        return

    doc = load_file(target)
    if not 1 <= args.number <= len(doc.pages):
        print(f"页码超出范围：这份文档共 {len(doc.pages)} 页")
        return

    page_no, text = doc.pages[args.number - 1]
    print(f"===== {doc.doc_id} 第 {page_no} 页 =====")
    print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="litrag —— 学术文献 RAG 助手")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="索引文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="把文档目录建立索引")
    p_ingest.add_argument("dir", help="文档目录")
    p_ingest.add_argument(
        "--chunk-size",
        type=int,
        default=200,
        dest="chunk_size",
        help="目标块长（字符）。默认 200 由评测得出，见 README 的参数实验",
    )
    p_ingest.add_argument("--overlap", type=int, default=60)
    p_ingest.set_defaults(func=cmd_ingest)

    p_ask = sub.add_parser("ask", help="提问")
    p_ask.add_argument("question")
    p_ask.add_argument("-k", type=int, default=5, help="召回条数")
    p_ask.add_argument("--no-llm", action="store_true", help="强制使用抽取式回答")
    p_ask.set_defaults(func=cmd_ask)

    p_eval = sub.add_parser("eval", help="跑评测集")
    p_eval.add_argument(
        "--eval-set", default=str(ROOT / "data" / "eval_set.jsonl"), dest="eval_set"
    )
    p_eval.set_defaults(func=cmd_eval)

    p_eval_refusal = sub.add_parser(
        "eval-refusal", help="在不可回答题集上测拒答率与误答率"
    )
    p_eval_refusal.add_argument(
        "--eval-set",
        default=str(ROOT / "data" / "eval_set_unanswerable.jsonl"),
        dest="eval_set",
    )
    p_eval_refusal.add_argument("-k", type=int, default=5, help="召回条数")
    p_eval_refusal.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="只按覆盖率判断的对照阈值；不填则用默认组合规则（覆盖率或分数）",
    )
    p_eval_refusal.add_argument(
        "--llm", action="store_true", help="使用 LLM 作答；默认走确定性抽取式"
    )
    p_eval_refusal.set_defaults(func=cmd_eval_refusal)

    p_stats = sub.add_parser("stats", help="查看索引统计")
    p_stats.set_defaults(func=cmd_stats)

    p_check = sub.add_parser("check", help="体检一个 PDF/TXT 能不能读出文字")
    p_check.add_argument("path", help="文件路径")
    p_check.set_defaults(func=cmd_check)

    p_page = sub.add_parser("page", help="打印某一页的原文（读英文用，绕过浏览器翻译）")
    p_page.add_argument("target", help="arXiv 编号或文件路径")
    p_page.add_argument("number", type=int, help="页码，从 1 开始")
    p_page.set_defaults(func=cmd_page)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.index = Path(args.index)
    args.func(args)


if __name__ == "__main__":
    main()
