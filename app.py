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

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>litrag · 文献阅读助手</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         max-width: 760px; margin: 48px auto; padding: 0 20px; line-height: 1.7; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .sub { color: #888; font-size: 13px; margin-bottom: 28px; }
  textarea { width: 100%; box-sizing: border-box; padding: 12px; font-size: 15px;
             border: 1px solid #ccc; border-radius: 8px; min-height: 84px;
             font-family: inherit; resize: vertical; }
  button { margin-top: 10px; padding: 10px 20px; font-size: 15px; cursor: pointer;
           border: 0; border-radius: 8px; background: #2f6feb; color: #fff; }
  button:disabled { background: #9ab; cursor: wait; }
  #out { margin-top: 28px; display: none; }
  .answer { white-space: pre-wrap; }
  .cite { margin-top: 18px; font-size: 13px; color: #666; border-top: 1px dashed #ccc;
          padding-top: 14px; }
  .cite div { margin-bottom: 10px; }
  .warn { margin-top: 14px; padding: 10px 12px; background: #fff4e5; color: #8a5a00;
          border-radius: 8px; font-size: 13px; }
</style>
</head>
<body>
  <h1>litrag · 文献阅读助手</h1>
  <div class="sub">每个结论都能点回原文 —— v0.1</div>

  <textarea id="q" placeholder="问点什么，比如：为什么要分块？"></textarea>
  <button id="go">提问</button>

  <div id="out">
    <div class="answer" id="answer"></div>
    <div class="warn" id="warn" style="display:none"></div>
    <div class="cite" id="cite"></div>
  </div>

<script>
const q = document.getElementById('q');
const go = document.getElementById('go');
const out = document.getElementById('out');
const answerEl = document.getElementById('answer');
const citeEl = document.getElementById('cite');
const warnEl = document.getElementById('warn');

async function ask() {
  const question = q.value.trim();
  if (!question) return;
  go.disabled = true; go.textContent = '检索中…';
  try {
    const res = await fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question})
    });
    const data = await res.json();
    answerEl.textContent = data.answer;
    citeEl.innerHTML = data.citations.map(c =>
      `<div><b>[${c.id}]</b> ${c.doc} / ${c.section}` +
      (c.page ? `，第 ${c.page} 页` : '') +
      `<br>「${c.quote}」</div>`
    ).join('');
    warnEl.style.display = data.warning ? 'block' : 'none';
    warnEl.textContent = data.warning || '';
    out.style.display = 'block';
  } catch (err) {
    answerEl.textContent = '请求失败：' + err;
    out.style.display = 'block';
  } finally {
    go.disabled = false; go.textContent = '提问';
  }
}

go.addEventListener('click', ask);
q.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') ask();
});
</script>
</body>
</html>
"""


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
