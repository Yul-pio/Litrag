"""清华大学硕士统招专业目录采集器。

清华官方目录是 HTML 表格而不是 PDF。采集器只解析表格并生成待审核候选，
不修改正式专业和招生记录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser

BASE_DETAIL_URL = (
    "https://yzbm.tsinghua.edu.cn/publish/s02/s0203/detail/"
    "c0fb8664-255f-4d6f-bf40-a5893e3343ec"
)
DEFAULT_DEPARTMENT_CODE = "024"
SOURCE_ID = "tsinghua-grad"
SCHOOL_ID = "tsinghua"

_PAGE_YEAR_RE = re.compile(r"清华大学\s*(?P<year>\d{4})\s*年")
_DEPARTMENT_RE = re.compile(r"^(?P<code>\d{3})\s+(?P<name>.+)$")
_MAJOR_RE = re.compile(
    r"^(?P<code>\d{6})\s*(?P<degree>（[^）]+）)?\s*(?P<name>.+)$"
)
_DIRECTION_RE = re.compile(
    r"^(?P<code>\d{2})\s*(?P<study_mode>（[^）]+）)?\s*(?P<name>.+)$"
)
_CIRCLED_NUMBERS_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


@dataclass
class _Cell:
    text: str
    rowspan: int = 1
    colspan: int = 1


class CatalogTableParser(HTMLParser):
    """收集目标表格的行列文本，并保留 rowspan/colspan 布局。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_title = ""
        self.rows: list[list[_Cell]] = []
        self._table_depth = 0
        self._target_depth: int | None = None
        self._row: list[_Cell] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_rowspan = 1
        self._cell_colspan = 1
        self._h1_depth = 0
        self._h1_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = dict(attrs)

        if tag == "h1":
            self._h1_depth += 1
            if self._h1_depth == 1:
                self._h1_parts = []

        if tag == "table":
            self._table_depth += 1
            if attrs_map.get("id") == "zsml-table" and self._target_depth is None:
                self._target_depth = self._table_depth
            return

        if self._target_depth is None or self._table_depth != self._target_depth:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []
            self._cell_rowspan = _positive_int(attrs_map.get("rowspan"))
            self._cell_colspan = _positive_int(attrs_map.get("colspan"))
        elif tag == "br" and self._cell_parts is not None:
            self._cell_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._h1_depth:
            self._h1_parts.append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h1" and self._h1_depth:
            self._h1_depth -= 1
            if self._h1_depth == 0:
                self.page_title = normalize_text("".join(self._h1_parts))

        if self._target_depth is None or self._table_depth != self._target_depth:
            if tag == "table" and self._table_depth:
                self._table_depth -= 1
            return

        if tag in {"td", "th"} and self._cell_parts is not None:
            if self._row is not None:
                self._row.append(
                    _Cell(
                        text=normalize_text("".join(self._cell_parts)),
                        rowspan=self._cell_rowspan,
                        colspan=self._cell_colspan,
                    )
                )
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._table_depth -= 1
            self._target_depth = None


def _positive_int(value: str | None) -> int:
    try:
        parsed = int(value or "1")
    except ValueError:
        return 1
    return max(parsed, 1)


def _expand_grid(rows: list[list[_Cell]]) -> list[list[str]]:
    """把 rowspan/colspan 展开成规则网格，方便按列读取字段。"""

    grid: list[list[str]] = []
    active: dict[int, tuple[int, str]] = {}
    for row_index, row in enumerate(rows):
        active = {
            column: span
            for column, span in active.items()
            if span[0] >= row_index
        }
        expanded: list[str] = []
        source_index = 0
        column = 0
        while source_index < len(row) or any(
            end_row >= row_index and active_column >= column
            for active_column, (end_row, _) in active.items()
        ):
            if column in active and active[column][0] >= row_index:
                expanded.append(active[column][1])
                column += 1
                continue
            if source_index >= len(row):
                expanded.append("")
                column += 1
                continue

            cell = row[source_index]
            source_index += 1
            for offset in range(cell.colspan):
                expanded.append(cell.text if offset == 0 else "")
                if cell.rowspan > 1:
                    active[column + offset] = (
                        row_index + cell.rowspan - 1,
                        cell.text if offset == 0 else "",
                    )
            column += cell.colspan
        grid.append(expanded)
    return grid


def _cell_at(cells: list[str], index: int) -> str:
    return cells[index] if index < len(cells) else ""


def _parse_plan(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group()) if match else None


def _parse_exam_subjects(value: str) -> list[str]:
    value = normalize_text(value)
    if not value:
        return []
    parts = [part for part in _CIRCLED_NUMBERS_RE.split(value) if part.strip()]
    return [normalize_text(part) for part in parts]


def _degree_type(label: str) -> str:
    if "专业学位" in label:
        return "专硕"
    if "学术学位" in label:
        return "学硕"
    return ""


def _detail_url(department_code: str) -> str:
    return f"{BASE_DETAIL_URL}?yxsdm={department_code}&ksfs="


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
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _matches_department(code: str, name: str, college_filter: str | None) -> bool:
    if not college_filter:
        return True
    query = normalize_text(college_filter)
    label = f"{code} {name}"
    return query == code or query in label or name in query or code in query


