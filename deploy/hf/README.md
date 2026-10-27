---
title: litrag
emoji: 📚
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: 面向学术文献的 RAG 助手，答案带页码引用
---

# litrag

面向学术文献的 RAG 助手：**检索 → 拒答 → 引用溯源 → 可评测**。

每个结论都能点回原文的页码；资料里没有就说没有，不编。

- 零第三方依赖（纯标准库 + 一个 PDF 解析库）
- 自带 46 题开发集（Recall@1 84.8%）和 20 题冻结 hold-out（Recall@1 95.0%）
- 内置零依赖 PDF 文本提取器，离线环境也能用

完整说明、架构和实验记录见仓库里的 `PROJECT.md`。
