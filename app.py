"""本地开发用的 HTTP 服务（标准库）。

生产环境走 wsgi.py（PythonAnywhere 只支持 WSGI），
两者调用同一个 litrag.service，逻辑只写一遍。

启动：
    python app.py          → http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag import service  # noqa: E402
from litrag.page import get_page  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    @property
    def session(self) -> str:
        return self.headers.get("X-Session", "")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._send(200, get_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/docs":
            self._json(200, service.list_documents(self.session))
            return
        if path == "/api/radar":
            self._json(200, service.list_radar_items())
            return
        if path == "/api/file":
            try:
                doc_id = (parse_qs(parsed.query).get("doc") or [""])[0]
                target, filename = service.get_document_file(self.session, doc_id)
                safe_name = quote(filename, safe="")
                self._send(
                    200,
                    target.read_bytes(),
                    "application/pdf",
                    {
                        "Content-Disposition": f"inline; filename*=UTF-8''{safe_name}",
                        "Cache-Control": "private, max-age=300",
                    },
                )
            except ValueError as exc:
                self._json(404, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/session":
                self._json(200, service.clear_session(self.session))
                return
            if path == "/api/doc":
                doc_id = (parse_qs(urlparse(self.path).query).get("doc") or [""])[0]
                self._json(200, service.delete_document(self.session, doc_id))
                return
            self._send(404, b"not found", "text/plain; charset=utf-8")
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)

        try:
            if parsed.path == "/api/ask":
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._json(200, service.ask(
                    payload.get("question", ""),
                    self.session,
                    demo=bool(payload.get("demo")),
                ))
                return

            if parsed.path == "/api/upload":
                name = (parse_qs(parsed.query).get("name") or ["upload.pdf"])[0]
                data = self.rfile.read(length)
                if not data:
                    raise ValueError("没有收到文件内容")
                self._json(200, service.add_document(self.session, name, data))
                return

            self._send(404, b"not found", "text/plain; charset=utf-8")
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:  # 安静一点
        return


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"已启动：http://{host}:{port}  （Ctrl+C 停止）")
    server.serve_forever()
