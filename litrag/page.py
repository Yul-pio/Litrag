"""页面加载：按需读取 web/index.html，并带一层 mtime 缓存。

为什么不在 import 时读一次：
    早期版本把 HTML 读进模块常量，结果改完 `web/index.html`、刷新浏览器
    什么都不会变，必须重启服务——做界面微调时非常折磨。
    现在每次请求检查 mtime，文件一变就重读，服务不用重启。
"""

from __future__ import annotations

from pathlib import Path

PAGE_FILE = Path(__file__).resolve().parents[1] / "web" / "index.html"
FALLBACK = "<h1>litrag</h1><p>缺少 web/index.html</p>"

_cache: dict[str, object] = {"mtime": None, "html": FALLBACK}


def get_page() -> str:
    """返回当前页面 HTML；文件没变时用缓存。"""
    try:
        mtime = PAGE_FILE.stat().st_mtime
    except OSError:
        return FALLBACK

    if _cache["mtime"] != mtime:
        try:
            _cache["html"] = PAGE_FILE.read_text(encoding="utf-8")
            _cache["mtime"] = mtime
        except OSError:
            return str(_cache["html"])

    return str(_cache["html"])
