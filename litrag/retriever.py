"""检索器：把 Chunk 列表变成可查询的索引，并支持存盘 / 加载。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .bm25 import BM25Index, tokenize
from .chunker import Chunk


@dataclass
class Hit:
    chunk: Chunk
    score: float


class Retriever:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.bm25 = BM25Index()
        self.build()

    def build(self) -> None:
        # 关键细节：把 section 标题也拼进索引文本。
        # 因为 chunker 把标题存成了元数据，正文里并不包含它；
        # 而用户提问往往会用到标题里的词（比如「为什么要分块」），
        # 不索引标题就会漏召回。
        tokenized = [tokenize(f"{c.section}\n{c.text}") for c in self.chunks]
        self.bm25.fit(tokenized)

    def search(self, query: str, k: int = 5) -> list[Hit]:
        scores = self.bm25.score(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        hits = [
            Hit(self.chunks[i], scores[i])
            for i in order[:k]
            if scores[i] > 0  # 分数为 0 说明一个词都没命中，直接丢掉
        ]
        return hits

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "chunks": [c.to_dict() for c in self.chunks],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "Retriever":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"索引不存在：{path}\n请先执行：python run.py ingest data/docs"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        chunks = [Chunk(**item) for item in payload["chunks"]]
        return cls(chunks)

    def stats(self) -> dict:
        docs = {c.doc_id for c in self.chunks}
        return {
            "documents": len(docs),
            "chunks": len(self.chunks),
            "avg_chunk_len": round(
                sum(len(c.text) for c in self.chunks) / max(len(self.chunks), 1), 1
            ),
            "vocabulary": len(self.bm25.idf),
        }
