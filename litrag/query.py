"""中文查询到英文检索词的轻量扩展。

BM25 是字面匹配，中文问题查英文论文时几乎没有共同 token。这里不做重量级
翻译，而是维护一张面向科研阅读的高频术语表，把中文问题的检索意图补进
查询中。英文问题保持原样，因此不会改变现有英文评测结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CJK = re.compile(r"[\u4e00-\u9fff]")


@dataclass(frozen=True)
class QueryPlan:
    original: str
    expanded: str
    translations: tuple[str, ...] = ()
    english_terms: tuple[str, ...] = ()

    @property
    def translated(self) -> bool:
        return bool(self.translations)


# 长词放在短词前面，避免「阶段」先于「三个阶段」命中。
_TERM_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("检索增强生成", ("retrieval", "augmented", "generation", "rag")),
    ("长上下文", ("long", "context")),
    ("三个阶段", ("three", "stages")),
    ("三个", ("three",)),
    ("两个", ("two",)),
    ("模块化", ("modular",)),
    ("双编码器", ("dual", "encoder")),
    ("双塔", ("dual", "encoder")),
    ("反思标记", ("reflection", "tokens")),
    ("批判标记", ("critique", "token")),
    ("数据集", ("dataset", "datasets")),
    ("上下文", ("context",)),
    ("阶段", ("stage", "stages")),
    ("组件", ("component", "components")),
    ("高级", ("advanced",)),
    ("朴素", ("naive",)),
    ("什么时候", ("when",)),
    ("决定", ("decide", "decides")),
    ("中间", ("middle",)),
    ("位置", ("position",)),
    ("性能", ("performance",)),
    ("下降", ("decrease", "worse")),
    ("任务", ("task", "tasks")),
    ("稠密", ("dense",)),
    ("密集", ("dense",)),
    ("稀疏", ("sparse",)),
    ("负例", ("negative", "negatives")),
    ("训练", ("training", "train")),
    ("检索器", ("retriever",)),
    ("检索", ("retrieval", "retrieve")),
    ("生成", ("generation", "generate")),
    ("评估", ("evaluation", "evaluate")),
    ("评测", ("evaluation", "benchmark")),
    ("指标", ("metric", "metrics")),
    ("方法", ("method", "approach")),
    ("模型", ("model",)),
    ("架构", ("architecture",)),
    ("知识", ("knowledge",)),
    ("索引", ("index",)),
    ("刷新", ("refresh",)),
    ("反思", ("reflection",)),
    ("标记", ("token", "tokens")),
    ("支持", ("support",)),
    ("回答", ("answer",)),
    ("查询", ("query",)),
    ("证据", ("evidence",)),
    ("局限", ("limitation", "limitations")),
    ("挑战", ("challenge", "challenges")),
    ("未来", ("future",)),
    ("方向", ("direction", "directions")),
    ("比较", ("compare", "comparison")),
    ("区别", ("difference",)),
    ("差异", ("difference",)),
    ("贡献", ("contribution",)),
    ("实验", ("experiment",)),
    ("结果", ("result",)),
    ("结论", ("conclusion",)),
    ("作者", ("authors",)),
    ("问题", ("problem",)),
    ("解决", ("solve",)),
    ("作用", ("role",)),
    ("为什么", ("why",)),
    ("如何", ("how",)),
    ("什么", ("what",)),
    ("是否", ("whether",)),
    ("哪些", ("which",)),
    ("多少", ("how", "many")),
    ("主要", ("main",)),
)


def build_query_plan(question: str) -> QueryPlan:
    """把中文问题扩展为「原问题 + 英文检索词」。"""
    question = (question or "").strip()
    if not question or not _CJK.search(question):
        return QueryPlan(original=question, expanded=question)

    lowered = question.lower()
    extras: list[str] = []
    matched: list[str] = []
    for chinese, english in _TERM_MAP:
        if chinese not in lowered:
            continue
        matched.append(chinese)
        for term in english:
            if term not in extras:
                extras.append(term)

    expanded = question
    if extras:
        expanded = f"{question} {' '.join(extras)}"
    return QueryPlan(
        original=question,
        expanded=expanded,
        translations=tuple(matched),
        english_terms=tuple(extras),
    )