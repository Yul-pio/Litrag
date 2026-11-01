"""WSGI 入口：给 PythonAnywhere 这类只支持 WSGI 的平台用。

路由：
    GET  /                 首页
    GET  /api/docs         当前会话已上传的文档列表
    GET  /api/file?doc=xx  打开当前会话有权访问的 PDF
    POST /api/ask          {"question": "..."}  → 回答
    POST /api/upload?name=xx.pdf  （原始字节）  → 解析并入库
    DELETE /api/session    清空当前会话的上传文件和索引
    GET  /admin            内部招生候选审核页
    GET/POST /api/admin/*  受 LITRAG_ADMIN_TOKEN 保护的审核接口

会话靠请求头 X-Session 区分（浏览器生成，存在 localStorage）。
"""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, quote

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag import admin, service  # noqa: E402
from litrag.page import get_page  # noqa: E402


def _respond(start_response, status: str, body: bytes, content_type: str, headers=None):
    response_headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    response_headers.extend((headers or {}).items())
    start_response(status, response_headers)
    return [body]


def _json(start_response, status: str, payload: dict):
    return _respond(
        start_response,
        status,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def _admin_response(start_response, result):
    status, body, content_type, headers = result
    return _respond(
        start_response,
        f"{status} {HTTPStatus(status).phrase}",
        body,
        content_type,
        headers,
    )


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    sid = environ.get("HTTP_X_SESSION", "")
    query_string = environ.get("QUERY_STRING", "")
    length = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(length) if length > 0 else b""

    admin_response = admin.dispatch(
        method,
        path,
        parse_qs(query_string),
        {
            "x-admin-token": environ.get("HTTP_X_ADMIN_TOKEN", ""),
            "content-type": environ.get("CONTENT_TYPE", ""),
        },
        body,
        environ.get("REMOTE_ADDR", ""),
    )
    if admin_response is not None:
        return _admin_response(start_response, admin_response)

    if method == "GET" and path in ("/", "/index.html"):
        return _respond(start_response, "200 OK", get_page().encode("utf-8"),
                        "text/html; charset=utf-8")

    try:
        if method == "GET" and path == "/api/docs":
            return _json(start_response, "200 OK", service.list_documents(sid))

        if method == "GET" and path == "/api/radar":
            return _json(start_response, "200 OK", service.list_radar_items())

        if method == "GET" and path == "/api/schools":
            return _json(start_response, "200 OK", service.list_schools())

        if method == "GET" and path == "/api/sources":
            return _json(start_response, "200 OK", service.list_sources())

        if method == "GET" and path == "/api/programs":
            return _json(start_response, "200 OK", service.list_programs())

        if method == "GET" and path == "/api/admissions":
            return _json(start_response, "200 OK", service.list_admission_records())

        if method == "GET" and path == "/api/file":
            query = parse_qs(environ.get("QUERY_STRING", ""))
            doc_id = (query.get("doc") or [""])[0]
            target, filename = service.get_document_file(sid, doc_id)
            safe_name = quote(filename, safe="")
            return _respond(
                start_response,
                "200 OK",
                target.read_bytes(),
                "application/pdf",
                {
                    "Content-Disposition": f"inline; filename*=UTF-8''{safe_name}",
                    "Cache-Control": "private, max-age=300",
                },
            )

        if method == "DELETE" and path == "/api/session":
            return _json(start_response, "200 OK", service.clear_session(sid))

        if method == "DELETE" and path == "/api/doc":
            query = parse_qs(environ.get("QUERY_STRING", ""))
            doc_id = (query.get("doc") or [""])[0]
            return _json(start_response, "200 OK", service.delete_document(sid, doc_id))

        if method == "POST" and path == "/api/ask":
            payload = json.loads(body or b"{}")
            return _json(start_response, "200 OK", service.ask(
                payload.get("question", ""),
                sid,
                demo=bool(payload.get("demo")),
            ))

        if method == "POST" and path == "/api/upload":
            query = parse_qs(query_string)
            name = (query.get("name") or ["upload.pdf"])[0]
            if not body:
                raise ValueError("没有收到文件内容")
            return _json(start_response, "200 OK", service.add_document(sid, name, body))
    except ValueError as exc:
        return _json(start_response, "400 Bad Request", {"error": str(exc)})
    except Exception as exc:  # 兜底，别把 500 页面直接丢给用户
        return _json(start_response, "500 Internal Server Error",
                     {"error": f"{type(exc).__name__}: {exc}"})

    return _respond(start_response, "404 Not Found", b"not found", "text/plain")
