"""BM25 检索：不依赖任何第三方库。

为什么 v1 用 BM25 而不是向量检索？
  1. 零成本、可离线，适合还没有收入的学生阶段
  2. 完全可解释，调参有依据，便于写进技术报告
  3. 它把「检索 → 引用 → 评测」的闭环先跑通，向量检索是 v2 的增量项

复试时这也是一个好故事：先用最朴素的方法建立评测基线，再证明升级带来的收益。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-z0-9_]+")

# 只用于“覆盖率/拒答”判断，不参与 BM25 检索。
# 问题词和虚词如果参与覆盖率，会让“什么是……”这类无关问题虚高。
_EN_COVERAGE_STOPWORDS = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with",
    "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "what", "why", "how", "which", "who", "when", "where", "whether",
    "can", "could", "should", "would", "about", "from", "by", "as", "at",
    "it", "its", "this", "that", "these", "those", "there", "their",
    "paper", "study", "article", "according", "mention", "explain",
    "please", "tell", "give",
})
_CJK_COVERAGE_STOPWORDS = frozenset({
    "什么", "为什", "怎么", "为什么", "如何", "是否", "多少", "哪些", "哪个",
    "请问", "一下", "这篇", "本文", "文章", "论文", "研究", "作者",
    "可以", "一般", "主要", "需要", "采用", "使用", "对于", "关于",
    "其中", "以及", "进行", "这个", "那个", "时候", "问题", "内容",
})


def tokenize(text: str) -> list[str]:
    """把文本切成检索用的 token。

    中文没有空格，这里用「单字 + 相邻双字」：
      - 单字保证召回（查「分块」时，「块」也能命中）
      - 双字保证区分度（「分块」比单独的「分」「块」更精确）
    英文和数字按词切。这个策略不需要 jieba，也不怕新词。
    """
    text = text.lower()
    tokens: list[str] = _LATIN.findall(text)
    for run in _CJK_RUN.findall(text):
        tokens.extend(run)  # 单字
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))  # 双字
    return tokens


def content_terms(text: str) -> list[str]:
    """提取「实词」，用来判断问题到底有没有被资料覆盖。

    和 tokenize 的区别：这里中文只保留双字、英文只保留长度 >= 2 的词。
    因为单字几乎在任何中文里都能撞上（的、是、在），拿它算覆盖率会虚高，
    导致「今天天气怎么样」这种无关问题也被判成相关。
    """
    text = text.lower()
    terms = [
        w for w in _LATIN.findall(text)
        if len(w) >= 2 and w not in _EN_COVERAGE_STOPWORDS
    ]
    for run in _CJK_RUN.findall(text):
        terms.extend(
            run[i : i + 2]
            for i in range(len(run) - 1)
            if run[i : i + 2] not in _CJK_COVERAGE_STOPWORDS
        )
    if not terms:  # 极短的查询（比如单个汉字）退化为通用切分
        terms = tokenize(text)
    return terms


class BM25Index:
    """Okapi BM25。

    score(q, d) = Σ idf(qi) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * |d| / avgdl))
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.n_docs = 0
        self.avgdl = 0.0
        self.doc_len: list[int] = []
        self.postings: dict[str, dict[int, int]] = defaultdict(dict)
        self.idf: dict[str, float] = {}

    def fit(self, docs_tokens: list[list[str]]) -> "BM25Index":
        self.n_docs = len(docs_tokens)
        self.doc_len = [len(tokens) for tokens in docs_tokens]
        self.avgdl = sum(self.doc_len) / self.n_docs if self.n_docs else 0.0

        self.postings = defaultdict(dict)
        for doc_idx, tokens in enumerate(docs_tokens):
            for term, tf in Counter(tokens).items():
                self.postings[term][doc_idx] = tf

        # 加 1 的对数形式，避免高频词 idf 变成负数
        for term, posting in self.postings.items():
            df = len(posting)
            self.idf[term] = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
        return self

    def score(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.n_docs
        if not self.n_docs:
            return scores

        for term in set(query_tokens):
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = self.idf[term]
            for doc_idx, tf in posting.items():
                dl = self.doc_len[doc_idx]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
                scores[doc_idx] += idf * tf * (self.k1 + 1) / denom
        return scores
