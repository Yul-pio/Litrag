# data/papers/

把你的论文 PDF 放在这里，文件名用 **arXiv 编号**（如 `2312.10997.pdf`）。

为什么用编号：

- 文件名就是 `doc_id`，评测集用它标注标准答案
- 引用来源会显示成 `2312.10997 / 第 2 页`，比 `论文最终版(3).pdf` 清楚得多

获取方式：

```bash
python scripts/fetch_papers.py
```

或者手动打开 `https://arxiv.org/abs/<编号>` → 点「查看PDF」→ 保存时把
`2312.10997v5.pdf` 改成 `2312.10997.pdf`。

入库前先体检：

```bash
python run.py check data/papers/2312.10997.pdf
```

**PDF 不提交到 git**（见 `.gitignore`）：体积大，而且版权归作者，不适合再分发。
