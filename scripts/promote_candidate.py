"""把采集候选转换为正式专业和招生记录。

流程：
    draft  -> 生成待填写审核单
    人工填写专业、方向、考试科目等字段
    apply  -> 写入 programs.json 和 admission_records.json

用法：
    python scripts/promote_candidate.py draft \
      --candidate-file data/candidates/pku_2027_catalog.json \
      --candidate-id pku-2027-catalog-4251f989

    python scripts/promote_candidate.py apply \
      --candidate-file data/candidates/pku_2027_catalog.json \
      --review data/candidates/pku-2027-catalog-4251f989.review.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PROGRAMS_PATH = DATA / "programs.json"
ADMISSIONS_PATH = DATA / "admission_records.json"


def load_json(path: Path) -> Any:
    if not path.exists():
        raise ValueError(f"文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def candidate_items(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return payload["items"]
    raise ValueError("候选文件必须是列表，或包含 items 列表")


def find_candidate(path: Path, candidate_id: str) -> dict:
    matches = [item for item in candidate_items(load_json(path)) if item.get("candidate_id") == candidate_id]
    if not matches:
        raise ValueError(f"找不到候选：{candidate_id}")
    return matches[0]


def draft_review(candidate: dict) -> dict:
    candidate_id = candidate.get("candidate_id", "").strip()
    if not candidate_id:
        raise ValueError("候选缺少 candidate_id")
    school_id = candidate.get("school_id", "")
    college = candidate.get("college", "")
    year = candidate.get("year")
    now = datetime.now().astimezone()
    return {
        "candidate_id": candidate_id,
        "id": candidate_id,
        "school_id": school_id,
        "source_id": candidate.get("source_id", ""),
        "year": year,
        "status": "draft",
        "document_type": candidate.get("document_type", "招生专业目录"),
        "college": college,
        "title": candidate.get("title", ""),
        "source_url": candidate.get("source_url", ""),
        "source_label": candidate.get("source_label", ""),
        "source_kind": candidate.get("source_kind", ""),
        "page_location": candidate.get("page_location", ""),
        "published_at": None,
        "verified_at": None,
        "summary": "",
        "why": "",
        "undergrad": "",
        "tags": [college] if college else [],
        "audiences": ["大四"],
        "featured": False,
        "signal": "官方待核验",
        "read_minutes": 4,
        "feed_date": now.strftime("%Y-%m-%d"),
        "feed_time": now.strftime("%H:%M"),
        "program": {
            "id": f"{candidate_id}-program",
            "school_id": school_id,
            "college": college,
            "year": year,
            "major_code": "",
            "major_name": "",
            "degree_type": "",
            "study_mode": "",
            "direction_code": "",
            "direction_name": "",
            "plan": None,
            "recommendation_quota": None,
            "program_total_plan": None,
            "program_total_recommendation": None,
            "exam_subjects": [],
            "notes_label": "专业备注",
            "notes": "",
            "verified_at": None,
        },
    }


def validate_review(review: dict, candidate: dict) -> None:
    errors: list[str] = []
    if review.get("candidate_id") != candidate.get("candidate_id"):
        errors.append("review.candidate_id 与候选不一致")
    if review.get("source_url") != candidate.get("source_url"):
        errors.append("source_url 与候选不一致，请确认没有改错链接")
    for field in ("id", "school_id", "source_id", "year", "document_type", "college", "title", "source_url", "status"):
        if not review.get(field):
            errors.append(f"缺少字段：{field}")
    if review.get("status") not in {"draft", "verified", "rejected"}:
        errors.append("status 必须是 draft / verified / rejected")

    program = review.get("program")
    if not isinstance(program, dict):
        errors.append("缺少 program 对象")
        program = {}
    for field in ("id", "school_id", "college", "year", "major_code", "major_name", "degree_type", "study_mode", "direction_code", "direction_name"):
        if not program.get(field):
            errors.append(f"program 缺少字段：{field}")
    if not isinstance(program.get("exam_subjects"), list):
        errors.append("program.exam_subjects 必须是列表")
    if program.get("school_id") != review.get("school_id"):
        errors.append("program.school_id 与 review.school_id 不一致")
    if program.get("college") != review.get("college"):
        errors.append("program.college 与 review.college 不一致")
    if program.get("year") != review.get("year"):
        errors.append("program.year 与 review.year 不一致")

    if review.get("status") == "verified":
        for field in ("verified_at", "summary", "why", "undergrad", "feed_date", "feed_time"):
            if not review.get(field):
                errors.append(f"verified 记录缺少字段：{field}")
        if not review.get("audiences"):
            errors.append("verified 记录缺少 audiences")
        if not program.get("exam_subjects"):
            errors.append("verified 记录缺少 exam_subjects")

    if errors:
        raise ValueError("审核单不完整：\n  - " + "\n  - ".join(errors))


def build_records(review: dict) -> tuple[dict, dict]:
    program = dict(review["program"])
    admission = {
        "id": review["id"],
        "school_id": review["school_id"],
        "college": review["college"],
        "year": review["year"],
        "document_type": review["document_type"],
        "title": review["title"],
        "source_id": review["source_id"],
        "source_url": review["source_url"],
        "source_label": review.get("source_label", ""),
        "source_kind": review.get("source_kind", ""),
        "page_location": review.get("page_location", ""),
        "published_at": review.get("published_at"),
        "verified_at": review.get("verified_at"),
        "status": review["status"],
        "program_ids": [program["id"]],
        "summary": review.get("summary", ""),
        "why": review.get("why", ""),
        "undergrad": review.get("undergrad", ""),
        "tags": review.get("tags", []),
        "audiences": review.get("audiences", []),
        "featured": bool(review.get("featured")),
        "signal": review.get("signal", "官方已核验"),
        "read_minutes": review.get("read_minutes", 4),
        "feed_date": review.get("feed_date"),
        "feed_time": review.get("feed_time"),
    }
    return program, admission


def upsert(items: list[dict], value: dict, update: bool) -> None:
    value_id = value.get("id")
    index = next((i for i, item in enumerate(items) if item.get("id") == value_id), None)
    if index is None:
        items.append(value)
        return
    if not update:
        raise ValueError(f"记录已存在：{value_id}；如确认覆盖请加 --update")
    items[index] = value


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description="候选审核与正式记录转换")
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="从候选生成待填写审核单")
    draft.add_argument("--candidate-file", required=True)
    draft.add_argument("--candidate-id", required=True)
    draft.add_argument("--output", default=None)

    apply = sub.add_parser("apply", help="应用已审核的审核单")
    apply.add_argument("--candidate-file", required=True)
    apply.add_argument("--review", required=True)
    apply.add_argument("--update", action="store_true", help="允许覆盖同 ID 的正式记录")
    apply.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    try:
        candidate_path = resolve_path(args.candidate_file)
        if args.command == "draft":
            candidate = find_candidate(candidate_path, args.candidate_id)
            review = draft_review(candidate)
            output = resolve_path(args.output) if args.output else candidate_path.parent / f"{args.candidate_id}.review.json"
            save_json(output, review)
            print(f"审核单已生成：{output}")
            print("请填写 program 字段、摘要、推荐理由和本科生怎么用，再执行 apply。")
            return 0

        review = load_json(resolve_path(args.review))
        candidate = find_candidate(candidate_path, review.get("candidate_id", ""))
        validate_review(review, candidate)
        program, admission = build_records(review)
        programs = load_json(PROGRAMS_PATH)
        admissions = load_json(ADMISSIONS_PATH)
        upsert(programs, program, args.update)
        upsert(admissions, admission, args.update)
        if args.dry_run:
            print(json.dumps({"program": program, "admission": admission}, ensure_ascii=False, indent=2))
            return 0
        save_json(PROGRAMS_PATH, programs)
        save_json(ADMISSIONS_PATH, admissions)
        print(f"已写入正式专业：{program['id']}")
        print(f"已写入正式招生记录：{admission['id']}")
        print("请继续运行：python scripts/validate_content.py")
        return 0
    except Exception as exc:
        print(f"失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())