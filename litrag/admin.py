"""内部招生候选审核后台。

审核页只负责展示候选、收集人工确认结果并写入正式数据。所有管理 API 都要求
`X-Admin-Token` 请求头，生产环境必须配置 `LITRAG_ADMIN_TOKEN`。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from scripts.collectors.tsinghua import normalize_text
from scripts.promote_candidate import (
    apply_review,
    candidate_items,
    direction_key,
    draft_review,
)
from scripts.review_candidates import review_candidates

ROOT = Path(__file__).resolve().parents[1]
ADMIN_HTML_PATH = ROOT / "web" / "admin.html"
PROGRAMS_PATH = ROOT / "data" / "programs.json"
ADMISSIONS_PATH = ROOT / "data" / "admission_records.json"
CANDIDATE_ROOT = ROOT / "data" / "candidates"
PENDING_DIR = CANDIDATE_ROOT / "pending"
PROCESSED_DIR = CANDIDATE_ROOT / "processed"
REJECTED_DIR = CANDIDATE_ROOT / "rejected"
AUDIT_PATH = CANDIDATE_ROOT / "review_audit.jsonl"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
QUEUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,180}$")
_lock = threading.RLock()


class AdminError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _header(headers: dict[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value or ""
    return ""


def _admin_token() -> str:
    return os.environ.get("LITRAG_ADMIN_TOKEN", "").strip()


def _configured() -> bool:
    return len(_admin_token()) >= 12


def _require_token(headers: dict[str, str]) -> None:
    token = _admin_token()
    if len(token) < 12:
        raise AdminError(503, "服务端未配置 LITRAG_ADMIN_TOKEN（至少 12 位）")
    supplied = _header(headers, "X-Admin-Token")
    if not supplied or not hmac.compare_digest(supplied, token):
        raise AdminError(401, "管理员令牌无效，请重新登录")


def _response_json(status: int, payload: dict) -> tuple[int, bytes, str, dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, body, "application/json; charset=utf-8", _security_headers()


def _response_text(status: int, text: str) -> tuple[int, bytes, str, dict[str, str]]:
    return status, text.encode("utf-8"), "text/plain; charset=utf-8", _security_headers()


def _security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }


def _query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
    return (query.get(name) or [default])[0]


def _json_body(body: bytes) -> dict:
    try:
        payload = json.loads(body.decode("utf-8-sig") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdminError(400, f"请求 JSON 无效：{exc}") from exc
    if not isinstance(payload, dict):
        raise AdminError(400, "请求体必须是 JSON 对象")
    return payload


def _queue_path(queue_id: str) -> Path:
    if not QUEUE_ID_RE.fullmatch(queue_id):
        raise AdminError(400, "待审核文件 ID 无效")
    root = PENDING_DIR.resolve()
    path = (PENDING_DIR / f"{queue_id}.json").resolve()
    if path.parent != root:
        raise AdminError(400, "待审核文件路径无效")
    if not path.exists():
        raise AdminError(404, "待审核文件不存在或已处理")
    return path


def _load_queue(queue_id: str) -> tuple[Path, dict]:
    path = _queue_path(queue_id)
    try:
        payload = _load_json(path)
        candidate_items(payload)
    except Exception as exc:
        raise AdminError(400, f"待审核文件格式错误：{exc}") from exc
    return path, payload


def _find_candidate(payload: Any, candidate_id: str) -> dict:
    for item in candidate_items(payload):
        if item.get("candidate_id") == candidate_id:
            return item
    raise AdminError(404, f"找不到候选：{candidate_id}")


def _remove_candidate(path: Path, payload: dict, candidate_id: str) -> None:
    remaining = [
        item for item in candidate_items(payload)
        if item.get("candidate_id") != candidate_id
    ]
    if remaining:
        if isinstance(payload, list):
            payload = remaining
        else:
            payload["items"] = remaining
            payload["count"] = len(remaining)
        _atomic_write_json(path, payload)
    else:
        path.unlink(missing_ok=True)


def _audit(action: str, queue_id: str, candidate: dict, client_ip: str, **extra: Any) -> None:
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "queue_id": queue_id,
        "candidate_id": candidate.get("candidate_id"),
        "school_id": candidate.get("school_id"),
        "college": candidate.get("college"),
        "client_ip": client_ip,
        **extra,
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _recent_audit(limit: int = 12) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    entries: list[dict] = []
    for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return list(reversed(entries[-limit:]))


def _candidate_report(candidate: dict, programs: list[dict], admissions: list[dict]) -> dict:
    report = review_candidates({"items": [candidate]}, programs, admissions)
    return report["items"][0]


def _pending_payload() -> dict:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    programs = _load_json(PROGRAMS_PATH)
    admissions = _load_json(ADMISSIONS_PATH)
    queue: list[dict] = []
    summary = {"candidate_count": 0, "new": 0, "changed": 0, "unchanged": 0, "high_risk": 0}

    paths = sorted(PENDING_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        queue_id = path.stem
        try:
            payload = _load_json(path)
            items = candidate_items(payload)
        except Exception as exc:
            queue.append({
                "queue_id": queue_id,
                "filename": path.name,
                "uploaded_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "error": str(exc),
                "items": [],
            })
            continue

        reviewed_items: list[dict] = []
        for candidate in items:
            try:
                report_item = _candidate_report(candidate, programs, admissions)
                defaults = draft_review(candidate, admissions)
                error = None
            except Exception as exc:
                report_item = {
                    "status": "error",
                    "risk": "high",
                    "changes": [],
                    "admission_id": None,
                    "existing_document": False,
                }
                defaults = None
                error = str(exc)

            status = report_item.get("status", "error")
            risk = report_item.get("risk", "high")
            summary["candidate_count"] += 1
            if status in summary:
                summary[status] += 1
            if risk == "high":
                summary["high_risk"] += 1
            reviewed_items.append({
                "queue_id": queue_id,
                "candidate_id": candidate.get("candidate_id"),
                "title": candidate.get("title"),
                "school_id": candidate.get("school_id"),
                "college": candidate.get("college"),
                "status": status,
                "risk": risk,
                "changes": report_item.get("changes", []),
                "existing_document": bool(report_item.get("existing_document")),
                "admission_id": report_item.get("admission_id"),
                "candidate": candidate,
                "review_defaults": defaults,
                "error": error,
            })

        queue.append({
            "queue_id": queue_id,
            "filename": path.name,
            "uploaded_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            "items": reviewed_items,
        })

    return {"queue": queue, "summary": summary, "audit": _recent_audit()}


def _upload(name: str, body: bytes) -> dict:
    if not body:
        raise AdminError(400, "候选文件为空")
    if len(body) > MAX_UPLOAD_BYTES:
        raise AdminError(413, "候选文件超过 2MB 限制")
    try:
        payload = json.loads(body.decode("utf-8-sig"))
        items = candidate_items(payload)
    except Exception as exc:
        raise AdminError(400, f"候选 JSON 格式错误：{exc}") from exc
    if not items:
        raise AdminError(400, "候选文件没有 items")

    stem = Path(name or "candidates.json").stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")[:40] or "candidates"
    digest = hashlib.sha256(body).hexdigest()[:10]
    now = datetime.now(timezone.utc)
    queue_id = f"{now:%Y%m%dT%H%M%SZ}-{digest}-{safe_stem}"
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(PENDING_DIR / f"{queue_id}.json", payload)
    return {"queue_id": queue_id, "count": len(items)}


def _optional_int(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AdminError(400, f"{label}必须是整数") from exc
    if result < 0:
        raise AdminError(400, f"{label}不能为负数")
    return result


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[\n;,，；]+", str(value or ""))
    return [normalize_text(item) for item in raw if normalize_text(item)]


def _apply_form(review: dict, form: dict) -> None:
    program = review["program"]
    for field in (
        "major_code",
        "major_name",
        "degree_type",
        "study_mode",
        "direction_code",
        "direction_name",
        "notes_label",
        "notes",
    ):
        if field in form:
            program[field] = normalize_text(form.get(field))
    for field, label in (
        ("plan", "方向招生计划"),
        ("recommendation_quota", "方向推免人数"),
        ("program_total_plan", "专业总招生计划"),
        ("program_total_recommendation", "专业总推免人数"),
    ):
        if field in form:
            program[field] = _optional_int(form.get(field), label)
    if "exam_subjects" in form:
        program["exam_subjects"] = _string_list(form.get("exam_subjects"))

    for field in ("summary", "why", "undergrad"):
        if field in form:
            review[field] = normalize_text(form.get(field))
    if "audiences" in form:
        review["audiences"] = _string_list(form.get("audiences"))
    review["signal"] = "官方已核验"
    review["status"] = "verified"

    now = datetime.now().astimezone()
    verified_at = now.date().isoformat()
    review["verified_at"] = verified_at
    program["verified_at"] = verified_at
    review["feed_date"] = review.get("feed_date") or verified_at
    review["feed_time"] = review.get("feed_time") or now.strftime("%H:%M")


def _approve(queue_id: str, candidate_id: str, form: dict, client_ip: str) -> dict:
    path, payload = _load_queue(queue_id)
    candidate = _find_candidate(payload, candidate_id)
    programs = _load_json(PROGRAMS_PATH)
    admissions = _load_json(ADMISSIONS_PATH)
    review = draft_review(candidate, admissions)
    _apply_form(review, form)

    update_existing = False
    same_direction = [
        item for item in programs
        if direction_key(item) == direction_key(review["program"])
    ]
    if same_direction:
        if review.get("admission_action") != "append":
            raise AdminError(409, "同方向已存在，但候选来源未匹配到现有招生文档")
        review["program"]["id"] = same_direction[0]["id"]
        if not bool(form.get("replace_existing")):
            raise AdminError(409, "该方向已存在；确认覆盖正式记录前请勾选“覆盖已有方向”")
        update_existing = True

    try:
        program, admission = apply_review(
            review,
            candidate,
            programs,
            admissions,
            update=update_existing,
        )
    except ValueError as exc:
        raise AdminError(400, str(exc)) from exc

    _atomic_write_json(PROGRAMS_PATH, programs)
    _atomic_write_json(ADMISSIONS_PATH, admissions)
    _audit(
        "approve",
        queue_id,
        candidate,
        client_ip,
        admission_action=review.get("admission_action"),
        program_id=program.get("id"),
        admission_id=admission.get("id"),
        replaced_existing=update_existing,
    )
    _remove_candidate(path, payload, candidate_id)
    return {
        "status": "ok",
        "action": review.get("admission_action"),
        "program_id": program.get("id"),
        "admission_id": admission.get("id"),
        "replaced_existing": update_existing,
    }


def _reject(queue_id: str, candidate_id: str, reason: str, client_ip: str) -> dict:
    path, payload = _load_queue(queue_id)
    candidate = _find_candidate(payload, candidate_id)
    reason = normalize_text(reason)[:500]
    _audit("reject", queue_id, candidate, client_ip, reason=reason)
    _remove_candidate(path, payload, candidate_id)
    return {"status": "ok"}


def dispatch(
    method: str,
    path: str,
    query: dict[str, list[str]],
    headers: dict[str, str],
    body: bytes,
    client_ip: str = "",
) -> tuple[int, bytes, str, dict[str, str]] | None:
    if path == "/admin":
        if method != "GET":
            return _response_text(405, "method not allowed")
        html = ADMIN_HTML_PATH.read_bytes()
        security = _security_headers()
        security["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'"
        )
        return 200, html, "text/html; charset=utf-8", security

    if not path.startswith("/api/admin/"):
        return None

    try:
        if method == "GET" and path == "/api/admin/status":
            return _response_json(200, {"configured": _configured()})

        _require_token(headers)
        if method == "GET" and path == "/api/admin/pending":
            with _lock:
                return _response_json(200, _pending_payload())
        if method == "POST" and path == "/api/admin/upload":
            with _lock:
                result = _upload(_query_value(query, "name", "candidates.json"), body)
            return _response_json(201, result)
        if method == "POST" and path == "/api/admin/approve":
            with _lock:
                result = _approve(
                    _query_value(query, "queue_id"),
                    _query_value(query, "candidate_id"),
                    _json_body(body),
                    client_ip,
                )
            return _response_json(200, result)
        if method == "POST" and path == "/api/admin/reject":
            form = _json_body(body)
            with _lock:
                result = _reject(
                    _query_value(query, "queue_id"),
                    _query_value(query, "candidate_id"),
                    str(form.get("reason") or ""),
                    client_ip,
                )
            return _response_json(200, result)
        return _response_json(404, {"error": "管理接口不存在"})
    except AdminError as exc:
        return _response_json(exc.status, {"error": exc.message})
    except Exception as exc:
        return _response_json(500, {"error": f"{type(exc).__name__}: {exc}"})
