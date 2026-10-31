"""冒烟测试：保证核心链路没被改坏。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.answerer import answer
from litrag.bm25 import content_terms, tokenize
from litrag.chunker import chunk_document
from litrag.evaluate import run_refusal_eval
from litrag.loader import Document, load_dir
from litrag.query import build_query_plan
from litrag import service
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

    def test_content_terms_drop_question_words(self):
        terms = content_terms("What are the three stages of RAG?")
        self.assertIn("stages", terms)
        self.assertIn("rag", terms)
        self.assertNotIn("what", terms)
        self.assertNotIn("the", terms)

        zh_terms = content_terms("为什么要分块？")
        self.assertIn("分块", zh_terms)
        self.assertNotIn("什么", zh_terms)

    def test_chinese_query_plan_adds_english_terms(self):
        plan = build_query_plan("RAG 的三个阶段是什么？")
        self.assertTrue(plan.translated)
        self.assertIn("three", plan.expanded)
        self.assertIn("stages", plan.expanded)

    def test_english_query_is_unchanged(self):
        plan = build_query_plan("What are the three stages of RAG?")
        self.assertFalse(plan.translated)
        self.assertEqual(plan.expanded, plan.original)


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

    def test_overlap_is_a_real_suffix(self):
        doc = Document(
            doc_id="overlap",
            title="overlap",
            path="overlap.md",
            pages=[(None, "甲" * 100 + "。" + "乙" * 100 + "。")],
        )
        chunks = chunk_document(doc, target_size=100, overlap_size=25)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(chunks[1].text.startswith(chunks[0].text[-25:]))


class TestRadar(unittest.TestCase):
    def test_radar_items_are_available(self):
        data = service.list_radar_items()
        self.assertGreaterEqual(data["count"], 5)
        self.assertEqual(data["count"], len(data["items"]))
        self.assertTrue(any(item.get("doc_id") for item in data["items"]))


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
        self.assertTrue(result.headline)
        self.assertIn(result.strength, {"strong", "medium"})
        for citation in result.citations:
            self.assertGreaterEqual(citation.id, 1)
            self.assertLessEqual(citation.id, len(hits))

    def test_refuses_irrelevant_question(self):
        hits = self.retriever.search("今天上海天气怎么样？", k=3)
        result = answer("今天上海天气怎么样？", hits, use_llm=False)
        self.assertEqual(result.mode, "none")
        self.assertEqual(result.citations, [])

    def test_refusal_eval_counts_false_answers(self):
        report = run_refusal_eval(
            self.retriever,
            [
                {"question": "今天上海天气怎么样？"},
                {"question": "What is the capital city of Australia?"},
            ],
            min_coverage=0.2,
        )
        self.assertEqual(report["total"], 2)
        self.assertGreaterEqual(report["refused"], 1)
        self.assertLessEqual(report["false_answer_rate"], 0.5)


class TestSessionFiles(unittest.TestCase):
    def test_read_and_clear_session_file(self):
        # 用项目内的临时目录，而不是系统临时目录：
        # 有些受限环境（沙箱、只读系统盘）不允许往 %TEMP% 里写，
        # 但这跟被测代码没关系，不该让测试因此失败。
        base = ROOT / "tmp" / "test-session-root"
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        try:
            with patch.object(service, "UPLOAD_ROOT", base):
                sid = "test-session-123"
                service._save_meta(
                    sid,
                    {
                        "docs": [
                            {"doc_id": "paper", "filename": "paper.pdf", "chunks": 1, "chars": 9}
                        ],
                        "chunks": [],
                    },
                )
                target = service._session_dir(sid) / "paper.pdf"
                target.write_bytes(b"%PDF-1.4 test")
                path, filename = service.get_document_file(sid, "paper")
                self.assertEqual(path, target)
                self.assertEqual(filename, "paper.pdf")

                service.clear_session(sid)
                self.assertFalse(service._session_dir(sid).exists())
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_delete_single_session_document(self):
        base = ROOT / "tmp" / "test-delete-root"
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        try:
            with patch.object(service, "UPLOAD_ROOT", base):
                sid = "test-delete-123"
                service._save_meta(
                    sid,
                    {
                        "docs": [
                            {"doc_id": "keep", "filename": "keep.pdf", "chunks": 1, "chars": 9},
                            {"doc_id": "drop", "filename": "drop.pdf", "chunks": 1, "chars": 9},
                        ],
                        "chunks": [
                            {"chunk_id": "keep#c0", "doc_id": "keep"},
                            {"chunk_id": "drop#c0", "doc_id": "drop"},
                        ],
                    },
                )
                (service._session_dir(sid) / "keep.pdf").write_bytes(b"%PDF keep")
                (service._session_dir(sid) / "drop.pdf").write_bytes(b"%PDF drop")
                result = service.delete_document(sid, "drop")
                self.assertEqual(result["remaining_docs"], 1)
                self.assertFalse((service._session_dir(sid) / "drop.pdf").exists())
                self.assertTrue((service._session_dir(sid) / "keep.pdf").exists())
                meta = service._load_meta(sid)
                self.assertEqual([d["doc_id"] for d in meta["docs"]], ["keep"])
                self.assertEqual([c["chunk_id"] for c in meta["chunks"]], ["keep#c0"])
                service.delete_document(sid, "keep")
                self.assertEqual(service.list_documents(sid)["source"], "global")
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_expired_session_is_purged(self):
        base = ROOT / "tmp" / "test-expire-root"
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        try:
            with patch.object(service, "UPLOAD_ROOT", base), patch.object(service, "SESSION_TTL_SECONDS", -1):
                sid = "test-expire-123"
                service._save_meta(sid, {"docs": [], "chunks": []})
                removed = service._purge_expired_sessions(force=True)
                self.assertEqual(removed, 1)
                self.assertFalse(service._session_dir(sid).exists())
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
