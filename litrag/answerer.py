"""回答生成 + 引用溯源。

两种模式：
1. 抽取式（默认，不需要任何 API Key）：从召回的块里挑出最相关的句子，
   直接引用原文，并给每句标上来源编号。优点是绝不胡编。
2. LLM 生成（配置了 LITRAG_API_KEY 时启用）：把召回内容拼进提示词，
   要求模型只用资料作答、并标注 [序号]。生成后会做「引用校验」，
   把模型编造的、不存在的编号挑出来（这是学术场景最关键的一步）。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .bm25 import content_terms, tokenize
from .retriever import Hit

_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_CITE_RE = re.compile(r"\[(\d+)\]")

# ---------------------------------------------------------------- 拒答判断
# 早期版本只看「前 3 块」的词项覆盖率，阈值 20%。在 20 题不可回答集上
# 误答率高达 55%：因为把 3 个块拼起来，总能凑出几个查询词。
#
# 改成两个特征：
#   1) cov_top1：只看第 1 名的块。它才是真正会被用来回答的块。
#   2) top_score：第 1 名的 BM25 原始分数。
#
# 实验发现（dev = 66 题可回答 + 20 题不可回答）：
#   - 单用 cov_top1 >= 0.34：英文论文集通过 91~95%，误答 10%，
#     但中文短语料库（3 篇示例）通过率只有 58%，同义改写难例只有 25%。
#   - 短语料库里 BM25 分数天然更高（中位数 40 vs 论文的 17），
#     所以补一个「绝对分数」条件，把被覆盖率误杀的中文问题救回来。
#
# 最终规则：cov_top1 >= 0.34 或 top_score >= 20。
# 注意：绝对分数依赖语料规模，换语料要重新校准；这是已知局限。
MIN_COVERAGE = 0.34
MIN_TOP_SCORE = 20.0

# 第 2 名及以后的块，得分不到第 1 名的这个比例，就不再从它里面取句子。
# 不加这条限制，答案里会混进「勉强相关」的噪声句。
RELATIVE_CHUNK_THRESHOLD = 0.6


@dataclass
class Citation:
    id: int
    chunk_id: str
    doc: str
    section: str
    page: int | None
    quote: str


@dataclass
class Answer:
    text: str
    citations: list[Citation]
    mode: str                 # "extractive" | "llm" | "none"
    warning: str | None = None


def _to_citation(idx: int, hit: Hit, quote: str) -> Citation:
    return Citation(
        id=idx,
        chunk_id=hit.chunk.chunk_id,
        doc=hit.chunk.doc_id,
        section=hit.chunk.section,
        page=hit.chunk.page,
        quote=quote.strip()[:160],
    )


def _coverage(question: str, hits: list[Hit], top_n: int = 3) -> float:
    """问题里的实词有多大比例出现在召回内容中。

    这是「拒答」的依据：没有这个判断，任何问题都能被 BM25 凑出一个结果，
    用户会以为系统在胡说。学术场景宁可拒答，也不能编。
    """
    terms = set(content_terms(question))
    if not terms:
        return 1.0
    context: set[str] = set()
    for hit in hits[:top_n]:
        context.update(tokenize(f"{hit.chunk.section}\n{hit.chunk.text}"))
    return len(terms & context) / len(terms)


def is_answerable(
    question: str,
    hits: list[Hit],
    coverage_threshold: float | None = None,
    score_threshold: float | None = None,
) -> tuple[bool, dict]:
    """判断「资料里到底有没有答案」。

    两个特征取「或」：
      - cov_top1：第 1 名块覆盖了多少查询实词（对长文档可靠）
      - top_score：第 1 名的原始 BM25 分数（补偿短语料库覆盖率偏低）

    传入 coverage_threshold 时只按覆盖率判断（供阈值扫描做对照实验用）。
    """
    if not hits:
        return False, {"coverage_top1": 0.0, "top_score": 0.0, "reason": "no_hits"}

    coverage_top1 = _coverage(question, hits, top_n=1)
    top_score = hits[0].score

    if coverage_threshold is not None:
        ok = coverage_top1 >= coverage_threshold
        return ok, {
            "coverage_top1": coverage_top1,
            "top_score": top_score,
            "reason": "coverage_only",
        }

    cov_limit = MIN_COVERAGE
    score_limit = MIN_TOP_SCORE if score_threshold is None else score_threshold
    ok = coverage_top1 >= cov_limit or top_score >= score_limit
    reason = "coverage" if coverage_top1 >= cov_limit else ("score" if ok else "low")
    return ok, {"coverage_top1": coverage_top1, "top_score": top_score, "reason": reason}


def _extractive(question: str, hits: list[Hit], max_sentences: int = 3) -> Answer:
    """从召回的块里挑原文句子拼成答案。

    排序逻辑：先按块的得分顺序看，每块最多取两句、并保持句子的原始顺序。
    这样读起来是「最相关的那一节先说，其次才是补充」，而不是句子满天飞。
    """
    q_tokens = set(tokenize(question))
    picked: list[tuple[int, int, str, Hit]] = []
    best_score = hits[0].score if hits else 0.0

    for idx, hit in enumerate(hits, start=1):
        if idx > 1 and hit.score < best_score * RELATIVE_CHUNK_THRESHOLD:
            break
        scored: list[tuple[float, int, str]] = []
        for pos, sent in enumerate(_SENT_SPLIT.split(hit.chunk.text)):
            sent = sent.strip()
            if len(sent) < 12:
                continue
            s_tokens = set(tokenize(sent))
            overlap = len(q_tokens & s_tokens)
            if overlap == 0:
                continue
            # 用 sqrt 长度归一化，避免长句仅因为词多就占优
            score = overlap / (len(s_tokens) ** 0.5)
            scored.append((score, pos, sent))

        scored.sort(key=lambda x: x[0], reverse=True)
        for _, pos, sent in scored[:2]:
            picked.append((idx, pos, sent, hit))
        if len(picked) >= max_sentences:
            break

    if not picked:
        top = hits[0]
        sent = top.chunk.text[:200]
        return Answer(
            text=f"没有找到与问题高度相关的句子，以下是最接近的原文片段[1]：\n{sent}",
            citations=[_to_citation(1, top, sent)],
            mode="extractive",
        )

    picked = picked[:max_sentences]
    picked.sort(key=lambda x: (x[0], x[1]))  # 按块顺序，块内按原文顺序

    body = "\n\n".join(f"{sent}[{idx}]" for idx, _, sent, _ in picked)
    citations, used = [], set()
    for idx, _, sent, hit in picked:
        if idx in used:
            continue
        used.add(idx)
        citations.append(_to_citation(idx, hit, sent))

    return Answer(text=body, citations=citations, mode="extractive")


def _llm(question: str, hits: list[Hit]) -> Answer:
    api_key = os.getenv("LITRAG_API_KEY")
    if not api_key:
        raise RuntimeError("未配置 LITRAG_API_KEY")

    base = os.getenv("LITRAG_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("LITRAG_MODEL", "deepseek-chat")

    context = "\n\n".join(f"[{i}] {h.chunk.text}" for i, h in enumerate(hits, start=1))
    system = (
        "你是学术文献阅读助手。规则：\n"
        "1. 只能依据给定资料回答，资料里没有的信息直接说“资料中未提及”。\n"
        "2. 每个结论后面必须用 [序号] 标注来源，序号对应资料编号。\n"
        "3. 不要编造序号，不要输出资料之外的知识。\n"
        "4. 用简洁的中文作答。"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"资料：\n{context}\n\n问题：{question}"},
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"LLM 接口返回 {exc.code}：{detail}") from exc

    text = data["choices"][0]["message"]["content"].strip()

    # 引用校验：模型可能编出不存在的编号，这里逐一核对
    used_ids = {int(x) for x in _CITE_RE.findall(text)}
    valid_ids = {i for i in used_ids if 1 <= i <= len(hits)}
    citations = [_to_citation(i, hits[i - 1], hits[i - 1].chunk.text) for i in sorted(valid_ids)]

    warning = None
    invalid = used_ids - valid_ids
    if invalid:
        warning = f"检测到 {len(invalid)} 个不存在的引用编号：{sorted(invalid)}"
    if not used_ids:
        warning = "模型未输出任何引用标注，可信度存疑"

    return Answer(text=text, citations=citations, mode="llm", warning=warning)


def answer(
    question: str,
    hits: list[Hit],
    use_llm: bool = True,
    min_coverage: float | None = None,
) -> Answer:
    if not hits:
        return Answer(
            text="没有检索到相关内容。请确认文档已入库，或换一种问法。",
            citations=[],
            mode="none",
        )

    # 先判「资料里到底有没有」，再决定要不要回答。
    # 顺序很重要：如果先让模型生成、再判断，它已经编完了。
    if min_coverage is None:
        answerable, diag = is_answerable(question, hits)
    else:
        answerable, diag = is_answerable(question, hits, coverage_threshold=min_coverage)

    if not answerable:
        return Answer(
            text="资料中没有找到与这个问题相关的内容。为避免编造，这里不给出回答。"
            "可以换一种问法，或者先把相关资料入库。",
            citations=[],
            mode="none",
            warning=(
                f"覆盖率 {diag['coverage_top1']:.0%}、最高分 {diag['top_score']:.1f}，"
                "均低于拒答阈值，已拒答"
            ),
        )

    # 灰区：判成「能答」，但证据其实偏薄。
    # 典型例子：「What is the capital of Brazil?」——capital 命中、brazil 没命中，
    # 覆盖率 50% 刚好过线，答案看起来就像答非所问。
    # 这种情况与其硬答，不如明确告诉用户「匹配度偏低，请核对原文」。
    borderline = diag.get("coverage_top1", 1.0) < 0.6 and diag.get("top_score", 99.0) < MIN_TOP_SCORE
    caveat = (
        f"匹配度偏低（覆盖率 {diag['coverage_top1']:.0%}、最高分 {diag['top_score']:.1f}），"
        "请核对引用原文"
        if borderline
        else None
    )

    if use_llm and os.getenv("LITRAG_API_KEY"):
        try:
            result = _llm(question, hits)
        except Exception as exc:  # LLM 失败时自动降级，不让整个流程挂掉
            result = _extractive(question, hits)
            result.warning = f"LLM 调用失败，已降级为抽取式回答（{exc}）"
    else:
        result = _extractive(question, hits)

    if caveat:
        result.warning = f"{result.warning}；{caveat}" if result.warning else caveat
    return result
