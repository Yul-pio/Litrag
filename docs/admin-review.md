# 招生审核后台

内部审核页地址：

```text
/admin
```

它负责把 GitHub Actions 产生的候选 JSON 变成可操作的审核队列，并把人工确认后的
方向写入正式数据。页面不在公开导航中展示。

## 本地启动

PowerShell：

```powershell
$env:LITRAG_ADMIN_TOKEN = "请换成至少12位的随机字符串"
python app.py
```

打开：

```text
http://127.0.0.1:8000/admin
```

输入同一个 `LITRAG_ADMIN_TOKEN` 进入审核台。令牌只保存在浏览器 `sessionStorage`，
关闭标签页后失效。

## 线上配置

PythonAnywhere 等 WSGI 环境需要在 Web App 的环境变量中设置：

```text
LITRAG_ADMIN_TOKEN=至少12位的随机字符串
```

修改后重启 Web App。不要把这个值写进 Git 仓库，也不要通过 URL 参数传递。

## 审核流程

1. 从 GitHub Actions 下载候选 artifact。
2. 在审核台上传其中的 `*_catalog.json`。
3. 查看候选与正式库的字段差异。
4. 核对专业、方向、招生计划、推免数、考试科目和官方来源。
5. 点击“核验通过并发布”，或填写原因后拒绝。

系统会自动处理：

- 同一官方来源已有招生文档时，把新方向追加到 `program_ids`；
- 已存在方向必须额外勾选“覆盖已有方向”；
- 写入正式数据前再次执行审核单校验；
- 通过和拒绝都写入 `data/candidates/review_audit.jsonl`；
- 已处理方向从待审核队列移除。

## 数据目录

```text
data/candidates/pending/         待审核候选
data/candidates/processed/       预留的已处理目录
data/candidates/rejected/        预留的已拒绝目录
data/candidates/review_audit.jsonl  审核审计日志
```

这些运行时文件不会提交到 Git。

## 安全边界

- 所有管理 API 都要求 `X-Admin-Token` 请求头；
- 未配置至少 12 位令牌时，管理 API 返回 503；
- 管理页面禁止被 iframe 嵌入，并设置 `no-store`；
- CSP 限制脚本和连接只访问本站；
- 上线必须使用 HTTPS。
