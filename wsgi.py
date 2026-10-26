"""WSGI 入口：给 PythonAnywhere 这类只支持 WSGI 的平台用。

为什么需要它：
    本项目的 app.py 用的是标准库 http.server，它不是 WSGI 应用。
    PythonAnywhere 的免费层只接受 WSGI，所以这里写一个薄薄的适配层，
    把同样的逻辑（首页 + /api/ask）暴露成 WSGI 接口。

本地自测：
    python -m wsgiref.simple_server wsgi 8001
    然后浏览器打开 http://127.0.0.1:8001
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import PAGE, RETRIEVER  # noqa: E402
from litrag.answerer import answer  # noqa: E402


def _respond(start_response, status: str, body: bytes, content_type: str):
    start_response(
        status,
        [("Content-Type", content_type), ("Content-Length", str(len(body)))],
    )
    return [body]


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")

    if method == "GET" and path in ("/", "/index.html"):
        return _respond(start_response, "200 OK", PAGE.encode("utf-8"),
                        "text/html; charset=utf-8")

    if method == "POST" and path == "/api/ask":
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            payload = json.loads(environ["wsgi.input"].read(length) or b"{}")
            question = (payload.get("question") or "").strip()
            if not question:
                raise ValueError("问题不能为空")

            hits = RETRIEVER.search(question, k=5)
            result = answer(question, hits, use_llm=True)
            body = json.dumps(
                {
                    "answer": result.text,
                    "mode": result.mode,
                    "warning": result.warning,
                    "citations": [c.__dict__ for c in result.citations],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            return _respond(start_response, "200 OK", body,
                            "application/json; charset=utf-8")
        except Exception as exc:
            body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            return _respond(start_response, "400 Bad Request", body,
                            "application/json; charset=utf-8")

    return _respond(start_response, "404 Not Found", b"not found", "text/plain")
