"""WSGI 入口：给 PythonAnywhere 这类只支持 WSGI 的平台用。

路由：
    GET  /                 首页
    GET  /api/docs         当前会话已上传的文档列表
    POST /api/ask          {"question": "..."}  → 回答
    POST /api/upload?name=xx.pdf  （原始字节）  → 解析并入库

会话靠请求头 X-Session 区分（浏览器生成，存在 localStorage）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag import service  # noqa: E402

PAGE_FILE = ROOT / "web" / "index.html"
PAGE = PAGE_FILE.read_text(encoding="utf-8") if PAGE_FILE.exists() else "<h1>litrag</h1>"


def _respond(start_response, status: str, body: bytes, content_type: str):
    start_response(
        status,
        [("Content-Type", content_type), ("Content-Length", str(len(body)))],
    )
    return [body]


def _json(start_response, status: str, payload: dict):
    return _respond(
        start_response,
        status,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    sid = environ.get("HTTP_X_SESSION", "")

    if method == "GET" and path in ("/", "/index.html"):
        return _respond(start_response, "200 OK", PAGE.encode("utf-8"),
                        "text/html; charset=utf-8")

    try:
        if method == "GET" and path == "/api/docs":
            return _json(start_response, "200 OK", service.list_documents(sid))

        if method == "POST" and path == "/api/ask":
            length = int(environ.get("CONTENT_LENGTH") or 0)
            payload = json.loads(environ["wsgi.input"].read(length) or b"{}")
            return _json(start_response, "200 OK",
                         service.ask(payload.get("question", ""), sid))

        if method == "POST" and path == "/api/upload":
            query = parse_qs(environ.get("QUERY_STRING", ""))
            name = (query.get("name") or ["upload.pdf"])[0]
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length <= 0:
                raise ValueError("没有收到文件内容")
            data = environ["wsgi.input"].read(length)
            return _json(start_response, "200 OK", service.add_document(sid, name, data))
    except ValueError as exc:
        return _json(start_response, "400 Bad Request", {"error": str(exc)})
    except Exception as exc:  # 兜底，别把 500 页面直接丢给用户
        return _json(start_response, "500 Internal Server Error",
                     {"error": f"{type(exc).__name__}: {exc}"})

    return _respond(start_response, "404 Not Found", b"not found", "text/plain")
