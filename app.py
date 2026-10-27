"""极简 Web 界面（只用标准库，不需要 flask / fastapi）。

启动：
    python app.py
然后打开 http://127.0.0.1:8000

它现在只是个「能用的验证界面」。等你确认有人愿意用，再换成 Next.js
或 FastAPI + 付费墙，那才是产品化的阶段。
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.answerer import answer
from litrag.retriever import Retriever
INDEX = ROOT / ".index" / "index.json"
RETRIEVER = Retriever.load(INDEX)

PAGE_FILE = ROOT / "web" / "index.html"
if PAGE_FILE.exists():
    PAGE = PAGE_FILE.read_text(encoding="utf-8")
else:
    PAGE = "<h1>litrag</h1><p>缺少 web/index.html</p>"


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/ask":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
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
            self._send(200, body, "application/json; charset=utf-8")
        except Exception as exc:
            body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
            self._send(400, body, "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:  # 安静一点
        return


if __name__ == "__main__":
    if not INDEX.exists():
        sys.exit("索引不存在，请先运行：python run.py ingest data/docs")

    # 本地开发监听 127.0.0.1；云平台会注入 PORT 环境变量，
    # 这时必须监听 0.0.0.0，否则外网访问不到。
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"已启动：http://{host}:{port}  （Ctrl+C 停止）")
    server.serve_forever()
