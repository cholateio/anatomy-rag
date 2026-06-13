.PHONY: help up up-gpu up-obs down logs migrate gpu-smoke golden-bytes ingest-sample ingest-gate bench-stageb bench-stageb-gate test lint fmt encoder-models encoder-gate

help:
	@echo "make up / up-gpu / up-obs / down / logs"
	@echo "make migrate      # 在 backend container 內跑 Alembic（連 PG_DIRECT_URL :5432，§0.3 例外）"
	@echo "make gpu-smoke    # 實機 GPU：build GPU encoder 並驗 torch.cuda.is_available()"
	@echo "make golden-bytes # 產生 Vercel UI Message Stream golden wire bytes"
	@echo "make bench-stageb  # Stage B MaxSim 延遲探針（手動；需 compose 起 DB + 已 migrate；DL-013，非 CI gate）"
	@echo "make bench-stageb-gate  # Stage B 並發/p95 gate（手動；SQL vs numpy，建議 production mode；附錄 D.5）"
	@echo "make test / lint / fmt / ingest-sample"
	@echo "make encoder-models # 預拉 HF 模型進 hfcache volume（首次 ~7-8GB；GPU 路徑前置步驟）"
	@echo "make encoder-gate   # Phase 3 encoder smoke gate（手動 GPU；需先 encoder-models）"

up:
	docker compose up --build -d

up-gpu:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d

up-obs:
	docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

# 在 backend container 內跑 alembic；backend 服務的 env_file(.env) 已注入
# PG_DIRECT_URL=...@postgres:5432（compose 網路內可解析 hostname postgres）。
migrate:
	docker compose run --rm backend sh -c "cd backend && uv run alembic -c alembic.ini upgrade head"

# 實機 GPU smoke gate（非 CI；production 驗收前必過）。需 Docker + nvidia-container-toolkit。
gpu-smoke:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml build encoder
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm encoder \
	  uv run --no-sync python -c "import torch; assert torch.cuda.is_available(), 'CUDA 不可用：檢查 cu128/driver'; print('CUDA OK:', torch.cuda.get_device_name(0))"

golden-bytes:
	cd frontend && node scripts/dump-golden-stream.mjs

# mock smoke：synthetic 來源 + mock runtime，寫入真 DB/MinIO（需 make up + make migrate）。
# 連 localhost:6432/9000（compose 對外埠）；不需 GPU/poppler。
ingest-sample:
	DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag \
	S3_ENDPOINT=http://localhost:9000 S3_BUCKET=anatomy-rag-pages \
	S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin \
	uv run --no-sync python -m anatomy_ingest.cli \
	  --synthetic 6 --book-meta ingest/scripts/sample_book.yaml --kb-version 1 --batch-size 2

# GPU gate：真 1 頁 PDF + real ColPali + 真 MinIO/PG（手動，非 CI；需 poppler + GPU venv）。
ingest-gate:
	uv run --no-sync python ingest/scripts/ingest_gate.py

# Stage B MaxSim 延遲探針（手動；需 compose 起 DB + 已 migrate；DL-013，非 CI gate）
bench-stageb:
	uv sync --package anatomy-backend --inexact
	uv run --no-sync python backend/scripts/bench_stage_b.py

# Stage B 並發/p95 benchmark gate（手動，硬性驗收；非 CI）。需 compose 起 DB + 已 migrate。
bench-stageb-gate:
	uv run --no-sync python backend/scripts/bench_stage_b_concurrency.py

# 本機測試：先同步全部 workspace 成員（避免 uv run 預設剪除成員），再以 --no-sync 跑。
# 註：首次會安裝 ingest/encoder 的完整依賴（含 torch），之後快取於 .venv。
test:
	uv sync --all-packages --group dev
	uv run --no-sync pytest

lint:
	uv sync --group dev --inexact
	uv run --no-sync ruff check .

fmt:
	uv sync --group dev --inexact
	uv run --no-sync ruff format .

# 預拉 HF 模型進 hfcache volume（首次 ~7–8GB；之後重建/重啟免重抓）。需先 build GPU image。
encoder-models:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml build encoder
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps encoder \
	  uv run --no-sync python -c "from huggingface_hub import snapshot_download as d; d('vidore/colpali-v1.3-hf'); d('Helsinki-NLP/opus-mt-zh-en'); print('models cached')"

# Phase 3 encoder smoke gate（D-P 種子；手動 GPU，非 CI）：渲染偽頁面→真模型編碼→zh/en 雙軌 recall@3
encoder-gate:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps encoder \
	  uv run --no-sync python colpali_service/scripts/encoder_gate.py
