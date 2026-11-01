"""把正式专业变更转换成用户可读的招生变化事件。"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHANGE_EVENTS_PATH = ROOT / "data" / "change_events.json"

TRACKED_FIELDS = (
    ("direction_name", "方向名称", "medium"),
    ("study_mode", "学习方式", "medium"),
    ("plan", "方向招生计划", "high"),
    ("recommendation_quota", "方向推免人数", "high"),
    ("program_total_plan", "专业总招生计划", "high"),
    ("program_total_recommendation", "专业总推免人数", "high"),
    ("exam_subjects", "初试科目", "high"),
    ("notes", "招生说明", "low"),
)

ACTIONABLE_FIELDS = {
    "direction_name",
    "study_mode",
    "plan",
    "recommendation_quota",
    "program_total_plan",
    "program_total_recommendation",
    "exam_subjects",
}

_lock = threading.RLock()


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("change_events.json 必须是列表")
    return payload


def _atomic_write(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load_change_events(path: Path | None = None) -> list[dict]:
    return _load_json(path or CHANGE_EVENTS_PATH)


def _normalized(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return tuple(_normalized(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _normalized(item)) for key, item in value.items()))
    return value


def _display(value: Any, limit: int = 90) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        text = " / ".join(str(item) for item in value)
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _field_changes(old_program: dict | None, new_program: dict) -> list[dict]:
    if old_program is None:
        changes = [{
            "field": "direction",
            "label": "新增研究方向",
            "old": None,
            "new": f"{new_program.get('direction_code', '')} {new_program.get('direction_name', '')}".strip(),
            "level": "high",
        }]
        for field, label, level in TRACKED_FIELDS:
            value = new_program.get(field)
            if value in (None, "", []):
                continue
            changes.append({
                "field": field,
                "label": label,
                "old": None,
                "new": value,
                "level": level,
            })
        return changes

    changes: list[dict] = []
    for field, label, level in TRACKED_FIELDS:
        old_value = old_program.get(field)
        new_value = new_program.get(field)
        if _normalized(old_value) == _normalized(new_value):
            continue
        changes.append({
            "field": field,
            "label": label,
            "old": old_value,
            "new": new_value,
            "level": level,
        })
    return changes


def _event_type(old_program: dict | None, changes: list[dict]) -> tuple[str, list[str]]:
    if old_program is None:
        return "direction_added", ["direction_added"]
    fields = {change["field"] for change in changes}
    change_types: list[str] = []
    if "exam_subjects" in fields:
        change_types.append("exam_changed")
    if fields & {"plan", "program_total_plan"}:
        change_types.append("plan_changed")
    if fields & {"recommendation_quota", "program_total_recommendation"}:
        change_types.append("quota_changed")
    if fields & {"direction_name", "study_mode"}:
        change_types.append("direction_updated")
    if "notes" in fields:
        change_types.append("notes_changed")
    return (change_types[0] if change_types else "admission_updated"), change_types


def _event_title(event_type: str, program: dict) -> str:
    direction = f"{program.get('direction_code', '')} {program.get('direction_name', '')}".strip()
    if event_type == "direction_added":
        return f"新增研究方向：{direction}"
    if event_type == "exam_changed":
        return f"初试科目调整：{direction}"
    if event_type == "plan_changed":
        return f"招生计划调整：{direction}"
    if event_type == "quota_changed":
        return f"招生名额调整：{direction}"
    if event_type == "direction_updated":
        return f"方向信息更新：{direction}"
    if event_type == "notes_changed":
        return f"招生说明更新：{direction}"
    return f"招生信息更新：{direction}"


def _event_summary(event_type: str, program: dict, changes: list[dict]) -> str:
    if event_type == "direction_added":
        plan = program.get("plan")
        bits = [
            f"{program.get('college', '')} {program.get('major_code', '')} {program.get('major_name', '')}",
            f"新增 {program.get('direction_code', '')} {program.get('direction_name', '')}".strip(),
        ]
        if plan is not None:
            bits.append(f"方向计划 {plan} 人")
        return "；".join(bit for bit in bits if bit.strip())
    return "；".join(
        f"{change['label']}：{_display(change['old'])} → {_display(change['new'])}"
        for change in changes[:3]
    )


def build_change_events(
    old_program: dict | None,
    new_program: dict,
    admission: dict,
    event_time: datetime | None = None,
) -> list[dict]:
    changes = _field_changes(old_program, new_program)
    if not changes:
        return []
    now = event_time or datetime.now().astimezone()
    event_type, change_types = _event_type(old_program, changes)
    fields = {change["field"] for change in changes}
    user_visible = old_program is None or bool(fields & ACTIONABLE_FIELDS)
    severity = "high" if any(change["level"] == "high" for change in changes) else (
        "medium" if any(change["level"] == "medium" for change in changes) else "low"
    )
    stable_payload = {
        "program_id": new_program.get("id"),
        "event_type": event_type,
        "changes": changes,
        "verified_at": new_program.get("verified_at"),
    }
    digest = hashlib.sha1(
        json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    feed_date = now.date().isoformat()
    event = {
        "id": f"chg-{feed_date.replace('-', '')}-{digest}",
        "event_type": event_type,
        "change_types": change_types,
        "severity": severity,
        "user_visible": user_visible,
        "school_id": new_program.get("school_id"),
        "college": new_program.get("college"),
        "year": new_program.get("year"),
        "major_code": new_program.get("major_code"),
        "major_name": new_program.get("major_name"),
        "direction_code": new_program.get("direction_code"),
        "direction_name": new_program.get("direction_name"),
        "program_id": new_program.get("id"),
        "admission_id": admission.get("id"),
        "title": _event_title(event_type, new_program),
        "summary": _event_summary(event_type, new_program, changes),
        "changes": changes,
        "source_id": admission.get("source_id"),
        "source_url": admission.get("source_url"),
        "source_label": admission.get("source_label"),
        "source_kind": admission.get("source_kind"),
        "page_location": admission.get("page_location", ""),
        "feed_date": feed_date,
        "feed_time": now.strftime("%H:%M"),
        "verified_at": new_program.get("verified_at") or admission.get("verified_at") or feed_date,
        "audiences": admission.get("audiences", []),
        "tags": ["招生变化", new_program.get("major_name", ""), new_program.get("direction_name", "")],
    }
    return [event]


def append_change_events(events: list[dict], path: Path | None = None) -> int:
    if not events:
        return 0
    target = path or CHANGE_EVENTS_PATH
    with _lock:
        existing = _load_json(target)
        existing_ids = {item.get("id") for item in existing}
        additions = [item for item in events if item.get("id") not in existing_ids]
        if not additions:
            return 0
        _atomic_write(target, existing + additions)
    return len(additions)
