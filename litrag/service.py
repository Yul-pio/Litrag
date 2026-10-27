"""会话级文档库：每个浏览器会话维护自己的一份索引。

设计取舍：
    - 不做用户系统（登录/密码），用一个浏览器生成的随机 session id 区分。
      对演示够用；正式产品要换成真正的账号体系。
    - 首次提问且没上传过，就回落到全局演示库（5 篇 RAG 论文）。
    - 上传的 PDF 解析后立刻建索引并存盘，下次请求直接加载。

限制（防止免费额度被单个用户吃光）：
    - 每个会话最多 10 篇
    - 单文件 ≤ 8MB
    - 每个会话最多 4000 个块
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from .answerer import answer
from .chunker import Chunk, chunk_document
from .loader import load_file
from .retriever import Retriever

ROOT = Path(__file__).resolve().parents[1]
GLOBAL_INDEX = ROOT / ".index" / "index.json"
UPLOAD_ROOT = ROOT / "uploads"

SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_DOCS = 10
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_CHUNKS = 4000

_lock = threading.Lock()
_user_cache: dict[str, Retriever] = {}
_global_retriever: Retriever | None = None


# ---------------------------------------------------------------- 基础工具
def valid_session(sid: str | None) -> bool:
    return bool(sid and SESSION_RE.match(sid))


def _session_dir(sid: str) -> Path:
    return UPLOAD_ROOT / sid


def _index_path(sid: str) -> Path:
    return _session_dir(sid) / "docs.json"


def _load_meta(sid: str) -> dict:
    path = _index_path(sid)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {"docs": [], "chunks": []}
    return {"docs": [], "chunks": []}


def _save_meta(sid: str, meta: dict) -> None:
    _session_dir(sid).mkdir(parents=True, exist_ok=True)
    _index_path(sid).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------- 取索引
def _global() -> Retriever:
    global _global_retriever
    with _lock:
        if _global_retriever is None:
            _global_retriever = Retriever.load(GLOBAL_INDEX)
        return _global_retriever


def get_retriever(sid: str | None) -> tuple[Retriever, str]:
    """返回 (检索器, 来源)。来源是 'user' 或 'global'。"""
    if valid_session(sid) and _index_path(sid).exists():
        with _lock:
            if sid not in _user_cache:
                meta = _load_meta(sid)
                chunks = [Chunk(**c) for c in meta["chunks"]]
                _user_cache[sid] = Retriever(chunks)
            return _user_cache[sid], "user"
    return _global(), "global"


# ---------------------------------------------------------------- 上传
def add_document(sid: str | None, filename: str, data: bytes) -> dict:
    """保存并索引一个 PDF，返回统计信息。失败时抛 ValueError。"""
    if not valid_session(sid):
        raise ValueError("会话标识无效，请刷新页面重试")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"文件太大（上限 {MAX_FILE_BYTES // 1024 // 1024} MB）")
    if not data.startswith(b"%PDF"):
        raise ValueError("这不是 PDF 文件")

    meta = _load_meta(sid)
    if len(meta["docs"]) >= MAX_DOCS:
        raise ValueError(f"每个会话最多 {MAX_DOCS} 篇，删不了就刷新页面换一个会话")

    safe_name = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", Path(filename).name)[:80] or "upload.pdf"
    doc_id = Path(safe_name).stem

    # 重名时加后缀，避免覆盖
    existing = {d["doc_id"] for d in meta["docs"]}
    base, n = doc_id, 2
    while doc_id in existing:
        doc_id = f"{base}-{n}"
        n += 1

    target = _session_dir(sid) / f"{doc_id}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    try:
        doc = load_file(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise ValueError(f"读不出文字（可能是扫描件）：{exc}") from exc

    doc.doc_id = doc_id
    chunks = chunk_document(doc, target_size=200, overlap_size=60)
    if not chunks:
        target.unlink(missing_ok=True)
        raise ValueError("这个 PDF 没有可提取的文字（可能是扫描件）")

    if len(meta["chunks"]) + len(chunks) > MAX_CHUNKS:
        target.unlink(missing_ok=True)
        raise ValueError(f"这个会话的文档太多了（上限 {MAX_CHUNKS} 块），刷新页面换一个会话")

    meta["docs"].append({"doc_id": doc_id, "filename": safe_name, "chunks": len(chunks),
                         "chars": sum(len(t) for _, t in doc.pages)})
    meta["chunks"].extend(c.to_dict() for c in chunks)
    _save_meta(sid, meta)

    with _lock:
        _user_cache.pop(sid, None)  # 下次请求重新加载

    return {"doc_id": doc_id, "chunks": len(chunks),
            "chars": sum(len(t) for _, t in doc.pages), "total_docs": len(meta["docs"])}


def get_document_file(sid: str | None, doc_id: str) -> tuple[Path, str]:
    """返回当前会话有权访问的 PDF 路径和原始文件名。

    doc_id 必须来自会话元数据或全局索引入口，不能直接拼成任意路径。
    """
    if not valid_session(sid):
        raise ValueError("会话标识无效，请刷新页面重试")
    if not doc_id or len(doc_id) > 100:
        raise ValueError("文档标识无效")

    meta = _load_meta(sid)
    for item in meta["docs"]:
        if item["doc_id"] != doc_id:
            continue
        target = _session_dir(sid) / f"{doc_id}.pdf"
        if target.is_file() and target.resolve().parent == _session_dir(sid).resolve():
            return target, item.get("filename", target.name)
        raise ValueError("文档文件已丢失，请重新上传")

    # 全局演示库：只允许索引里真实存在的 doc_id。
    if any(chunk.doc_id == doc_id for chunk in _global().chunks):
        target = ROOT / "data" / "papers" / f"{doc_id}.pdf"
        if target.is_file():
            return target, target.name

    raise ValueError("找不到对应的 PDF 文件")


def clear_session(sid: str | None) -> dict:
    """删除当前会话的上传文件、索引和内存缓存。"""
    if not valid_session(sid):
        raise ValueError("会话标识无效，请刷新页面重试")

    root = UPLOAD_ROOT.resolve()
    target = _session_dir(sid).resolve()
    if target.parent != root:
        raise ValueError("会话路径无效")

    with _lock:
        _user_cache.pop(sid, None)
        if target.exists():
            shutil.rmtree(target)
    return {"ok": True, "message": "当前会话的文献库已清空"}


def list_documents(sid: str | None) -> dict:
    if not valid_session(sid):
        return {"source": "global", "docs": []}
    if not _index_path(sid).exists():
        return {"source": "global", "docs": []}
    meta = _load_meta(sid)
    return {"source": "user", "docs": meta["docs"]}


# ---------------------------------------------------------------- 提问
_QUESTION_MARKERS = (
    "?", "？",
    "什么", "怎么", "为什么", "如何", "哪", "是否", "多少", "几", "谁",
    "吗", "呢", "请问",
)
_EN_MARKERS = (
    "what", "why", "how", "which", "who", "when", "where", "does", "do ",
    "is ", "are ", "can ", "could ", "should ", "explain", "compare",
)


def _looks_like_question(text: str) -> bool:
    """判断输入是不是一个"问题"，而不是一堆关键词。

    抽取式回答对关键词几乎无能为力：它会挑出所有含这个词的句子，
    看起来就像在胡扯。与其硬答，不如提示用户换个问法。
    """
    lowered = text.lower()
    if any(mark in text for mark in _QUESTION_MARKERS):
        return True
    if any(marker in lowered for marker in _EN_MARKERS):
        return True
    # 很长的输入通常是在描述问题，即使没有问号
    return len(text) >= 20


def ask(question: str, sid: str | None) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    if not _looks_like_question(question):
        return {
            "answer": (
                f"「{question}」看起来是关键词，不是一个问题。\n\n"
                "这个工具只会从原文里摘句子，不会归纳。试着问完整一点，例如：\n"
                "· 这份报告的职业目标是什么？\n"
                "· 作者的指导老师是谁？\n"
                "· What are the three stages of RAG?"
            ),
            "mode": "hint",
            "warning": None,
            "source": "hint",
            "citations": [],
        }

    retriever, source = get_retriever(sid)
    hits = retriever.search(question, k=5)
    result = answer(question, hits, use_llm=True)
    return {
        "answer": result.text,
        "mode": result.mode,
        "warning": result.warning,
        "source": source,
        "citations": [c.__dict__ for c in result.citations],
    }
