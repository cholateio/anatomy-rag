.PHONY: help up up-gpu up-obs down logs migrate gpu-smoke golden-bytes ingest-sample test lint fmt

help:
	@echo "make up / up-gpu / up-obs / down / logs"
	@echo "make migrate      # 在 backend container 內跑 Alembic（連 PG_DIRECT_URL :5432，§0.3 例外）"
	@echo "make gpu-smoke    # 實機 GPU：build GPU encoder 並驗 torch.cuda.is_available()"
	@echo "make golden-bytes # 產生 Vercel UI Message Stream golden wire bytes"
	@echo "make test / lint / fmt / ingest-sample"

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

# 佔位（Phase 2+ ingest CLI 與樣本資料就緒後生效；需 ingest 映像或於 GPU 主機執行）。
ingest-sample:
	docker compose run --rm backend sh -c "uv run python -m anatomy_ingest.cli --pdf /data/sample.pdf --book-meta /data/sample.yaml --kb-version 1 --batch-size 4"

# 本機測試：先同步全部 workspace 成員（避免 uv run 預設剪除成員），再以 --no-sync 跑。
# 註：首次會安裝 ingest/encoder 的完整依賴（含 torch），之後快取於 .venv。
test:
	uv sync --all-packages --group dev
	uv run --no-sync pytest

lint:
	uv run --group dev ruff check .

fmt:
	uv run --group dev ruff format .
