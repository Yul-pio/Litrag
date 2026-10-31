"""北京大学硕士招生专业目录采集器。

只负责发现官方 PDF 链接并生成候选记录，不直接发布。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

BASE_URL = "https://admission.pku.edu.cn/zsxx/sszs/zyml/{year}/yx/zsml_ss_yx.html"
SOURCE_ID = "pku-grad"
SCHOOL_ID = "pku"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


class PdfLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = normalize_text("".join(self._text))
        if text:
            self.links.append((self._href, text))
        self._href = None
        self._text = []


def fetch_html(url: str, timeout: int = 30) -> str:
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "litrag-content-collector/1.0 (+https://github.com/Yul-pio/litrag)",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _candidate_id(year: int, college: str) -> str:
    digest = hashlib.sha1(college.encode("utf-8")).hexdigest()[:8]
    return f"pku-{year}-catalog-{digest}"


def collect_pku_catalog(
    year: int = 2027,
    html: str | None = None,
    college_filter: str | None = None,
    fetched_at: str | None = None,
) -> dict:
    source_url = BASE_URL.format(year=year)
    page = html if html is not None else fetch_html(source_url)
    parser = PdfLinkParser()
    parser.feed(page)

    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    items: list[dict] = []
    seen: set[str] = set()
    for href, link_text in parser.links:
        if not href.lower().split("?")[0].endswith(".pdf"):
            continue
        if college_filter and college_filter not in link_text:
            continue
        pdf_url = urljoin(source_url, href)
        if pdf_url in seen:
            continue
        seen.add(pdf_url)
        college = link_text
        items.append({
            "candidate_id": _candidate_id(year, college),
            "candidate_type": "admission_catalog",
            "status": "candidate",
            "school_id": SCHOOL_ID,
            "source_id": SOURCE_ID,
            "year": year,
            "document_type": "招生专业目录",
            "college": college,
            "title": f"北京大学{year}年硕士研究生招生专业目录（{college}）",
            "source_url": pdf_url,
            "source_label": "北京大学研究生招生网 · 官方 PDF",
            "source_kind": "PDF",
            "page_location": f"招生目录列表 → {college}",
            "fetched_at": fetched_at,
            "needs_review": True,
            "raw": {
                "list_url": source_url,
                "link_text": college,
            },
        })

    return {
        "schema_version": 1,
        "candidate_type": "admission_catalog",
        "school_id": SCHOOL_ID,
        "source_id": SOURCE_ID,
        "year": year,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "count": len(items),
        "items": items,
    }