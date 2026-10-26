# NOTES —— 出题与记录

> **这个文件里放两样东西：事实卡、评测记录。**
> 题目不在这里，题目在 `data/eval_set_papers.jsonl`。

---

## 一、六步流程（每篇论文 20 分钟）

| 步 | 时间 | 做什么 |
|---|---|---|
| 1 | 2 min | 打开 HTML 版论文，`Ctrl+F` 搜 `Experiments` |
| 2 | 8 min | **填事实卡**（在本文第四节的对应卡片里） |
| 3 | 5 min | 用 8 个角度把事实卡变成题目 |
| 4 | 3 min | 写进 `data/eval_set_papers.jsonl` |
| 5 | 1 min | `python run.py eval --eval-set data/eval_set_papers.jsonl` |
| 6 | 1 min | **记一行到本文第五节** |

---

## 二、8 个出题角度

| # | 角度 | 从卡里哪一格来 |
|---|---|---|
| 1 | 解决什么问题 | ① |
| 2 | 核心方法叫什么 | ② |
| 3 | 和 baseline 的区别 | ② + ④ |
| 4 | 怎么验证的（数据集 + 指标） | ③ + ⑤ |
| 5 | 最强结论是什么 | ⑥ |
| 6 | 作者承认的局限 | ⑦ |
| 7 | **同义改写**（换说法问 ② 或 ⑥） | 自己改写 |
| 8 | **跨文档**（和另一篇论文比） | 另一张卡 |

---

## 三、质检三问（每题都要过）

1. **能指出答案在第几页吗？** 指不出来 → 删掉
2. **对错能一眼判定吗？** 不能 → 问太泛，限缩
3. **换个说法还问得通吗？** 换个词就废 → 关键词匹配题，重写

---

## 四、事实卡（每篇一张，填下面的空格）

### 卡 1 · 2310.11511（Self-RAG）—— 示例，已填好

```
论文编号：2310.11511
① 解决什么问题：LLM 只靠参数里的知识，容易产生事实错误；普通 RAG 无差别检索，质量不可控
② 方法叫什么：Self-RAG，用 reflection tokens（Retrieve / IsRel / IsSup / IsUse）让模型自己决定"要不要检索、检索到的有没有用、生成的是否被支持"
③ 数据集：PopQA（1,399 条长尾查询）、TriviaQA-unfiltered（11,313 条测试查询）
④ Baseline：Llama2 7B / 13B、Alpaca、ChatGPT，以及多种 retrieval-augmented 方法
⑤ 指标：短答案任务用 accuracy；长文本用 citation precision / recall
⑥ 最强结论：7B / 13B 的 Self-RAG 在多项任务上显著优于 SOTA LLM 与 RAG 方法（第 1 页摘要）
⑦ 作者承认的局限：（待补：Ctrl+F 搜 limitations）
```

> 上面的内容来自论文第 6～8 页，可以对照验证。

### 卡 2 · 2312.10997（RAG 综述）—— 综述版，已填

> 综述没有 Experiments 一节（全文搜 "Experiments" 命中 0 次），
> 所以卡要改成「综述版」：把 ③④ 换成"覆盖范围"，⑤ 换成"评价框架"。

```
论文编号：2312.10997
① 解决什么问题：LLM 有幻觉、知识过时、推理过程不可追溯三个问题，RAG 是解法（第 1 页）
② 提出的分类：RAG 三阶段——Naive RAG / Advanced RAG / Modular RAG（第 1、3～5 页）
③ 覆盖范围：综述了 100 多篇 RAG 研究（第 1 页摘要）
④ 不适用（综述不做自己的实验对比）
⑤ 评价框架：专门一节讲 evaluation framework 和 benchmark（第 12～15 页）
⑥ 核心结论：RAG 由检索、生成、增强三部分组成，并沿三阶段演进（第 1 页）
⑦ 挑战与方向：文末列出当前挑战和未来研究方向（第 14 页起）
```

### 卡 3 · 2307.03172（Lost in the Middle）—— 待填

```
论文编号：2307.03172
① 解决什么问题：长上下文模型是否真的能用好"放在中间"的信息（第 1 页）
② 方法/设计：不是新方法，是受控实验——改变相关文档在输入里的位置，观察性能变化（第 1、3～4 页）
③ 数据集/任务：multi-document QA（基于 NaturalQuestions-Open）+ key-value retrieval（第 1、3、6 页）
④ 对比对象（不是 baseline）：GPT-3.5-Turbo、GPT-4、Claude、MPT-30B-Instruct、Llama-2 7B/13B/70B 等现成模型（第 5、9 页）
⑤ 指标：accuracy（答对率）
⑥ 核心结论：U 形曲线——信息在开头或结尾时表现最好，在中间时显著下降（第 1 页摘要）
⑦ 作者承认的局限：（待补：Ctrl+F 搜 limitations；若没有，写"未单列局限"）
```