def collect_tsinghua_catalog(
    year: int = 2027,
    html: str | None = None,
    college_filter: str | None = None,
    fetched_at: str | None = None,
    department_code: str = DEFAULT_DEPARTMENT_CODE,
) -> dict:
    source_url = _detail_url(department_code)
    page = html if html is not None else fetch_html(source_url)
    parser = CatalogTableParser()
    parser.feed(page)

    title_year_match = _PAGE_YEAR_RE.search(parser.page_title)
    if not title_year_match:
        raise ValueError(f"无法从清华招生目录标题识别年份：{parser.page_title or '空标题'}")
    title_year = int(title_year_match.group("year"))
    if title_year != year:
        raise ValueError(f"页面年份 {title_year} 与请求年份 {year} 不一致，请更新采集地址")

    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    grid = _expand_grid(parser.rows)
    items: list[dict] = []
    department: dict | None = None
    major: dict | None = None
    include_department = False

    for cells in grid:
        first = _cell_at(cells, 0)
        if not first or "院系、专业、研究方向" in first:
            continue

        department_match = _DEPARTMENT_RE.match(first)
        if department_match:
            code = department_match.group("code")
            name = normalize_text(department_match.group("name"))
            include_department = _matches_department(code, name, college_filter)
            department = {
                "code": code,
                "name": name,
                "notes": _cell_at(cells, 3),
            }
            major = None
            continue

        major_match = _MAJOR_RE.match(first)
        if major_match:
            major = {
                "code": major_match.group("code"),
                "name": normalize_text(major_match.group("name")),
                "degree_label": major_match.group("degree") or "",
                "plan": _parse_plan(_cell_at(cells, 1)),
                "notes": _cell_at(cells, 3),
            }
            continue

        direction_match = _DIRECTION_RE.match(first)
        if not direction_match or department is None or major is None or not include_department:
            continue

        direction_code = direction_match.group("code")
        study_mode = direction_match.group("study_mode") or ""
        direction_name = normalize_text(direction_match.group("name"))
        plan = _parse_plan(_cell_at(cells, 1))
        exam_subjects = _parse_exam_subjects(_cell_at(cells, 2))
        notes = _cell_at(cells, 3) or major["notes"]
        candidate_id = (
            f"thu-{year}-{department['code']}-{major['code']}-{direction_code}"
        )

        items.append({
            "candidate_id": candidate_id,
            "candidate_type": "admission_program",
            "status": "candidate",
            "school_id": SCHOOL_ID,
            "source_id": SOURCE_ID,
            "year": year,
            "document_type": "招生专业目录",
            "college": department["name"],
            "title": (
                f"清华大学{year}年硕士研究生招生专业目录"
                f"（{department['code']} {department['name']}·统招）"
            ),
            "source_url": _detail_url(department["code"]),
            "source_label": "清华大学研究生招生网 · 官方 HTML 招生目录",
            "source_kind": "HTML 招生目录",
            "page_location": (
                f"{department['code']} {department['name']} → "
                f"{major['code']} {major['name']} → "
                f"{direction_code}{study_mode} {direction_name}"
            ),
            "fetched_at": fetched_at,
            "needs_review": True,
            "raw": {
                "page_title": parser.page_title,
                "list_url": _detail_url(department["code"]),
                "department_code": department["code"],
                "department_name": department["name"],
                "department_notes": department["notes"],
                "major_code": major["code"],
                "major_name": major["name"],
                "degree_label": major["degree_label"],
                "major_plan": major["plan"],
                "direction_code": direction_code,
                "study_mode_label": study_mode,
                "direction_name": direction_name,
                "direction_plan": plan,
                "exam_subjects_raw": _cell_at(cells, 2),
                "notes": notes,
            },
            "program": {
                "id": f"{candidate_id}-program",
                "school_id": SCHOOL_ID,
                "college": department["name"],
                "year": year,
                "major_code": major["code"],
                "major_name": major["name"],
                "degree_type": _degree_type(major["degree_label"]),
                "study_mode": study_mode.strip("（）()"),
                "direction_code": direction_code,
                "direction_name": direction_name,
                "plan": plan,
                "recommendation_quota": None,
                "program_total_plan": major["plan"],
                "program_total_recommendation": None,
                "exam_subjects": exam_subjects,
                "notes_label": "招生说明",
                "notes": notes,
                "verified_at": None,
            },
        })

    if not items:
        filter_note = f"（筛选：{college_filter}）" if college_filter else ""
        raise ValueError(f"清华招生目录未解析到方向候选{filter_note}，页面结构可能已变化")

    return {
        "schema_version": 1,
        "candidate_type": "admission_program",
        "school_id": SCHOOL_ID,
        "source_id": SOURCE_ID,
        "year": year,
        "source_url": source_url,
        "page_title": parser.page_title,
        "fetched_at": fetched_at,
        "count": len(items),
        "items": items,
    }
