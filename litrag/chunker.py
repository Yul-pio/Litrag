"""分块：把文档切成带元数据的小块。

这里用的是「按标题分节 + 按段落合并 + 相邻重叠」的策略，而不是固定字数硬切：
- 按标题分节：保证一块不会横跨两个主题
- 按段落合并：保证句子不被从中间截断
- 相邻重叠：缓解边界处语义被切断的问题

块上保留 section / page 元数据，是为了后面做「引用溯源」时能告诉用户
这句话来自哪一篇、哪一节、第几页。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .loader import Document

_PARA_SPLIT = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    page: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _split_blocks(text: str) -> list[tuple[str, str]]:
    """把文本拆成 (类型, 内容)，类型是 'heading' 或 'para'。"""
    blocks: list[tuple[str, str]] = []
    for raw in _PARA_SPLIT.split(text):
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("#"):
            blocks.append(("heading", raw.lstrip("#").strip()))
        else:
            blocks.append(("para", raw))
    return blocks


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|(?<=\.)\s+")


def _split_long_paragraph(text: str, target_size: int) -> list[str]:
    """把超长段落按句子切开，避免出现上万字的巨型 chunk。"""
    if len(text) <= target_size * 1.5:
        return [text]

    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence:
            continue
        if len(current) + len(sentence) > target_size and current:
            pieces.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces or [text]


def _overlap_tail(text: str, overlap_size: int) -> str:
    """返回用于下一块开头的真实文本尾部。

    旧实现只保留最后一个段落，而且段落长度超过 overlap_size 时会完全
    丢掉重叠。这里改为按字符窗口取尾部，并尽量对齐到句子边界，保证
    ``overlap_size`` 参数真的对应“最多保留多少字符”。
    """
    if overlap_size <= 0 or not text:
        return ""

    tail = text[-overlap_size:].strip()
    if len(text) <= overlap_size or not tail:
        return tail

    # 优先从句末标点之后开始，避免下一块从半句话开始。
    boundary = re.search(r"[。！？!?；;]\s*|(?<=\.)\s+|\n+", tail)
    if boundary and boundary.end() < len(tail):
        candidate = tail[boundary.end():].strip()
        if candidate:
            return candidate

    # 英文没有明显句末标点时，避免从单词中间开始。
    if " " in tail:
        first_space = tail.find(" ")
        if 0 < first_space < len(tail) - 1:
            candidate = tail[first_space + 1:].strip()
            if candidate:
                return candidate

    return tail


def chunk_document(
    doc: Document,
    target_size: int = 200,
    overlap_size: int = 60,
) -> list[Chunk]:
    """把一个 Document 切成若干 Chunk。

    target_size 的默认值 200 不是拍脑袋定的：在 46 题评测集上做过
    150/200/250/320/500/800 六组对照，200 的 Recall@1 最高。详见 README。

    target_size  ：目标块长（字符数）
    overlap_size ：相邻块保留的重叠长度
    """
    chunks: list[Chunk] = []

    for page, page_text in doc.pages:
        section = ""
        buffer: list[str] = []
        buffer_len = 0

        def flush(keep_tail: bool = False) -> None:
            nonlocal buffer, buffer_len
            if not buffer:
                return
            text = "\n".join(buffer).strip()
            tail = _overlap_tail(text, overlap_size) if keep_tail else ""
            if text:
                # 没有 Markdown 标题时（比如从 PDF 来的文本），
                # 用页码当 section，至少比显示文件名更有信息量
                label = section or (f"第 {page} 页" if page else doc.title)
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc.doc_id}#c{len(chunks)}",
                        doc_id=doc.doc_id,
                        title=doc.title,
                        section=label,
                        text=text,
                        page=page,
                    )
                )
            if tail:
                buffer, buffer_len = [tail], len(tail)
            else:
                buffer, buffer_len = [], 0

        for kind, content in _split_blocks(page_text):
            if kind == "heading":
                # 换节时先落盘，避免一块横跨两节
                flush()
                section = content
                continue

            # PDF 文本经常整页就是一个大段落，必须再按句子切开，
            # 否则一块上万字，检索精度会被严重稀释。
            for piece in _split_long_paragraph(content, target_size):
                if buffer_len + len(piece) > target_size and buffer:
                    flush(keep_tail=True)
                buffer.append(piece)
                buffer_len += len(piece)

        flush()

    return chunks


def chunk_documents(docs: list[Document], **kwargs) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(chunk_document(doc, **kwargs))
    return chunks
