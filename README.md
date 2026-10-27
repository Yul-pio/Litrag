# litrag

> 面向学术文献的极简 RAG 助手：**检索 → 拒答 → 引用 → 评测**。
> 每个结论都能点回原文的页码。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](requirements.txt)
[![Live Demo](https://img.shields.io/badge/demo-online-success)](https://yulpio.pythonanywhere.com)

**在线演示**：<https://yulpio.pythonanywhere.com>（问一句 "What are the three stages of RAG?" 试试）

---

## 为什么做这个

大模型回答学术问题有两个硬伤：**不知道你的私有文献**，以及**会编造引用**。

通用聊天产品解决不了第二点，因为学术场景对事实错误零容忍——一个编造的参考文献足以让整段结论作废。

litrag 的做法很笨但很稳：**先检索到原文，只根据原文回答，每句话标出页码**。没有证据就拒答，不猜。

## 特性

- **零第三方依赖**：纯 Python 标准库实现，`git clone` 后直接能跑，不需要 `pip install`
- **引用可追溯**：每个结论带 `[n]` 编号，网页端可直接打开 PDF 对应页
- **会拒答**：问题与资料无关时明确说“没有”，而不是硬编
- **可评测**：简单集、难例集、46 题论文集、20 题 hold-out 和 20 题不可回答集
- **会话可清理**：上传的 PDF 和索引可在网页端一键删除
- **离线可用**：内置自写的 PDF 文本提取器，装不了第三方库的环境也能解析 PDF
- **LLM 可选**：不配 API Key 也能完整使用（抽取式回答），配了则更自然，并启用引用编号校验

---

## 快速开始

```bash
git clone <your-repo-url>
cd litrag

# 1. 建立索引（示例语料自带，零依赖）
python run.py ingest data/docs

# 2. 提问
python run.py ask "为什么要分块？"

# 3. 评测
python run.py eval
python run.py eval --eval-set data/eval_set_hard.jsonl
python run.py eval --eval-set data/eval_set_holdout.jsonl
python run.py eval-refusal
python scripts/sweep_threshold.py       # 拒答阈值权衡

# 4. 网页版
python app.py            # http://127.0.0.1:8000

# 5. 测试
python -m unittest discover -s tests -v
```

### 处理你自己的 PDF

```bash
# 下载论文（需要联网）
python scripts/fetch_papers.py

# 或者手动放进 data/papers/ ，文件名保持 arXiv 编号，如 2312.10997.pdf
python run.py check data/papers/2312.10997.pdf   # 先体检：能不能读出文字
python run.py ingest data                         # 递归读取 docs/ 和 papers/
python run.py ask "What are the three main stages of RAG?"
```

> `check` 会告诉你能读出多少字符、内容长什么样。扫描件（整页是图片）读不出来，会提前提示，省得入库后对着乱码排查。

### 接入大模型（可选）

```bash
export LITRAG_API_KEY="sk-xxx"                    # PowerShell: $env:LITRAG_API_KEY="sk-xxx"
export LITRAG_BASE_URL="https://api.deepseek.com" # 任何 OpenAI 兼容接口
export LITRAG_MODEL="deepseek-chat"
```

模型输出必须带 `[n]` 引用，生成后会**逐一核对编号是否真实存在**——模型编造的编号会被抓出来并告警。

---

## 架构

```
文档 ──loader──> Document(页, 文本)
                     │
                 chunker       按标题分节 → 段落合并 → 超长按句切分
                     │
                 bm25          单字+双字切分，标题参与索引
                     │
问题 ──tokenize──> 检索 Top-K
                     │
              实词覆盖率判断 ──不足──> 拒答
                     │
              抽取式 / LLM 生成
                     │
              引用校验（编号是否存在）
                     │
              回答 + 可追溯引用
```

索引是一次性离线任务，检索和生成是在线请求——两者的优化目标不同（前者吞吐，后者延迟），所以本项目的索引可以序列化到磁盘复用。

---

## 技术亮点：零依赖 PDF 文本提取器

`litrag/pdf_text.py` 是一个**不依赖任何第三方库**的 PDF 文字提取器，用于「机器无法安装解析库」的场景（离网环境、内网服务器）。

它要解决的不是"读出 PDF"，而是"读出来的字能用于检索"。处理一篇 21 页的 arXiv 综述时，踩了三个真实的坑：

| 问题 | 现象 | 根因 | 修复 |
|---|---|---|---|
| **词间空格丢失** | `Retrieval-AugmentedGenerationforLarge` | PDF 用 TJ 数组里的 `-350`（千分之一字宽的位移）表示空格，而不是空格字符；原实现把所有空格统一堆到了行尾 | 按顺序逐项处理 TJ：字符串输出、负数判断是否补空格 |
| **句子被硬切** | 回答是 `tualizes the broader scope of…` 半截句子 | PDF 每个视觉行后面都有换行符，直接切句会得到碎片 | 启发式合并断行：上一行无句末标点且下行小写 → 接上；连字符断词则去连字符拼接 |
| **上标被误判成换行** | 作者名的 `a/b/c` 各自独占一行 | 用固定像素阈值判断是否换行，没考虑字号 | 改用「当前字号」做相对阈值（`|dy| > 0.5 × fontSize`） |

修复前后（同一篇论文）：

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 提取字符数 | 89,931（且不可用） | 100,972 |
| 英文词间空格 | 全部丢失 | 正常 |
| 句子完整性 | 半截句 | 完整句，可切块检索 |
| 检索效果 | 召回正确页但答案不可读 | 答案完整且引用页码正确 |

完整实现说明见 **[docs/pdf-extraction.md](docs/pdf-extraction.md)**。

### 它是怎么工作的（30 秒版）

1. 扫描 PDF 对象（`N 0 obj … endobj`），手工解析以避免二进制流里的假对象
2. 用 `zlib` 解压 `FlateDecode` 流
3. 定位每页的 `/Contents` 内容流和 `/Resources /Font`
4. 解析文本指令（`Tj` / `TJ` / `'` / `"`），按 `Td` / `Tm` 还原换行
5. 通过字体 `/ToUnicode` CMap 把字节映射成 Unicode；缺失时降级为 latin-1

**边界**：扫描件（无文字层）、使用 CID 字体且缺少 ToUnicode 的 PDF 提取会失败或乱码。`loader.py` 中保留了四级降级链：

```
PyMuPDF  →  pdfplumber  →  pypdf  →  内置 pdf_text
（装了就用好的，一个都没装才用内置实现）
```

---

## 评测

当前语料：8 篇文档（5 篇论文 + 3 篇示例）/ 2266 个块，平均块长 207.5 字符。

### 论文开发集（46 题，文档级判定）

题目覆盖 5 篇论文的事实题、同义改写题和跨文档题，标注 `expected_doc`。

| 题数 | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| 46 | 84.8% | 97.8% | 97.8% | 90.9% |

### 冻结后的 hold-out 集（20 题）

这 20 题在检索方案冻结后新建，只用于最终报告，不参与块大小或 overlap 调参。

| 题数 | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|
| 20 | 95.0% | 95.0% | 100.0% | 96.0% |

### 示例语料评测集（章节级判定）

| 评测集 | 题数 | Recall@1 | Recall@3 | MRR |
|---|---|---|---|---|
| 简单集（复用文档词汇） | 12 | 91.7% | 91.7% | 93.8% |
| 难例集（同义改写） | 8 | 100% | 100% | 100% |

### 拒答评测（20 题，资料中没有答案）

`data/eval_set_unanswerable.jsonl` 包含天气、体育、地理、生活常识等不在语料中的问题。

| 阈值 | 正确拒答 | 误答 | 说明 |
|---|---|---|---|
| 20% | 45.0% | 55.0% | 当前默认值，兼容短中文文档 |
| 40% | 60.0% | 40.0% | 误答下降，但难例集通过率也下降 |
| 60% | 75.0% | 25.0% | 论文集更稳，短文档误拒明显增加 |
| 70% | 100.0% | 0.0% | 过度拒答，不适合作为默认值 |

复现：

```bash
python run.py eval-refusal
python scripts/sweep_threshold.py
```

**这是一个刻意保留的失败结果。** 单一覆盖率阈值无法同时适配短中文文档和英文论文；下一步应训练轻量 answerability 分类器，或使用 LLM judge 判断“资料是否足以回答”，而不是继续手调一个全局阈值。

### 参数实验：块大小与重叠

在 46 题开发集上，只改 `--chunk-size`：

| 块大小 | 块数 | Recall@1 | Recall@3 | MRR |
|---|---|---|---|---|
| 150 | 2744 | 78.3% | 93.5% | 85.9% |
| **200** | **2266** | **84.8%** | 97.8% | **90.9%** |
| 250 | 1905 | 82.6% | 95.7% | 89.3% |
| 320 | 1494 | 80.4% | 97.8% | 88.8% |
| 500 | 920 | 82.6% | 95.7% | 89.3% |
| 800 | 590 | 82.6% | 95.7% | 89.2% |

固定 `size=200` 时，overlap 的影响：

| overlap | 块数 | Recall@1 | Recall@3 | MRR |
|---|---|---|---|---|
| 0 | 2203 | 84.8% | 91.3% | 89.2% |
| 30 | 2232 | 80.4% | 95.7% | 88.2% |
| **60** | **2266** | **84.8%** | **97.8%** | **90.9%** |
| 90 | 2298 | 80.4% | 97.8% | 88.8% |
| 120 | 2342 | 84.8% | 95.7% | 90.8% |

结论与诚实的边界：

- 200 字符在 Recall@1 和 MRR 上最好，所以保留默认值。
- overlap=60 在 Recall@3 和 MRR 上最好；但 46 道题里一道题就是 2.2 个百分点，差距小于两道题时不能断言显著。
- 旧版本的 overlap 只保留最后一个段落，段落超过窗口时实际上没有重叠。现在改为真实字符窗口，所以旧指标不能和新指标直接比较。

---

## 这是 Agent 吗？

**不是，是确定性 RAG 应用。**

| | 本项目 | Agent |
|---|---|---|
| 执行路径 | 固定：检索 → 拒答判断 → 生成 → 引用校验 | 由模型自主规划 |
| 工具调用 | 只有检索 | 多工具、模型自选 |
| 循环 | 一次性 | 多轮「思考-行动-观察」 |
| 失败恢复 | 降级到抽取式 | 自我反思重试 |

刻意不做 Agent：学术场景要求可复现和可评测，自主规划会引入不可控的失败路径。若要验证 Agent 的收益，正确做法是把它作为对照组，用同一套评测集比较收益与延迟，而不是默认开启。

---

## 关键设计决策

1. **中文用「单字 + 双字」切分**，不引入分词器：单字保召回，双字保区分度，无需词典也不怕新词；代价是索引膨胀。
2. **标题存元数据，但建索引时拼回文本**：否则「为什么要分块」这类提问会漏召回。
3. **先判断覆盖率，再生成**：当前用 20% 实词覆盖率做基线门槛，并在不可回答集上报告误答率。顺序很重要——先生成再判断，模型已经编完了。
4. **引用校验而非"让模型自觉"**：编号必须能在召回结果里找到对应项，否则告警。

---

## 已知限制

- **中文提问查英文文献效果差**：BM25 是字面匹配，跨语言需要查询翻译或向量检索（v2 计划）
- **PDF 章节识别弱**：没有解析 PDF 的目录结构，`section` 退化为「第 N 页」
- **扫描件读不出来**：没有 OCR
- **单一拒答阈值仍然不够好**：20% 在 20 题不可回答集上仍有 55% 误答；阈值扫描只能缓解，不能根治，下一步需要 answerability 分类器或 LLM judge
- **引用支持度尚未自动评测**：目前只校验编号存在，下一步应检查引用片段是否真的支持对应句子
- **无连字符断词会产生多余空格**：PDF 把单词拆到两行时，合并后可能变成 `em ploy`；有连字符的已处理，无连字符的暂时无法可靠判断
- **跨文档混淆**：综述里大段讨论某篇原始论文时，检索可能把综述排在原始论文前面（`2312.10997` 常抢走 `2310.11511` 的提问）。v2 可引入来源权重或引用图解决
- **没有用户系统**：无法多用户隔离，离产品化还有距离

论文目录的使用说明见 [docs/add-papers.md](docs/add-papers.md)。

---

## 目录结构

```
litrag/
├── run.py                  # 命令行入口：ingest / ask / eval / check / stats
├── app.py                  # 极简 Web 界面（标准库 http.server）
├── litrag/
│   ├── loader.py           # 四级降级的文档加载
│   ├── pdf_text.py         # 零依赖 PDF 文本提取器
│   ├── chunker.py          # 分节 + 合并 + 超长切分 + 重叠
│   ├── bm25.py             # BM25 + 中文单字/双字切分
│   ├── retriever.py        # 索引、检索、序列化
│   ├── answerer.py         # 抽取式 / LLM / 引用校验 / 拒答
│   └── evaluate.py         # Recall@k、MRR、错题清单
├── data/
│   ├── docs/               # 示例语料
│   ├── papers/             # 你的论文（PDF 不入库，见 .gitignore）
│   ├── eval_set.jsonl      # 简单评测集
│   ├── eval_set_hard.jsonl # 难例评测集
│   ├── eval_set_papers.jsonl # 46 题论文开发集
│   ├── eval_set_holdout.jsonl # 20 题冻结测试集
│   └── eval_set_unanswerable.jsonl # 20 题拒答测试集
├── demo/                   # 演示动画（HTML + GIF）
├── docs/pdf-extraction.md  # PDF 提取器实现说明
├── scripts/fetch_papers.py # 批量下载 arXiv 论文
└── tests/test_smoke.py     # 冒烟测试
```

---

## 路线图

| 版本 | 目标 |
|---|---|
| v1（当前） | BM25 + 抽取式 + 拒答 + 引用 + 评测，跑通闭环 |
| v1.5 | 真实语料扩到 50 篇，评测集扩到 50 题 |
| v2 | 向量检索 + BM25 混合排序，解决同义改写与跨语言问题 |
| v2.5 | rerank 小模型，提升 Recall@1 |
| v3 | 综述生成、笔记导出、多文档对比 |

不做：多模态（图表理解）、Agent 自主规划、自建向量数据库——对当前阶段性价比太低。

---

## License

[MIT](LICENSE)
