"""生成一段演示动画（GIF），用项目真实的检索结果渲染。

运行：
    python demo/make_demo.py

产物：
    demo/litrag-demo.gif

为什么要做这个：
    复试和路演都需要「十秒钟看懂你在干什么」的东西。
    一页 PPT 讲不清「检索 → 判断 → 生成 → 溯源」，一段动画可以。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from litrag.answerer import _coverage, answer  # noqa: E402
from litrag.retriever import Retriever  # noqa: E402

# ---------------------------------------------------------------- 画布与配色
W, H = 900, 660
BG = (13, 18, 30)
PANEL = (24, 31, 48)
PANEL_2 = (30, 39, 60)
FG = (234, 238, 247)
MUTED = (135, 148, 172)
DIM = (90, 102, 124)
ACCENT = (98, 160, 255)
GREEN = (72, 199, 142)
AMBER = (240, 180, 90)

FONT_DIR = Path("C:/Windows/Fonts")
FONT_REG = FONT_DIR / "msyh.ttc"
FONT_BOLD = FONT_DIR / "msyhbd.ttc"
FONT_MONO = FONT_DIR / "consola.ttf"

QUESTION = "相邻块为什么要保留重叠？"
INDEX = ROOT / ".index" / "index.json"
OUT = ROOT / "demo" / "litrag-demo.gif"


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO if mono and FONT_MONO.exists() else (FONT_BOLD if bold else FONT_REG)
    return ImageFont.truetype(str(path), size)


F_TITLE = font(26, bold=True)
F_SUB = font(15)
F_BODY = font(19)
F_BODY_B = font(20, bold=True)
F_SMALL = font(14)
F_LABEL = font(15, bold=True)
F_MONO = font(15, mono=True)


def wrap(text: str, fnt: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if fnt.getlength(cur + ch) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def mix(c1, c2, t: float):
    """颜色插值，用来做淡入。"""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def rounded(d, box, radius, fill, outline=None, width=1):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def render(
    q_progress: float,
    row_progress: list[float],
    gate_progress: float,
    ans_progress: float,
    cite_progress: float,
    hits,
    coverage: float,
) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 顶部标题栏
    d.text((44, 28), "litrag", font=F_TITLE, fill=ACCENT)
    d.text((44 + F_TITLE.getlength("litrag") + 12, 36), "文献阅读助手", font=F_SUB, fill=MUTED)
    d.text((W - 44 - F_MONO.getlength("v0.1 demo"), 36), "v0.1 demo", font=F_MONO, fill=DIM)
    d.line((44, 74, W - 44, 74), fill=(38, 47, 68), width=1)

    # Q 面板
    rounded(d, (44, 92, W - 44, 168), 10, PANEL)
    d.text((66, 108), "Q", font=F_LABEL, fill=ACCENT)
    q_text = QUESTION[: int(len(QUESTION) * q_progress)]
    d.text((94, 106), q_text, font=F_BODY, fill=FG)

    # ① 检索
    d.text((44, 190), "① 检索", font=F_LABEL, fill=ACCENT)
    d.text((44 + 70, 192), "BM25 · 取前 3 条", font=F_SMALL, fill=DIM)

    max_score = max((h.score for h in hits[:3]), default=1.0) or 1.0
    for i, hit in enumerate(hits[:3]):
        p = row_progress[i]
        if p <= 0:
            continue
        y = 216 + i * 42
        alpha = min(1.0, p * 1.6)
        rounded(d, (44, y, W - 44, y + 34), 8, mix(BG, PANEL, alpha))
        d.text((60, y + 8), f"{i + 1}", font=F_MONO, fill=mix(BG, ACCENT, alpha))
        label = f"{hit.chunk.doc_id} / {hit.chunk.section}"
        d.text((86, y + 7), label, font=F_SMALL, fill=mix(BG, FG, alpha))
        # 分数条
        bar_x, bar_w = 560, 200
        d.rectangle((bar_x, y + 13, bar_x + bar_w, y + 21), fill=mix(BG, PANEL_2, alpha))
        fill_w = int(bar_w * (hit.score / max_score) * min(1.0, p))
        if fill_w > 0:
            color = GREEN if i == 0 else mix(BG, ACCENT, alpha * 0.7)
            d.rectangle((bar_x, y + 13, bar_x + fill_w, y + 21), fill=color)
        d.text((bar_x + bar_w + 14, y + 8), f"{hit.score:.1f}", font=F_MONO, fill=mix(BG, MUTED, alpha))

    # ② 判断
    gate_a = gate_progress
    d.text((44, 360), "② 判断", font=F_LABEL, fill=mix(BG, ACCENT, gate_a))
    d.text((44 + 70, 362), "资料里到底有没有？", font=F_SMALL, fill=mix(BG, DIM, gate_a))
    if gate_a > 0:
        rounded(d, (44, 386, W - 44, 432), 8, mix(BG, (18, 40, 34), gate_a))
        d.text(
            (62, 401),
            f"词项覆盖率 {coverage:.0%}  ≥  阈值 20%",
            font=F_SMALL,
            fill=mix(BG, GREEN, gate_a),
        )
        d.text((360, 401), "→  有依据，继续回答", font=F_SMALL, fill=mix(BG, MUTED, gate_a))

    # ③ 生成
    ans_a = ans_progress
    d.text((44, 452), "③ 生成", font=F_LABEL, fill=mix(BG, ACCENT, ans_a))
    d.text((44 + 70, 454), "抽取原文，不做改写", font=F_SMALL, fill=mix(BG, DIM, ans_a))
    if ans_a > 0:
        body = (
            "为了缓解边界截断，通常会让相邻块之间保留一段重叠，"
            "常见比例是块长的百分之十到百分之二十。[1]"
        )
        lines = wrap(body, F_BODY, W - 130)
        shown_chars = int(sum(len(x) for x in lines) * ans_a)
        acc = 0
        for i, line in enumerate(lines):
            if acc >= shown_chars:
                break
            piece = line[: max(0, shown_chars - acc)]
            d.text((62, 480 + i * 30), piece, font=F_BODY, fill=FG)
            acc += len(line)

    # ④ 溯源
    cite_a = cite_progress
    d.text((44, 556), "④ 溯源", font=F_LABEL, fill=mix(BG, ACCENT, cite_a))
    d.text((44 + 70, 558), "每句话都能点回原文", font=F_SMALL, fill=mix(BG, DIM, cite_a))
    if cite_a > 0 and hits:
        top = hits[0].chunk
        rounded(d, (44, 582, W - 44, 624), 8, mix(BG, PANEL_2, cite_a))
        # 注意：这里必须用中文字体，等宽字体没有汉字字形，会渲染成方块
        d.text((62, 594), f"[1]  {top.doc_id} / {top.section}", font=F_SMALL, fill=mix(BG, ACCENT, cite_a))
        quote = top.text[:30] + "…"
        d.text((320, 595), f"「{quote}」", font=F_SMALL, fill=mix(BG, MUTED, cite_a))

    # 页脚
    d.text(
        (44, H - 22),
        "RAG · 执行路径固定，可评测、可复现 —— 这不是 Agent",
        font=F_SMALL,
        fill=DIM,
    )
    return img


def main() -> None:
    if not INDEX.exists():
        sys.exit("索引不存在，请先运行：python run.py ingest data/docs")

    retriever = Retriever.load(INDEX)
    hits = retriever.search(QUESTION, k=5)
    result = answer(QUESTION, hits, use_llm=False)
    coverage = _coverage(QUESTION, hits)
    print(f"真实检索结果：{[f'{h.chunk.doc_id}/{h.chunk.section}({h.score:.1f})' for h in hits[:3]]}")
    print(f"词项覆盖率：{coverage:.0%}")
    print(f"回答模式：{result.mode}")

    frames: list[Image.Image] = []
    durations: list[int] = []

    def add(img: Image.Image, ms: int) -> None:
        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=64))
        durations.append(ms)

    # 场景一：问题打字
    steps = 12
    for i in range(steps):
        add(render(i / (steps - 1), [0, 0, 0], 0, 0, 0, hits, coverage), 70)

    # 场景二：三条召回依次出现
    progress = [0.0, 0.0, 0.0]
    for i in range(3):
        for j in range(5):
            progress[i] = (j + 1) / 5
            add(render(1, list(progress), 0, 0, 0, hits, coverage), 70)

    # 场景三：拒答判断
    for j in range(5):
        add(render(1, [1, 1, 1], (j + 1) / 5, 0, 0, hits, coverage), 70)

    # 场景四：答案逐字出现
    for j in range(10):
        add(render(1, [1, 1, 1], 1, (j + 1) / 10, 0, hits, coverage), 70)

    # 场景五：引用卡片
    for j in range(5):
        add(render(1, [1, 1, 1], 1, 1, (j + 1) / 5, hits, coverage), 70)

    # 场景六：停留
    for _ in range(14):
        add(render(1, [1, 1, 1], 1, 1, 1, hits, coverage), 120)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        OUT,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    size_kb = OUT.stat().st_size / 1024
    print(f"已生成 {OUT}（{len(frames)} 帧，{size_kb:.0f} KB）")


if __name__ == "__main__":
    main()
