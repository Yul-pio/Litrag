"""文档加载：把 txt / md / pdf 读成统一的 Document。

Document.pages 是一个 [(页码, 文本), ...] 列表：
- txt / md 只有一个元素，页码是 None
- pdf 每页一个元素，页码从 1 开始（引用溯源时要用到）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = {".txt", ".md", ".markdown", ".pdf"}


@dataclass
class Document:
    doc_id: str          # 用文件名（不含扩展名）做 id，方便评测时标注
    title: str
    path: str
    pages: list[tuple[int | None, str]]


def _guess_title(text: str, fallback: str) -> str:
    """优先取第一个 Markdown 标题做文档名。"""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
        if line:
            break
    return fallback


def _read_pdf(path: Path) -> list[tuple[int, str]]:
    """按「解析质量」从高到低尝试四个后端。

    降级链：
      1. PyMuPDF      最快、排版还原最好
      2. pdfplumber   慢一些，版面分析不错
      3. pypdf        纯 Python，效果一般
      4. pdf_text.py  本项目自带的纯 Python 提取器（零依赖，离线可用）

    第 4 个后端是为了「机器上不了外网、装不了包」的情况准备的。
    它的能力有限（扫描件、无 ToUnicode 的 CID 字体都读不出来），
    但能保证项目在最差的环境里也能跑起来。
    """
    errors: list[str] = []

    # 1) PyMuPDF：速度最快、排版还原最好，推荐
    try:
        import fitz  # PyMuPDF 的模块名

        pages = []
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc, start=1):
                pages.append((i, page.get_text("text") or ""))
        return pages
    except ImportError:
        errors.append("pymupdf 未安装")
    except Exception as exc:
        errors.append(f"pymupdf 失败：{exc}")

    # 2) pdfplumber：慢一些，但版面分析不错
    try:
        import pdfplumber

        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                pages.append((i, page.extract_text() or ""))
        return pages
    except ImportError:
        errors.append("pdfplumber 未安装")
    except Exception as exc:
        errors.append(f"pdfplumber 失败：{exc}")

    # 3) pypdf：兜底
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, start=1)]
        return pages
    except ImportError:
        errors.append("pypdf 未安装")
    except Exception as exc:
        errors.append(f"pypdf 失败：{exc}")

    # 4) 自带的纯 Python 提取器：没有第三方依赖，离线也能用
    try:
        from .pdf_text import extract_pdf_text

        pages = extract_pdf_text(path)
        if pages:
            return pages
        errors.append("内置提取器没有找到文本层（可能是扫描件）")
    except Exception as exc:
        errors.append(f"内置提取器失败：{exc}")

    raise RuntimeError("这个 PDF 读不出来。尝试记录：\n  - " + "\n  - ".join(errors))


def load_file(path: str | Path) -> Document:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"暂不支持的文件类型：{suffix}")

    if suffix == ".pdf":
        pages = _read_pdf(path)
        first_text = pages[0][1] if pages else ""
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
        pages = [(None, text)]
        first_text = text

    return Document(
        doc_id=path.stem,
        title=_guess_title(first_text, path.stem),
        path=str(path),
        pages=pages,
    )


def load_dir(dir_path: str | Path) -> list[Document]:
    """递归读取目录下所有受支持的文档，按文件名排序保证结果可复现。"""
    root = Path(dir_path)
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED
    )
    docs = []
    for f in files:
        try:
            docs.append(load_file(f))
        except Exception as exc:  # 单个文件失败不影响整体
            print(f"[跳过] {f.name}: {exc}")
    return docs
