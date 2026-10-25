# 部署到公网（Render 免费层）

目标：拿到一个 `https://xxx.onrender.com` 的链接，别人能打开、能提问。

预计 30 分钟，其中 20 分钟是等它构建。

---

## 前置条件

- [ ] GitHub 仓库已推送（`git push` 成功）
- [ ] 有一个 GitHub 账号（已有）
- [ ] 浏览器能打开 `render.com`

---

## 第 1 步：注册 Render（3 分钟）

打开 https://render.com → **Get Started** → 用 **GitHub 账号登录**（最快，省得验证邮箱）。

登录后授权它读取你的仓库。

---

## 第 2 步：创建服务（3 分钟）

**方式 A（推荐）：用蓝图**

1. 控制台点 **New** → **Blueprint**
2. 选仓库 `litrag`
3. 它会自动读到根目录的 `render.yaml`
4. 点 **Apply**

**方式 B：手动填**

1. **New** → **Web Service**
2. 选仓库 `litrag`
3. 填：

| 字段 | 值 |
|---|---|
| Name | `litrag` |
| Region | Singapore |
| Runtime | Python |
| Build Command | `pip install -r requirements.txt && python scripts/fetch_papers.py || true; python run.py ingest data` |
| Start Command | `python app.py` |
| Instance Type | Free |

4. 点 **Create Web Service**

---

## 第 3 步：等构建（10～20 分钟）

日志里会依次出现：

```
Collecting pymupdf ...
已入库 8 篇文档，切成 2208 块
索引已保存到 /opt/render/project/src/.index/index.json
已启动：http://0.0.0.0:10000
```

看到最后一行，说明起来了。

> 免费层第一次构建比较慢，正常。

---

## 第 4 步：验证（5 分钟）

1. 打开给你的链接，应该看到输入框
2. 输入：`What are the three stages of RAG?`
3. 应该出现答案 + `[1] 2312.10997 / 第 N 页`

**用手机流量再打开一次**，确认不是只有你能访问。

---

## 第 5 步：记下来

把链接写进 `NOTES.md`：

```
上线时间：____
链接：https://____.onrender.com
构建耗时：____
```

---

## 常见错误

| 现象 | 原因 | 解决 |
|---|---|---|
| `Application failed to respond` | 服务没监听 `0.0.0.0` | 确认 `app.py` 里 `host = "0.0.0.0" if os.environ.get("PORT")` |
| `ModuleNotFoundError: litrag` | 工作目录不对 | 仓库根目录必须是 `litrag/`（含 `run.py`） |
| 构建成功但打开 502 | 索引太大、启动超时 | 减少论文数量，或把 `ingest` 挪到构建阶段（`render.yaml` 已这样做） |
| `No such file: data/papers/xxx.pdf` | PDF 没提交（被 .gitignore 排除） | 正常，构建时会用 `fetch_papers.py` 现下 |
| 15 分钟不访问后第一次很慢 | 免费层休眠 | 正常，等 30 秒 |

---

## 免费层的三个限制（要提前知道）

1. **15 分钟无访问会休眠**，下次访问要等 30 秒左右唤醒
2. **每月 750 小时**，够一个服务常开
3. **512MB 内存**，当前索引（2208 块）占用很小，没问题

---

## 部署之后

- 把链接发给自己手机，试用一周
- 每次改代码：`git push` → Render 自动重新构建
- 要改启动命令：Render 控制台 → Settings

**部署不是终点，是第一次让别人能用。**
