# anatomy-rag backend（FastAPI）。Docker 未裝時僅寫不建；build 驗證見 Tasks 0.13/0.17。
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
# UV_NO_SYNC：防止 CMD 的 uv run 在啟動時重新 sync 而剪除 workspace 成員（root 不依賴成員）。
ENV PYTHONUNBUFFERED=1 UV_NO_SYNC=1
# 複製整個 workspace（uv sync 需所有成員 manifest）
COPY pyproject.toml uv.lock ./
COPY shared ./shared
COPY backend ./backend
COPY colpali_service ./colpali_service
COPY ingest ./ingest
COPY eval ./eval
# backend 依賴輕量 anatomy-shared（純 numpy、無 torch）→ 映像精簡
RUN uv sync --package anatomy-backend --no-dev
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD curl -f http://localhost:8000/healthz || exit 1
CMD ["uv", "run", "--no-sync", "uvicorn", "anatomy_backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
