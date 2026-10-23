"""冒烟测试：保证核心链路没被改坏。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.answerer import answer
from litrag.bm25 import content_terms, tokenize
from litrag.chunker import chunk_document
from litrag.loader import Document, load_dir
from litrag.retriever import Retriever

DOCS_DIR = ROOT / "data" / "docs"


class TestTokenizer(unittest.TestCase):
    def test_chinese_bigrams(self):
        tokens = tokenize("分块策略")
        self.assertIn("分块", tokens)   # 双字
        self.assertIn("分", tokens)     # 单字

    def test_latin_words(self):
        self.assertIn("rag", tokenize("RAG 系统"))

    def test_content_terms_drop_single_chars(self):
        terms = content_terms("分块")
        self.assertEqual(terms, ["分块"])


class TestChunker(unittest.TestCase):
    def test_sections_become_metadata(self):
        doc = Document(
            doc_id="t",
            title="测试",
            path="t.md",
            pages=[(None, "# 第一节\n\n甲甲甲的正文内容。\n\n# 第二节\n\n乙乙乙的正文内容。")],
        )
        chunks = chunk_document(doc, target_size=100)
        sections = [c.section for c in chunks]
        self.assertEqual(sections, ["第一节", "第二节"])
        # 每个块只能包含自己那一节的正文
        self.assertNotIn("乙乙乙", chunks[0].text)
        self.assertNotIn("甲甲甲", chunks[1].text)


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        docs = load_dir(DOCS_DIR)
        cls.retriever = Retriever(
            [c for d in docs for c in chunk_document(d, target_size=320, overlap_size=60)]
        )

    def test_retrieves_relevant_section(self):
        hits = self.retriever.search("为什么相邻块之间要保留重叠？", k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.doc_id, "02-chunking")

    def test_answer_has_valid_citations(self):
        hits = self.retriever.search("为什么要分块？", k=3)
        result = answer("为什么要分块？", hits, use_llm=False)
        self.assertEqual(result.mode, "extractive")
        self.assertTrue(result.citations)
        for citation in result.citations:
            self.assertGreaterEqual(citation.id, 1)
            self.assertLessEqual(citation.id, len(hits))

    def test_refuses_irrelevant_question(self):
        hits = self.retriever.search("今天上海天气怎么样？", k=3)
        result = answer("今天上海天气怎么样？", hits, use_llm=False)
        self.assertEqual(result.mode, "none")
        self.assertEqual(result.citations, [])


if __name__ == "__main__":
    unittest.main()
