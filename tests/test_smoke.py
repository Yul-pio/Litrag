"""冒烟测试：保证核心链路没被改坏。

运行：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import sys
import unittest
from copy import deepcopy
import json
import os
import tempfile
from datetime import datetime, timezone
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
from litrag import admin, change_events, service
from scripts.collectors.pku import collect_pku_catalog
from scripts.collectors.tsinghua import collect_tsinghua_catalog
from scripts.promote_candidate import apply_review, build_records, draft_review, validate_review
from scripts.review_candidates import render_markdown, review_candidates
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


class TestCollectors(unittest.TestCase):
    def test_pku_catalog_link_is_candidate(self):
        html = (ROOT / "tests" / "fixtures" / "pku_2027_catalog.html").read_text(encoding="utf-8")
        result = collect_pku_catalog(year=2027, html=html, college_filter="软件与微电子学院")
        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["source_id"], "pku-grad")
        self.assertEqual(item["college"], "软件与微电子学院")
        self.assertTrue(item["source_url"].endswith("zsml_ss_yx_00017.pdf"))

    def test_tsinghua_html_catalog_becomes_review_candidates(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        result = collect_tsinghua_catalog(
            year=2027,
            html=html,
            college_filter="计算机科学与技术系",
            fetched_at="2026-11-01T00:00:00+00:00",
        )
        self.assertEqual(result["count"], 2)
        by_direction = {
            item["program"]["direction_code"]: item for item in result["items"]
        }
        item = by_direction["01"]
        self.assertEqual(item["status"], "candidate")
        self.assertEqual(item["source_id"], "tsinghua-grad")
        self.assertEqual(item["source_kind"], "HTML 招生目录")
        self.assertEqual(item["program"]["major_code"], "085400")
        self.assertEqual(item["program"]["plan"], 13)
        self.assertEqual(
            item["program"]["exam_subjects"],
            [
                "101 思想政治理论",
                "201 英语（一）",
                "301 数学（一）",
                "408 计算机学科专业基础",
            ],
        )
        self.assertEqual(
            by_direction["03"]["program"]["exam_subjects"],
            item["program"]["exam_subjects"],
        )
        self.assertIn("强军计划", by_direction["03"]["raw"]["notes"])

        review = draft_review(item)
        self.assertEqual(review["status"], "draft")
        self.assertEqual(review["program"]["major_code"], "085400")
        self.assertEqual(review["program"]["direction_code"], "01")

    def test_tsinghua_year_mismatch_is_rejected(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "页面年份 2027"):
            collect_tsinghua_catalog(year=2028, html=html)


class TestCandidatePromotion(unittest.TestCase):
    def test_candidate_can_be_promoted_after_review(self):
        html = (ROOT / "tests" / "fixtures" / "pku_2027_catalog.html").read_text(encoding="utf-8")
        candidate = collect_pku_catalog(year=2027, html=html, college_filter="软件与微电子学院")["items"][0]
        review = draft_review(candidate)
        review.update({
            "status": "verified",
            "verified_at": "2026-10-31",
            "summary": "测试摘要",
            "why": "测试推荐理由",
            "undergrad": "测试使用方式",
            "audiences": ["大四"],
            "feed_date": "2026-10-31",
            "feed_time": "12:00",
        })
        review["program"].update({
            "major_code": "085400",
            "major_name": "电子信息",
            "degree_type": "专硕",
            "study_mode": "全日制",
            "direction_code": "01",
            "direction_name": "软件工程",
            "exam_subjects": ["101 思想政治理论"],
            "verified_at": "2026-10-31",
        })
        validate_review(review, candidate)
        program, admission = build_records(review)
        self.assertEqual(program["major_code"], "085400")
        self.assertEqual(admission["program_ids"], [program["id"]])
        self.assertEqual(admission["source_url"], candidate["source_url"])

    def test_new_direction_appends_to_existing_admission(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        payload = collect_tsinghua_catalog(year=2027, html=html, college_filter="计算机科学与技术系")
        candidate = next(
            item for item in payload["items"]
            if item["program"]["direction_code"] == "03"
        )
        programs = deepcopy(json.loads((ROOT / "data" / "programs.json").read_text(encoding="utf-8")))
        admissions = deepcopy(json.loads((ROOT / "data" / "admission_records.json").read_text(encoding="utf-8")))

        review = draft_review(candidate, admissions)
        self.assertEqual(review["admission_action"], "append")
        self.assertEqual(review["id"], "thu-cs-2027-catalog")
        self.assertEqual(review["admission_id"], "thu-cs-2027-catalog")

        review["status"] = "verified"
        review["verified_at"] = "2026-11-01"
        review["program"]["verified_at"] = "2026-11-01"
        validate_review(review, candidate)
        program, admission = apply_review(review, candidate, programs, admissions)

        self.assertEqual(admission["id"], "thu-cs-2027-catalog")
        self.assertIn(program["id"], admission["program_ids"])
        self.assertEqual(len(admissions), 3)
        self.assertTrue(any(item["id"] == program["id"] for item in programs))

    def test_source_url_mismatch_is_rejected(self):
        html = (ROOT / "tests" / "fixtures" / "pku_2027_catalog.html").read_text(encoding="utf-8")
        candidate = collect_pku_catalog(year=2027, html=html, college_filter="软件与微电子学院")["items"][0]
        review = draft_review(candidate)
        review["source_url"] = "https://example.com/wrong.pdf"
        with self.assertRaises(ValueError):
            validate_review(review, candidate)


class TestCandidateReview(unittest.TestCase):
    def setUp(self):
        self.programs = json.loads((ROOT / "data" / "programs.json").read_text(encoding="utf-8"))
        self.admissions = json.loads((ROOT / "data" / "admission_records.json").read_text(encoding="utf-8"))

    def test_tsinghua_review_detects_new_direction_and_changed_notes(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        payload = collect_tsinghua_catalog(year=2027, html=html, college_filter="计算机科学与技术系")

        report = review_candidates(payload, self.programs, self.admissions)
        by_direction = {item["direction_code"]: item for item in report["items"]}

        self.assertEqual(report["summary"]["new"], 1)
        self.assertEqual(report["summary"]["changed"], 1)
        self.assertEqual(by_direction["01"]["status"], "changed")
        self.assertIn("program.notes", {item["field"] for item in by_direction["01"]["changes"]})
        self.assertEqual(by_direction["03"]["status"], "new")
        self.assertEqual(by_direction["03"]["admission_id"], "thu-cs-2027-catalog")
        self.assertTrue(by_direction["03"]["existing_document"])

    def test_plan_change_is_high_risk(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        payload = collect_tsinghua_catalog(year=2027, html=html, college_filter="计算机科学与技术系")
        payload = deepcopy(payload)
        payload["items"][0]["program"]["plan"] = 14

        report = review_candidates(payload, self.programs, self.admissions)
        item = next(item for item in report["items"] if item["direction_code"] == "01")
        plan_change = next(change for change in item["changes"] if change["field"] == "program.plan")

        self.assertEqual(item["status"], "changed")
        self.assertEqual(plan_change["level"], "high")
        self.assertEqual(plan_change["formal"], 13)
        self.assertEqual(plan_change["candidate"], 14)
        self.assertIn("高风险", render_markdown(report))

    def test_pku_document_candidate_matches_by_source_url(self):
        html = (ROOT / "tests" / "fixtures" / "pku_2027_catalog.html").read_text(encoding="utf-8")
        payload = collect_pku_catalog(year=2027, html=html, college_filter="软件与微电子学院")

        report = review_candidates(payload, self.programs, self.admissions)

        self.assertEqual(report["summary"]["candidate_count"], 1)
        self.assertEqual(report["summary"]["unchanged"], 1)
        self.assertEqual(report["items"][0]["admission_id"], "pku-softmicro-2027-catalog")
        self.assertEqual(report["items"][0]["risk"], "none")


class TestChangeEvents(unittest.TestCase):
    def test_new_direction_event_is_user_visible(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        candidate = next(
            item for item in collect_tsinghua_catalog(
                year=2027, html=html, college_filter="计算机科学与技术系"
            )["items"]
            if item["program"]["direction_code"] == "03"
        )
        admissions = json.loads((ROOT / "data" / "admission_records.json").read_text(encoding="utf-8"))
        admission = next(item for item in admissions if item["id"] == "thu-cs-2027-catalog")

        events = change_events.build_change_events(
            None,
            candidate["program"],
            admission,
            event_time=datetime(2026, 11, 1, 10, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "direction_added")
        self.assertTrue(events[0]["user_visible"])
        self.assertEqual(events[0]["program_id"], "thu-2027-024-085400-03-program")
        self.assertEqual(events[0]["admission_id"], "thu-cs-2027-catalog")

    def test_notes_only_change_stays_out_of_public_feed(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        candidate = next(
            item for item in collect_tsinghua_catalog(
                year=2027, html=html, college_filter="计算机科学与技术系"
            )["items"]
            if item["program"]["direction_code"] == "01"
        )
        programs = json.loads((ROOT / "data" / "programs.json").read_text(encoding="utf-8"))
        old_program = next(item for item in programs if item["id"] == "thu-cs-2027-085400-01")
        admissions = json.loads((ROOT / "data" / "admission_records.json").read_text(encoding="utf-8"))
        admission = next(item for item in admissions if item["id"] == "thu-cs-2027-catalog")

        events = change_events.build_change_events(
            old_program,
            candidate["program"],
            admission,
            event_time=datetime(2026, 11, 1, 10, 30, tzinfo=timezone.utc),
        )

        self.assertEqual(events[0]["event_type"], "notes_changed")
        self.assertFalse(events[0]["user_visible"])


class TestAdminReview(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.programs_path = root / "programs.json"
        self.admissions_path = root / "admission_records.json"
        self.pending_dir = root / "candidates" / "pending"
        self.audit_path = root / "candidates" / "review_audit.jsonl"
        self.change_events_path = root / "change_events.json"
        self.dismissed_path = root / "candidates" / "dismissed_candidates.json"
        self.programs_path.write_text(
            (ROOT / "data" / "programs.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.admissions_path.write_text(
            (ROOT / "data" / "admission_records.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.token = "test-admin-token-123"
        self.ingest_token = "test-ingest-token-123"
        self.patches = [
            patch.object(admin, "PROGRAMS_PATH", self.programs_path),
            patch.object(admin, "ADMISSIONS_PATH", self.admissions_path),
            patch.object(admin, "PENDING_DIR", self.pending_dir),
            patch.object(admin, "AUDIT_PATH", self.audit_path),
            patch.object(admin, "CHANGE_EVENTS_PATH", self.change_events_path),
            patch.object(admin, "DISMISSED_PATH", self.dismissed_path),
            patch.dict(os.environ, {
                "LITRAG_ADMIN_TOKEN": self.token,
                "LITRAG_INGEST_TOKEN": self.ingest_token,
            }),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def dispatch(self, method, path, body=b"", query=None, token=True, client_ip="test"):
        headers = {"x-admin-token": self.token} if token else {}
        return admin.dispatch(method, path, query or {}, headers, body, client_ip)

    def ingest(self, payload):
        return admin.dispatch(
            "POST",
            "/api/admin/ingest",
            {"name": ["auto-candidates.json"]},
            {"x-ingest-token": self.ingest_token},
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "test",
        )

    @staticmethod
    def candidate_form(item: dict) -> dict:
        defaults = item["review_defaults"]
        program = defaults["program"]
        return {
            "major_code": program["major_code"],
            "major_name": program["major_name"],
            "degree_type": program["degree_type"],
            "study_mode": program["study_mode"],
            "direction_code": program["direction_code"],
            "direction_name": program["direction_name"],
            "plan": program["plan"],
            "recommendation_quota": program["recommendation_quota"],
            "program_total_plan": program["program_total_plan"],
            "program_total_recommendation": program["program_total_recommendation"],
            "exam_subjects": "\n".join(program["exam_subjects"]),
            "notes": program["notes"],
            "summary": defaults["summary"],
            "why": defaults["why"],
            "undergrad": defaults["undergrad"],
            "audiences": "，".join(defaults["audiences"]),
            "replace_existing": False,
        }

    def test_admin_upload_and_approve_new_direction(self):
        status, *_ = self.dispatch("GET", "/api/admin/pending", token=False)
        self.assertEqual(status, 401)

        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        payload = collect_tsinghua_catalog(year=2027, html=html, college_filter="计算机科学与技术系")
        status, body, _, _ = self.dispatch(
            "POST",
            "/api/admin/upload",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            query={"name": ["tsinghua_2027_catalog.json"]},
        )
        self.assertEqual(status, 201)

        status, body, _, _ = self.dispatch("GET", "/api/admin/pending")
        self.assertEqual(status, 200)
        pending = json.loads(body)
        self.assertEqual(pending["summary"]["candidate_count"], 2)
        queue_id = pending["queue"][0]["queue_id"]
        item = next(
            item for item in pending["queue"][0]["items"]
            if item["candidate"]["program"]["direction_code"] == "03"
        )
        form = self.candidate_form(item)
        status, body, _, _ = self.dispatch(
            "POST",
            "/api/admin/approve",
            body=json.dumps(form, ensure_ascii=False).encode("utf-8"),
            query={"queue_id": [queue_id], "candidate_id": [item["candidate_id"]]},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))

        programs = json.loads(self.programs_path.read_text(encoding="utf-8"))
        admissions = json.loads(self.admissions_path.read_text(encoding="utf-8"))
        program_id = "thu-2027-024-085400-03-program"
        self.assertTrue(any(item["id"] == program_id for item in programs))
        admission = next(item for item in admissions if item["id"] == "thu-cs-2027-catalog")
        self.assertIn(program_id, admission["program_ids"])
        self.assertEqual(len(admissions), 3)

        status, body, _, _ = self.dispatch("GET", "/api/admin/pending")
        pending = json.loads(body)
        self.assertEqual(pending["summary"]["candidate_count"], 1)
        audit = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(audit[-1]["action"], "approve")
        change_events_data = json.loads(self.change_events_path.read_text(encoding="utf-8"))
        self.assertTrue(any(
            item["program_id"] == program_id and item["event_type"] == "direction_added"
            for item in change_events_data
        ))

    def test_admin_requires_override_for_existing_direction(self):
        html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        payload = collect_tsinghua_catalog(year=2027, html=html, college_filter="计算机科学与技术系")
        status, body, _, _ = self.dispatch(
            "POST",
            "/api/admin/upload",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            query={"name": ["tsinghua_2027_catalog.json"]},
        )
        self.assertEqual(status, 201)
        status, body, _, _ = self.dispatch("GET", "/api/admin/pending")
        pending = json.loads(body)
        queue_id = pending["queue"][0]["queue_id"]
        item = next(
            item for item in pending["queue"][0]["items"]
            if item["candidate"]["program"]["direction_code"] == "01"
        )
        query = {"queue_id": [queue_id], "candidate_id": [item["candidate_id"]]}

        status, body, _, _ = self.dispatch(
            "POST",
            "/api/admin/approve",
            body=json.dumps(self.candidate_form(item), ensure_ascii=False).encode("utf-8"),
            query=query,
        )
        self.assertEqual(status, 409)
        self.assertIn("覆盖已有方向", body.decode("utf-8"))

        form = self.candidate_form(item)
        form["replace_existing"] = True
        status, body, _, _ = self.dispatch(
            "POST",
            "/api/admin/approve",
            body=json.dumps(form, ensure_ascii=False).encode("utf-8"),
            query=query,
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        programs = json.loads(self.programs_path.read_text(encoding="utf-8"))
        program = next(item for item in programs if item["id"] == "thu-cs-2027-085400-01")
        self.assertEqual(program["notes"], "")
        change_events_data = json.loads(self.change_events_path.read_text(encoding="utf-8"))
        self.assertTrue(any(item["event_type"] == "notes_changed" and not item["user_visible"] for item in change_events_data))

    def test_automatic_ingest_skips_unchanged_duplicates_and_dismissed(self):
        pku_html = (ROOT / "tests" / "fixtures" / "pku_2027_catalog.html").read_text(encoding="utf-8")
        pku_payload = collect_pku_catalog(year=2027, html=pku_html, college_filter="软件与微电子学院")
        status, body, _, _ = self.ingest(pku_payload)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertFalse(result["queued"])
        self.assertEqual(result["skipped_unchanged"], 1)

        tsinghua_html = (ROOT / "tests" / "fixtures" / "tsinghua_2027_cs_catalog.html").read_text(encoding="utf-8")
        tsinghua_payload = collect_tsinghua_catalog(
            year=2027, html=tsinghua_html, college_filter="计算机科学与技术系"
        )
        status, body, _, _ = self.ingest(tsinghua_payload)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertTrue(result["queued"])
        self.assertEqual(result["queued"], 2)

        status, body, _, _ = self.ingest(tsinghua_payload)
        result = json.loads(body)
        self.assertFalse(result["queued"])
        self.assertEqual(result["skipped_duplicate"], 2)

        out_of_scope = deepcopy(tsinghua_payload)
        for item in out_of_scope["items"]:
            item["college"] = "建筑学院"
        status, body, _, _ = self.ingest(out_of_scope)
        result = json.loads(body)
        self.assertFalse(result["queued"])
        self.assertEqual(result["skipped_out_of_scope"], 2)

        status, body, _, _ = self.dispatch("GET", "/api/admin/pending")
        pending = json.loads(body)
        queue_id = pending["queue"][0]["queue_id"]
        item = next(
            item for item in pending["queue"][0]["items"]
            if item["candidate"]["program"]["direction_code"] == "03"
        )
        status, body, _, _ = self.dispatch(
            "POST",
            "/api/admin/reject",
            body=json.dumps({"reason": "目标受众不匹配"}, ensure_ascii=False).encode("utf-8"),
            query={"queue_id": [queue_id], "candidate_id": [item["candidate_id"]]},
        )
        self.assertEqual(status, 200)

        status, body, _, _ = self.ingest(tsinghua_payload)
        result = json.loads(body)
        self.assertFalse(result["queued"])
        self.assertEqual(result["skipped_dismissed"], 1)
        self.assertEqual(result["skipped_duplicate"], 1)

    def test_github_actions_identity_can_ingest_without_shared_secret(self):
        pku_html = (ROOT / "tests" / "fixtures" / "pku_2027_catalog.html").read_text(encoding="utf-8")
        payload = collect_pku_catalog(year=2027, html=pku_html, college_filter="软件与微电子学院")
        headers = {
            "x-github-token": "github-actions-short-lived-token",
            "x-github-repository": "Yul-pio/litrag",
            "x-github-run-id": "123456789",
        }
        with patch.object(admin, "_verify_github_actions_request", return_value=True), patch.dict(
            os.environ, {"LITRAG_INGEST_TOKEN": ""}
        ):
            status, body, _, _ = admin.dispatch(
                "POST",
                "/api/admin/ingest",
                {"name": ["pku_2027_catalog.json"]},
                headers,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "test",
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["skipped_unchanged"], 1)


class TestRadar(unittest.TestCase):
    def test_radar_items_are_available(self):
        data = service.list_radar_items()
        self.assertGreaterEqual(data["count"], 5)
        self.assertEqual(data["count"], len(data["items"]))
        self.assertTrue(any(item.get("doc_id") for item in data["items"]))
        pku = next(item for item in data["items"] if item["id"] == "pku-softmicro-2027-catalog")
        self.assertEqual(pku["major_code"], "085400")
        self.assertEqual(pku["direction_code"], "01")
        self.assertEqual(pku["program_total_plan"], 583)
        self.assertEqual(pku["program_total_recommendation"], 255)
        thu = next(item for item in data["items"] if item["id"] == "thu-cs-2027-catalog")
        self.assertEqual(thu["major_code"], "085400")
        self.assertEqual(thu["direction_code"], "01")
        self.assertEqual(thu["direction_name"], "计算机科学与技术专业硕士")
        self.assertEqual(thu["program_total_plan"], 13)

    def test_school_registry_is_available(self):
        data = service.list_schools()
        self.assertEqual(data["count"], 10)
        self.assertEqual(data["count"], len(data["items"]))
        self.assertTrue(all(item.get("admissions_url") for item in data["items"]))

    def test_structured_admission_data_is_available(self):
        sources = service.list_sources()
        programs = service.list_programs()
        records = service.list_admission_records()
        self.assertGreaterEqual(sources["count"], 10)
        self.assertEqual(programs["count"], 3)
        self.assertEqual(records["count"], 3)
        self.assertTrue(all(item.get("source_url") for item in records["items"]))

    def test_verified_change_events_are_available(self):
        data = service.list_change_events()
        self.assertGreaterEqual(data["count"], 3)
        self.assertTrue(all(item.get("user_visible", True) for item in data["items"]))
        self.assertTrue(all(item.get("school") and item.get("source") for item in data["items"]))
        self.assertEqual(data["items"][0]["feed_date"], "2026-10-31")


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


class TestWorkbenchBootstrap(unittest.TestCase):
    def test_missing_global_index_is_rebuilt_from_demo_docs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index_path = Path(temp_dir) / "index.json"
            with patch.object(service, "GLOBAL_INDEX", index_path), patch.object(
                service, "_global_retriever", None
            ):
                retriever = service._global()
                library = service.list_documents(None)

            self.assertGreater(len(retriever.chunks), 0)
            self.assertTrue(index_path.exists())
            self.assertGreater(len(library["demo_docs"]), 0)


if __name__ == "__main__":
    unittest.main()
