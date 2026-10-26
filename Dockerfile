FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WHUT_HOST=0.0.0.0 \
    WHUT_PORT=8787 \
    WHUT_DATA_DIR=/app/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# web/dist 已随仓库提交（不想装 Node 也能跑）；改了前端就 npm run build
COPY web/dist ./web/dist
# 镜像内自带一份默认配置，可用 volume 挂载覆盖
COPY config.example.json ./config.json

RUN useradd --uid 10001 --create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data
USER app

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=5).status == 200 else 1)"

CMD ["python", "-m", "app"]
