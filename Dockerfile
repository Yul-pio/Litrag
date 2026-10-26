# Hugging Face Spaces 用的镜像
# 说明：HF 的 Docker Space 默认要求服务监听 7860 端口。
# 构建时就把论文下好、索引建好，避免每次启动都重来（启动更快）。

FROM python:3.11-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 构建期下载论文并建索引；下载失败也不阻断（只用示例语料）
RUN python scripts/fetch_papers.py || true
RUN python run.py ingest data

ENV PORT=7860
EXPOSE 7860

CMD ["python", "app.py"]
