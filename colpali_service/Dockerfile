# syntax=docker/dockerfile:1
# 真實 GPU encoder：經 docker-compose.gpu.yml 啟用；需 nvidia-container-toolkit。
# TODO(verify): 確認此 CUDA 12.8 runtime tag 存在並 pin digest（落地時）。
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04
# UV_HTTP_TIMEOUT=600：cu128 的 GB 級 wheel（torch / nvidia-cudnn-cu12 等）在預設 30s 逾時下易斷，拉長至 10 分。
ENV PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive ENCODER_MOCK=false COLPALI_DEVICE=cuda \
    UV_NO_SYNC=1 UV_HTTP_TIMEOUT=600
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.11 python3.11-venv python3-pip git curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY shared ./shared
COPY backend ./backend
COPY colpali_service ./colpali_service
COPY ingest ./ingest
COPY eval ./eval
# 裝 colpali_service + gpu extra（torch/transformers），再把 torch 換成 cu128 wheel。
# --reinstall 覆蓋預設 CPU wheel；UV_NO_SYNC + CMD --no-sync 防 uv run 再同步還原 torch。
# cache mount：把已下載的 GB 級 wheel 快取於 BuildKit，重試/迭代時免重抓數 GB（只補缺的）。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package colpali-service --extra gpu --no-dev && \
    uv pip install torch --index-url https://download.pytorch.org/whl/cu128 --reinstall
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=5s --retries=30 CMD curl -f http://localhost:8001/healthz || exit 1
CMD ["uv", "run", "--no-sync", "uvicorn", "colpali_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
