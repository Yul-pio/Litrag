"""校验院校、来源、专业和招生记录之间的引用关系。

用法：
    python scripts/validate_content.py

未来自动采集的候选内容也应在发布前经过同样的校验。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FILES = {
    "schools": DATA / "schools.json",
    "sources": DATA / "sources.json",
    "programs": DATA / "programs.json",
    "admissions": DATA / "admission_records.json",
    "radar": DATA / "radar_items.json",
}


def load(name: str) -> list[dict]:
    path = FILES[name]
    if not path.exists():
        raise ValueError(f"缺少文件：{path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"{name} 必须是列表")
    return data


def main() -> int:
    errors: list[str] = []
    try:
        schools = load("schools")
        sources = load("sources")
        programs = load("programs")
        admissions = load("admissions")
        radar = load("radar")
    except Exception as exc:
        print(f"校验失败：{exc}")
        return 1

    def unique(items: list[dict], label: str) -> dict[str, dict]:
        seen: dict[str, dict] = {}
        for item in items:
            item_id = str(item.get("id", "")).strip()
            if not item_id:
                errors.append(f"{label} 存在缺少 id 的记录")
                continue
            if item_id in seen:
                errors.append(f"{label} id 重复：{item_id}")
            seen[item_id] = item
        return seen

    school_map = unique(schools, "schools")
    source_map = unique(sources, "sources")
    program_map = unique(programs, "programs")
    record_map = unique(admissions, "admission_records")
    unique(radar, "radar_items")

    for source in sources:
        if source.get("school_id") not in school_map:
            errors.append(f"source {source.get('id')} 引用了不存在的 school_id")
        if not source.get("url"):
            errors.append(f"source {source.get('id')} 缺少 url")

    for program in programs:
        if program.get("school_id") not in school_map:
            errors.append(f"program {program.get('id')} 引用了不存在的 school_id")
        for field in ("major_code", "major_name", "degree_type", "study_mode", "direction_code", "direction_name"):
            if not program.get(field):
                errors.append(f"program {program.get('id')} 缺少 {field}")
        if not isinstance(program.get("exam_subjects"), list):
            errors.append(f"program {program.get('id')} exam_subjects 必须是列表")

    for record in admissions:
        if record.get("school_id") not in school_map:
            errors.append(f"record {record.get('id')} 引用了不存在的 school_id")
        if record.get("source_id") not in source_map:
            errors.append(f"record {record.get('id')} 引用了不存在的 source_id")
        if not record.get("source_url"):
            errors.append(f"record {record.get('id')} 缺少 source_url")
        if record.get("status") not in {"draft", "verified", "rejected"}:
            errors.append(f"record {record.get('id')} status 非法")
        for program_id in record.get("program_ids") or []:
            if program_id not in program_map:
                errors.append(f"record {record.get('id')} 引用了不存在的 program_id {program_id}")

    print(f"schools={len(schools)} sources={len(sources)} programs={len(programs)} admissions={len(admissions)} radar={len(radar)}")
    if errors:
        print("\n发现问题：")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("校验通过：引用关系完整。")
    return 0


if __name__ == "__main__":
    sys.exit(main())