### 卡 4 · 2004.04906（DPR）—— 已填（标准研究论文）

```
论文编号：2004.04906
① 解决什么问题：开放域问答里，用稠密向量做检索能不能取代 BM25 这类稀疏检索（第 1 页）
② 方法叫什么：dual-encoder（双塔），用 in-batch negatives + BM25 硬负例训练（第 3 页 3.2 节）
③ 数据集：NQ（Natural Questions）、TriviaQA、WebQuestions（WQ）、CuratedTREC（TREC）；SQuAD 用于训练（第 4 页）
④ Baseline：BM25（检索）；端到端 QA 对比 ORQA、REALM（第 5 页表）
⑤ 指标：Top-20 / Top-100 检索准确率；端到端 QA 准确率
⑥ 最强结论：稠密检索能超过、并有望取代稀疏检索；开放域 QA 达到 SOTA（第 1、2 页）
⑦ 局限/消融结论：更复杂的模型结构或相似度函数不一定带来额外收益（结论节）
```

> 常见错误：别用浏览器自动翻译读论文。`Dense Passage Retriever` 会被译成
> “密集通道回收器”，写进卡里就露馅了。

### 卡 5 · 2002.08909（REALM）—— 待填

```
论文编号：2002.08909
① 解决什么问题：语言模型的知识固化在参数里，能否在预训练阶段就"检索 + 使用"外部知识（第 1～2 页）
② 方法叫什么：retriever（dual encoder + MIPS index）+ knowledge-augmented encoder，端到端联合训练，索引异步刷新（第 2 页）
③ 数据集：NaturalQuestions-Open（NQ）、WebQuestions（WQ）、CuratedTrec（CT）（第 6 页）
④ Baseline：ORQA（最直接对比）、BERT（闭卷）、T5（第 6～8 页）
⑤ 指标：exact match（EM）准确率（第 6 页）
⑥ 最强结论：Open-QA 上超过此前方法，三个基准同时达到当时最好（第 1 页）
⑦ 作者承认的局限：（待补：搜 limitation / future work）
```

> ⚠️ 读原文用 `python run.py page 2002.08909 6`，**不要开着浏览器翻译读论文**。
> 实测：`open-domain QA` 会被译成「开放质量保证」，`generation` 被译成「世代」。

---

## 五、评测记录（每次跑完，在下面加一行）

格式：

```
日期 | 题数 | Recall@1 | MRR | 最不该错的题 | 下一步
```

记录区：

```
2026-10-25 | 6  | 83.3% | 91.7% | How does Self-RAG decide when to retrieve?（被综述抢走） | 加 2310.11511 的 8 题
2026-10-25 | 14 | 85.7% | 92.9% | 两道跨文档题互相抢：问 Self-RAG 命中综述，问综述命中 Self-RAG | 填卡 2，出 8 题
2026-10-25 | 30 | 86.7% | 91.7% | 「What are the two tasks...」完全没召回：题里没有区分词 | 填卡 4，出 8 题
2026-10-25 | 38 | 81.6% | 89.0% | 7 道未排第一：4 道是综述↔原文互相抢，2 道是题目太泛 | 填卡 5，补满 40 题
2026-10-25 | 46 | 80.4% | 88.8% | 9 道未排第一：5 道文档歧义、2 道题目太泛、2 道跨文档 | 第 2 周结束，进入参数实验
2026-10-25 | 46 | 89.1% | 92.5% | chunk-size 网格：150/200/250/320/500/800 六组对照，200 的 R@1 最高 | 默认值改 200；下一组试 overlap
2026-10-25 | 46 | 89.1% | 92.5% | overlap 网格 0/60/120：84.8 / 89.1 / 87.0，60 最好；120 反而降（块间重复变多） | 默认保持 60；下一步试标题加权
```

> 加题之后指标**下降是正常的**（题变难了），记下来才不会误判成代码改坏了。

---

## 六、上线记录

```
2026-10-26 | 临时隧道 | https://creations-institutions-passion-river.trycloudflare.com
            | cloudflared 快速隧道，不需要信用卡；随进程结束失效，重启后网址会变
            | 长期方案：Hugging Face Spaces（免费无卡，需加 Dockerfile）
```

**自己重新开启的方法（两个窗口）：**

窗口 1（网站）：
```
cd /d D:\DS_work\litrag
python app.py
```

窗口 2（隧道）：
```
cd /d D:\DS_work\litrag
.\tools\cloudflared.exe tunnel --url http://localhost:8000
```

窗口 2 会打印新的 `https://xxx.trycloudflare.com`。
