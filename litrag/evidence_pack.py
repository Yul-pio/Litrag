"""不依赖大模型的复试文献证据包生成器。

它不是自由聊天，而是用固定任务模板从论文中抽取问题、方法、实验、结论和
局限，并保留可点击页码。输出同时包含结构化数据和可导出的 Markdown。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from .chunker import Chunk

SECTION_SPECS = (
    (
        "problem",
        "研究问题",
        (
            "research problem", "challenge", "objective", "we study", "we introduce",
            "we propose", "we present", "in this paper", "this work", "本文", "研究问题",
            "目标", "挑战", "动机",
        ),
        "early",
    ),
    (
        "method",
        "方法与模型",
        (
            "method", "approach", "framework", "algorithm", "architecture", "training",
            "inference", "retrieve", "generate", "critique", "our model", "we train",
            "方法", "模型", "框架", "算法", "实现", "训练", "检索", "生成",
        ),
        "middle",
    ),
    (
        "results",
        "实验与结果",
        (
            "experiment", "evaluation", "result", "performance", "benchmark",
            "comparison", "outperform", "accuracy", "score", "实验", "评估", "结果",
            "性能", "对比", "准确率", "数据集",
        ),
        "late-middle",
    ),
    (
        "limitations",
        "局限与边界",
        (
            "limitation", "weakness", "constraint", "future work", "remaining",
            "局限", "不足", "限制", "未来工作", "仍未", "挑战",
        ),
        "late",
    ),
    (
        "contribution",
        "结论与贡献",
        (
            "conclusion", "contribution", "main finding", "we conclude", "overall",
            "demonstrate", "in summary", "结论", "贡献", "发现", "意义", "启示",
        ),
        "late",
    ),
)

QUESTION_TEMPLATES = (
    "这篇论文要解决的核心问题是什么？",
    "作者为什么选择这种方法，而不是更简单的方案？",
    "实验设计如何证明方法真的有效？",
    "主要结果有哪些适用边界或局限？",
    "如果老师追问创新点，你会怎么回答？",
    "如果把这个方法用到自己的项目里，你会改哪里？",
    "这个方向下一步还可以研究什么？",
    "用一句话概括这篇论文的贡献。",
)


def _normalize_quote(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+|(?<=\.)\s+(?=[A-Z])")


def _useful_citation(text: str) -> bool:
    text = (text or "").strip()
    lowered = text.lower()
    if len(text) < 28:
        return False
    bad_prefixes = (
        "preprint", "table ", "table:", "figure ", "figure:", "fig.",
        "bold numbers", "note:", "algorithm ", "input output",
    )
    if lowered.startswith(bad_prefixes):
        return False
    visible = [char for char in text if not char.isspace()]
    if visible and sum(char.isdigit() for char in visible) / len(visible) > 0.16:
        return False
    if len(re.findall(r"[{}|<>]", text)) > 4:
        return False
    return True


def _page_bonus(page: int | None, max_page: int, bias: str) -> float:
    if not page or max_page <= 1:
        return 0.0
    ratio = page / max_page
    if bias == "early":
        return 1.2 if ratio <= 0.3 else (-0.3 if ratio >= 0.65 else 0.2)
    if bias == "middle":
        return 0.6 if 0.15 <= ratio <= 0.75 else 0.0
    if bias == "late-middle":
        return 0.9 if ratio >= 0.25 else -0.2
    if bias == "late":
        return 1.0 if ratio >= 0.6 else -0.2
    return 0.0


def _section_candidates(chunks: list[Chunk], keywords: tuple[str, ...], bias: str) -> list[dict]:
    max_page = max((chunk.page or 0 for chunk in chunks), default=0)
    scored: list[tuple[float, dict]] = []
    seen: set[str] = set()
    for chunk in chunks:
        for raw in _SENTENCE_SPLIT.split(chunk.text):
            sentence = re.sub(r"\s+", " ", raw or "").strip()
            sentence = re.sub(r"^-(?=[A-Z])", "", sentence)
            if "Self-RAG" in (chunk.title or "") and sentence.startswith("RAG "):
                sentence = "Self-" + sentence
            if not _useful_citation(sentence):
                continue
            if re.match(r"^\d+[A-Z]", sentence) or re.match(r"^(and|or|but|because)\b", sentence, re.I):
                continue
            if not (sentence[0].isupper() or "\u4e00" <= sentence[0] <= "\u9fff"):
                continue
            lowered = sentence.lower()
            hits = sum(1 for keyword in keywords if keyword in lowered)
            if hits == 0:
                continue
            key = _normalize_quote(sentence)[:120]
            if key in seen:
                continue
            seen.add(key)
            complete = sentence[-1] in ".。!?！？；;"
            score = hits * 1.6 + _page_bonus(chunk.page, max_page, bias)
            score += 0.25 if complete else -0.4
            score += 0.12 if 45 <= len(sentence) <= 260 else 0.0
            scored.append((score, {
                "quote": sentence[:360],
                "chunk_id": chunk.chunk_id,
                "doc": chunk.doc_id,
                "title": chunk.title,
                "section": chunk.section,
                "page": chunk.page,
                "complete": complete,
            }))
    scored.sort(key=lambda item: item[0], reverse=True)
    complete = [item for item in scored if item[1]["complete"]]
    pool = complete or scored
    picked: list[dict] = []
    used_chunks: set[str] = set()
    for _, item in pool:
        if item["chunk_id"] in used_chunks:
            continue
        picked.append(item)
        used_chunks.add(item["chunk_id"])
        if len(picked) >= 2:
            break
    return picked


def _add_evidence_item(
    item: dict,
    evidence: list[dict],
    seen: set[tuple[str, str]],
) -> int | None:
    key = (item["chunk_id"], _normalize_quote(item["quote"])[:100])
    if key in seen:
        return None
    seen.add(key)
    global_id = len(evidence) + 1
    evidence.append({
        "id": global_id,
        "chunk_id": item["chunk_id"],
        "doc": item["doc"],
        "title": item["title"],
        "section": item["section"],
        "page": item["page"],
        "quote": item["quote"],
    })
    return global_id


def _format_page(citation: dict) -> str:
    if citation.get("page"):
        return f"第 {citation['page']} 页"
    return citation.get("section") or "原文片段"


def build_markdown(pack: dict) -> str:
    lines = [
        "# 复试文献证据包",
        "",
        f"- 论文：{pack['title']}",
        f"- 目标：{pack.get('school') or '未填写'} / {pack.get('major') or '未填写'}",
        f"- 导师或方向：{pack.get('advisor') or pack.get('direction') or '未填写'}",
        f"- 用途：{pack.get('purpose')}",
        f"- 时长：{pack.get('duration')} 分钟",
        "",
        "## 一句话总结",
        "",
        pack.get("summary") or "论文中没有找到可提取的摘要句。",
        "",
        "## 核心内容",
        "",
    ]
    for section in pack.get("sections", []):
        lines.extend([f"### {section['label']}", ""])
        if section.get("points"):
            for point in section["points"]:
                lines.append(f"- {point['text']} [{point['evidence_id']}]")
        else:
            lines.append("- 论文中没有提取到明确内容，需要人工补读。")
        lines.append("")

    lines.extend(["## 3 分钟汇报提纲", ""])
    for item in pack.get("presentation", []):
        lines.append(f"**{item['label']}**：{item['text']}")
        lines.append("")

    lines.extend(["## 可能追问", ""])
    for index, question in enumerate(pack.get("questions", []), start=1):
        lines.append(f"{index}. {question}")
    lines.extend(["", "## 原文证据", ""])
    for citation in pack.get("evidence", []):
        lines.extend([
            f"### [{citation['id']}] {citation.get('title') or citation.get('doc')}",
            f"{citation.get('doc')} · {_format_page(citation)}",
            f"> {citation.get('quote')}",
            "",
        ])
    if pack.get("warnings"):
        lines.extend(["## 使用提醒", ""])
        lines.extend(f"- {warning}" for warning in pack["warnings"])
        lines.append("")
    lines.extend(["---", f"生成时间：{pack.get('generated_at', '')}"])
    return "\n".join(lines).strip() + "\n"


def build_evidence_pack(
    chunks: list[Chunk],
    doc_id: str,
    school: str = "",
    major: str = "",
    advisor: str = "",
    direction: str = "",
    purpose: str = "复试汇报",
    duration: int = 3,
    title: str | None = None,
) -> dict:
    if not chunks:
        raise ValueError("没有可用于生成证据包的论文内容")
    duration = max(1, min(int(duration), 10))
    evidence: list[dict] = []
    seen: set[tuple[str, str]] = set()
    sections: list[dict] = []
    warnings: list[str] = []

    for key, label, keywords, bias in SECTION_SPECS:
        candidates = _section_candidates(chunks, keywords, bias)
        points: list[dict] = []
        for item in candidates:
            evidence_id = _add_evidence_item(item, evidence, seen)
            if evidence_id is not None:
                points.append({"text": item["quote"], "evidence_id": evidence_id})
        if not points:
            warnings.append(f"{label}没有提取到明确原文，需要人工补读。")
        sections.append({
            "key": key,
            "label": label,
            "points": points,
            "evidence_ids": [point["evidence_id"] for point in points],
        })

    problem_section = next((item for item in sections if item["key"] == "problem"), {})
    summary_points = problem_section.get("points", [])
    summary_ids = [point["evidence_id"] for point in summary_points[:1]]
    summary = (
        summary_points[0]["text"][:240]
        if summary_points
        else (
            evidence[0]["quote"][:240]
            if evidence
            else "论文中没有找到明确的摘要结论，需要人工补读。"
        )
    )

    def section_text(key: str, fallback: str) -> str:
        section = next((item for item in sections if item["key"] == key), {})
        texts = [point["text"] for point in section.get("points", [])]
        return " ".join(texts[:2]) if texts else fallback

    title = (title or chunks[0].title or doc_id).strip()
    presentation = [
        {
            "label": "开场",
            "text": f"我用 {duration} 分钟介绍《{title}》，重点说明它解决什么问题、为什么这样做，以及结果和局限。",
        },
        {
            "label": "研究问题",
            "text": section_text("problem", "先说明论文研究问题，再补充背景。"),
        },
        {
            "label": "方法",
            "text": section_text("method", "按输入、核心步骤和输出解释方法。"),
        },
        {
            "label": "结果",
            "text": section_text("results", "只讲论文明确报告的实验和结果。"),
        },
        {
            "label": "边界与总结",
            "text": section_text("limitations", "说明局限，再给一句贡献总结。"),
        },
    ]

    questions = list(QUESTION_TEMPLATES)
    focus = direction or advisor
    if focus:
        questions.append(f"这篇论文与导师方向“{focus}”有什么具体联系？")
    questions.append("如果让你复现这篇论文，第一步会做什么？")

    pack = {
        "doc_id": doc_id,
        "title": title,
        "school": school.strip(),
        "major": major.strip(),
        "advisor": advisor.strip(),
        "direction": direction.strip(),
        "purpose": purpose.strip() or "复试汇报",
        "duration": duration,
        "summary": summary,
        "summary_evidence_ids": summary_ids,
        "sections": sections,
        "presentation": presentation,
        "questions": questions,
        "evidence": evidence,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "structured_extractive",
    }
    pack["markdown"] = build_markdown(pack)
    return pack
