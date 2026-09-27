# syntax=docker/dockerfile:1

# Dashboard chỉ là file tĩnh: build trên máy build rồi chép sang image server.
FROM --platform=$BUILDPLATFORM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY server/requirements.txt ./
RUN pip install -r requirements.txt
COPY server/alembic.ini ./
COPY server/alembic ./alembic
COPY server/app ./app
COPY --from=web /web/dist ./web
RUN useradd --uid 10001 --home-dir /app --shell /usr/sbin/nologin commentscope
USER commentscope
ENV ENVIRONMENT=production \
    WEB_DIST_DIR=/app/web
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]
# Đúng 1 process: bộ lập lịch kiểm tra proxy / đổi IP 4G chạy ngay trong process này, chạy 2 bản sẽ đổi IP 2 lần.
# Cổng 8000 chỉ mở trong mạng Docker cho Caddy, nên tin header X-Forwarded-For để giới hạn đăng nhập sai theo IP thật.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--forwarded-allow-ips", "*"]
