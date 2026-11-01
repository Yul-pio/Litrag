"""对比采集候选与正式内容，生成只读审核报告。

这个工具不会修改 programs.json 或 admission_records.json。它负责把人工审核
从“翻完整 JSON”变成“先看新增、变更、未变和正式库缺失”。

用法：
    python scripts/review_candidates.py --candidate-file data/candidates/tsinghua_2027_catalog.json
    python scripts/review_candidates.py --candidate-file data/candidates/tsinghua_2027_catalog.json --format json
    python scripts/review_candidates.py --candidate-file data/candidates/tsinghua_2027_catalog.json --fail-on-changes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROGRAMS_PATH = ROOT / "data" / "programs.json"
ADMISSIONS_PATH = ROOT / "data" / "admission_records.json"

PROGRAM_FIELDS = (
    ("major_name", "major_name", "medium", "专业名称"),
    ("degree_type", "degree_type", "medium", "学位类型"),
    ("study_mode", "study_mode", "medium", "学习方式"),
    ("direction_name", "direction_name", "medium", "方向名称"),
    ("plan", "plan", "high", "方向招生计划"),
    ("recommendation_quota", "recommendation_quota", "high", "方向推免人数"),
    ("program_total_plan", "program_total_plan", "high", "专业总招生计划"),
    ("program_total_recommendation", "program_total_recommendation", "high", "专业总推免人数"),
    ("exam_subjects", "exam_subjects", "high", "初试科目"),
    ("notes", "notes", "medium", "招生说明"),
)

ADMISSION_FIELDS = (
    ("source_url", "source_url", "high", "官方来源链接"),
    ("source_id", "source_id", "high", "来源注册 ID"),
    ("document_type", "document_type", "medium", "文档类型"),
    ("college", "college", "medium", "院系"),
    ("source_kind", "source_kind", "low", "来源类型"),
)

LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}


def load_json(path: Path) -> Any:
    if not path.exists():
        raise ValueError(f"文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def candidate_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    raise ValueError("候选文件必须是列表，或包含 items 列表")


def record_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    raise ValueError("正式数据必须是列表，或包含 items 列表")


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return tuple(normalize_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, normalize_value(item)) for key, item in value.items()))
    return value


def values_equal(left: Any, right: Any) -> bool:
    return normalize_value(left) == normalize_value(right)


def _unique_value(items: list[dict], field: str) -> Any:
    values = {item.get(field) for item in items if item.get(field) not in (None, "")}
    return next(iter(values)) if len(values) == 1 else None


def _college_matches(value: Any, query: str | None) -> bool:
    if not query:
        return True
    college = normalize_text(value)
    needle = normalize_text(query)
    return needle in college or college in needle


def _program_key(program: dict) -> tuple[str, str, str, str, str]:
    return (
        normalize_text(program.get("school_id")),
        str(program.get("year") or ""),
        normalize_text(program.get("college")),
        normalize_text(program.get("major_code")),
        normalize_text(program.get("direction_code")),
    )


def _compare_fields(
    source: dict,
    target: dict | None,
    specs: tuple[tuple[str, str, str, str], ...],
    prefix: str,
) -> list[dict]:
    if target is None:
        return []
    changes: list[dict] = []
    for source_field, target_field, level, label in specs:
        if source_field not in source:
            continue
        candidate_value = source.get(source_field)
        formal_value = target.get(target_field)
        if values_equal(candidate_value, formal_value):
            continue
        changes.append({
            "level": level,
            "field": f"{prefix}.{source_field}",
            "label": label,
            "formal": formal_value,
            "candidate": candidate_value,
        })
    changes.sort(key=lambda item: LEVEL_ORDER.get(item["level"], 99))
    return changes


def _find_admission_for_program(
    program: dict,
    admissions: list[dict],
    candidate: dict,
) -> dict | None:
    program_id = program.get("id")
    linked = [item for item in admissions if program_id in (item.get("program_ids") or [])]
    if linked:
        exact_url = [item for item in linked if item.get("source_url") == candidate.get("source_url")]
        return (exact_url or linked)[0]
    return _find_admission_for_candidate(candidate, admissions)


def _find_admission_for_candidate(candidate: dict, admissions: list[dict]) -> dict | None:
    school_id = candidate.get("school_id")
    year = candidate.get("year")
    scoped = [
        item for item in admissions
        if item.get("school_id") == school_id and item.get("year") == year
    ]
    source_url = candidate.get("source_url")
    exact = [item for item in scoped if item.get("source_url") == source_url]
    if exact:
        return exact[0]

    title = normalize_text(candidate.get("title"))
    college = normalize_text(candidate.get("college"))
    document_type = normalize_text(candidate.get("document_type"))
    title_matches = [
        item for item in scoped
        if normalize_text(item.get("title")) == title
        and normalize_text(item.get("college")) == college
        and normalize_text(item.get("document_type")) == document_type
    ]
    if len(title_matches) == 1:
        return title_matches[0]

    document_matches = [
        item for item in scoped
        if normalize_text(item.get("college")) == college
        and normalize_text(item.get("document_type")) == document_type
    ]
    return document_matches[0] if len(document_matches) == 1 else None


def _review_item(
    candidate: dict,
    program: dict | None,
    admission: dict | None,
    changes: list[dict],
    status: str,
) -> dict:
    candidate_program = candidate.get("program") if isinstance(candidate.get("program"), dict) else {}
    risk = "none"
    if status == "new" or any(change["level"] == "high" for change in changes):
        risk = "high"
    elif any(change["level"] == "medium" for change in changes):
        risk = "medium"
    elif changes:
        risk = "low"

    return {
        "candidate_id": candidate.get("candidate_id"),
        "status": status,
        "risk": risk,
        "title": candidate.get("title"),
        "college": candidate.get("college"),
        "major_code": candidate_program.get("major_code"),
        "major_name": candidate_program.get("major_name"),
        "direction_code": candidate_program.get("direction_code"),
        "direction_name": candidate_program.get("direction_name"),
        "plan": candidate_program.get("plan"),
        "source_url": candidate.get("source_url"),
        "source_kind": candidate.get("source_kind"),
        "program_id": program.get("id") if program else None,
        "admission_id": admission.get("id") if admission else None,
        "existing_document": bool(admission and not program and status == "new"),
        "changes": changes,
    }


def review_candidates(
    payload: Any,
    programs_payload: Any,
    admissions_payload: Any,
    school_id: str | None = None,
    year: int | None = None,
    college: str | None = None,
) -> dict:
    all_candidates = candidate_items(payload)
    programs = record_items(programs_payload)
    admissions = record_items(admissions_payload)

    scope_school = school_id or (_unique_value(all_candidates, "school_id") if isinstance(payload, dict) else None)
    if scope_school is None and isinstance(payload, dict):
        scope_school = payload.get("school_id")
    scope_year = year or (_unique_value(all_candidates, "year") if isinstance(payload, dict) else None)
    if scope_year is None and isinstance(payload, dict):
        scope_year = payload.get("year")

    candidates = [
        item for item in all_candidates
        if (not scope_school or item.get("school_id") == scope_school)
        and (not scope_year or item.get("year") == scope_year)
        and _college_matches(item.get("college"), college)
    ]

    program_index: dict[tuple[str, str, str, str, str], dict] = {
        _program_key(item): item for item in programs
    }
    matched_program_ids: set[str] = set()
    reviewed: list[dict] = []

    for candidate in candidates:
        candidate_program = candidate.get("program")
        if not isinstance(candidate_program, dict):
            admission = _find_admission_for_candidate(candidate, admissions)
            changes = _compare_fields(candidate, admission, ADMISSION_FIELDS, "admission")
            status = "new" if admission is None else ("changed" if changes else "unchanged")
            reviewed.append(_review_item(candidate, None, admission, changes, status))
            continue

        key = _program_key(candidate_program)
        formal_program = program_index.get(key)
        if formal_program:
            matched_program_ids.add(formal_program["id"])
        admission = (
            _find_admission_for_program(formal_program, admissions, candidate)
            if formal_program
            else _find_admission_for_candidate(candidate, admissions)
        )
        changes = _compare_fields(candidate_program, formal_program, PROGRAM_FIELDS, "program")
        changes.extend(_compare_fields(candidate, admission, ADMISSION_FIELDS, "admission"))
        changes.sort(key=lambda item: LEVEL_ORDER.get(item["level"], 99))
        status = "new" if formal_program is None else ("changed" if changes else "unchanged")
        reviewed.append(_review_item(candidate, formal_program, admission, changes, status))

    program_candidates = [
        item for item in candidates if isinstance(item.get("program"), dict)
    ]
    missing: list[dict] = []
    if program_candidates:
        candidate_schools = {item.get("school_id") for item in program_candidates}
        candidate_years = {item.get("year") for item in program_candidates}
        candidate_colleges = {normalize_text(item.get("college")) for item in program_candidates}
        for program in programs:
            if program.get("id") in matched_program_ids:
                continue
            if program.get("school_id") not in candidate_schools:
                continue
            if program.get("year") not in candidate_years:
                continue
            if normalize_text(program.get("college")) not in candidate_colleges:
                continue
            missing.append({
                "program_id": program.get("id"),
                "school_id": program.get("school_id"),
                "college": program.get("college"),
                "year": program.get("year"),
                "major_code": program.get("major_code"),
                "direction_code": program.get("direction_code"),
                "direction_name": program.get("direction_name"),
            })

    summary = {
        "candidate_count": len(reviewed),
        "new": sum(item["status"] == "new" for item in reviewed),
        "changed": sum(item["status"] == "changed" for item in reviewed),
        "unchanged": sum(item["status"] == "unchanged" for item in reviewed),
        "missing": len(missing),
        "high_risk_items": sum(item["risk"] == "high" for item in reviewed),
    }
    scope_college = college or (_unique_value(candidates, "college") if isinstance(payload, dict) else None)
    return {
        "schema_version": 1,
        "scope": {
            "school_id": scope_school,
            "year": scope_year,
            "college": scope_college,
        },
        "summary": summary,
        "items": reviewed,
        "missing": missing,
    }


def display_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return " / ".join(display_value(item) for item in value)
    text = str(value)
    return text if len(text) <= 140 else text[:137] + "..."


def _render_reviewed_items(title: str, items: list[dict]) -> list[str]:
    lines = [f"## {title}（{len(items)}）", ""]
    for item in items:
        lines.extend([
            f"### {item.get('title') or item.get('candidate_id')}",
            "",
            f"- 候选 ID：`{item.get('candidate_id')}`",
            f"- 院系：{item.get('college') or '-'}",
            f"- 专业/方向：{item.get('major_code') or '-'} / {item.get('direction_code') or '-'} {item.get('direction_name') or ''}".rstrip(),
            f"- 计划：{display_value(item.get('plan'))}",
            f"- 来源：{item.get('source_url') or '-'}",
        ])
        if item.get("program_id"):
            lines.append(f"- 正式专业：`{item['program_id']}`")
        if item.get("admission_id"):
            lines.append(f"- 正式招生文档：`{item['admission_id']}`")
        if item.get("existing_document"):
            lines.append("- 说明：匹配到已有招生文档，但这是正式库中尚未出现的专业方向")
        if item.get("changes"):
            lines.extend(["", "#### 字段差异", ""])
            for change in item["changes"]:
                level = {"high": "高", "medium": "中", "low": "低"}.get(change["level"], change["level"])
                lines.append(
                    f"- [{level}] `{change['field']}` {change['label']}："
                    f"{display_value(change['formal'])} → {display_value(change['candidate'])}"
                )
        lines.append("")
    return lines


def render_markdown(report: dict, include_unchanged: bool = False) -> str:
    summary = report["summary"]
    scope = report["scope"]
    lines = [
        "# 招生候选审核报告",
        "",
        f"- 学校：`{scope.get('school_id') or '全部'}`",
        f"- 年份：`{scope.get('year') or '全部'}`",
        f"- 院系：{scope.get('college') or '全部'}",
        "",
        "## 摘要",
        "",
        "| 候选 | 新增 | 变更 | 未变 | 正式库缺失 | 高风险 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| {summary['candidate_count']} | {summary['new']} | {summary['changed']} | "
        f"{summary['unchanged']} | {summary['missing']} | {summary['high_risk_items']} |",
        "",
    ]

    new_items = [item for item in report["items"] if item["status"] == "new"]
    changed_items = [item for item in report["items"] if item["status"] == "changed"]
    unchanged_items = [item for item in report["items"] if item["status"] == "unchanged"]
    if new_items:
        lines.extend(_render_reviewed_items("新增候选", new_items))
    if changed_items:
        lines.extend(_render_reviewed_items("变更候选", changed_items))
    if unchanged_items:
        if include_unchanged:
            lines.extend(_render_reviewed_items("未变候选", unchanged_items))
        else:
            lines.extend([f"## 未变候选（{len(unchanged_items)}）", "", "默认折叠；加 `--include-unchanged` 展开。", ""])
    if report["missing"]:
        lines.extend([f"## 正式库中缺失（{len(report['missing'])}）", ""])
        for item in report["missing"]:
            lines.append(
                f"- `{item['program_id']}`：{item.get('major_code') or '-'} / "
                f"{item.get('direction_code') or '-'} {item.get('direction_name') or ''}".rstrip()
            )
        lines.append("")
    if not report["items"] and not report["missing"]:
        lines.extend(["没有匹配到候选或正式记录。", ""])

    return "\n".join(lines).rstrip() + "\n"


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成招生候选差异审核报告（只读）")
    parser.add_argument("--candidate-file", required=True)
    parser.add_argument("--programs", default=str(PROGRAMS_PATH))
    parser.add_argument("--admissions", default=str(ADMISSIONS_PATH))
    parser.add_argument("--school", default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--college", default=None)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--output", default=None)
    parser.add_argument("--include-unchanged", action="store_true")
    parser.add_argument("--fail-on-changes", action="store_true")
    args = parser.parse_args()

    try:
        candidate_path = resolve_path(args.candidate_file)
        report = review_candidates(
            load_json(candidate_path),
            load_json(resolve_path(args.programs)),
            load_json(resolve_path(args.admissions)),
            school_id=args.school,
            year=args.year,
            college=args.college,
        )
        if args.format == "json":
            rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        else:
            rendered = render_markdown(report, include_unchanged=args.include_unchanged)

        if args.output:
            output = resolve_path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"审核报告：{output}")
        else:
            print(rendered, end="")

        summary = report["summary"]
        has_changes = summary["new"] or summary["changed"] or summary["missing"]
        return 2 if args.fail_on_changes and has_changes else 0
    except Exception as exc:
        print(f"生成审核报告失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
