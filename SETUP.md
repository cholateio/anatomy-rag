# SETUP.md — 從零到跑起來（新同事第一天上工指南）

本文件帶你在**一台全新機器**上把 anatomy-rag 跑起來。照著做，不需要再去問任何人「怎麼啟動／怎麼設定」。
全程預設**開發 / mock 模式**：encoder、LLM、auth 都走 mock，**不需要 GPU、也不需要 OpenAI 金鑰**即可完成基礎設施煙霧測試（infrastructure smoke test）。

> **目前進度誠實聲明（Phase 0）**：本階段交付的是「可重現的環境 + monorepo 骨架」。
> 跑起來後你會看到：所有服務健康、encoder 的**決定性 mock** `/encode_query` 可回應、前端骨架頁。
> **真正的端到端問答 `/chat` 在 Phase 8 才完成**——Phase 0 不宣稱 e2e。

---

## §0 你需要先準備的東西（前置）

| 軟體 | 版本 / 來源 | 用途 |
|---|---|---|
| **Docker Desktop**（Windows）+ WSL2 整合 | 最新穩定版 | 跑所有服務（Postgres/PgBouncer/Redis/MinIO/encoder/backend/frontend） |
| **uv** | ≥ 0.11（本專案以 0.11.16 驗證） | Python 套件 / workspace 管理（`uv.lock` 已鎖定版本） |
| **Node.js + npm** | Node ≥ 20（以 v24 驗證）、npm ≥ 10 | 前端骨架、產生 Vercel golden wire bytes |
| Git | 任意近期版本 | 取得程式碼 |
| （選用，僅 GPU 路徑）NVIDIA 驅動 + nvidia-container-toolkit | 支援 CUDA 12.8 的驅動 | 真實 ColPali encoder（Blackwell sm_120 → cu128） |

鎖定的關鍵版本（落地起手值，`uv.lock` / `package-lock.json` 已凍結）：
- 後端 / 建庫：Python ≥ 3.11（系統 3.12 亦可）、FastAPI、asyncpg、Alembic、pydantic v2 等（見 `uv.lock`）。
- 前端：`next@16.2.7`、`react@19.2.7`、`ai@6.0.197`、`@ai-sdk/react@3.0.199`。
- 容器映像：`pgvector/pgvector:pg16`（需 pgvector ≥ 0.8）、`bitnamilegacy/pgbouncer`（Bitnami 2025H2 下架免費 `docker.io/bitnami/*` latest，改用凍結的 legacy namespace；env 介面相同）、`redis:7-alpine`、`minio/minio`。

---

## §A 開發路徑（dev / mock，最常用）

### A.1 安裝 Docker Desktop + WSL2 整合

1. 在 **Windows** 安裝 Docker Desktop（官網下載安裝程式）。
2. 安裝時勾選 / 安裝後到 **Settings → General** 開啟 **Use the WSL 2 based engine**。
3. **Settings → Resources → WSL Integration**：開啟你的 WSL2 發行版（例如 Ubuntu）的整合開關。
4. 重開 WSL 終端機，驗證：
   ```bash
   docker --version
   docker compose version
   ```
   - ✅ **成功應看到**：兩個版本字串（如 `Docker version 27.x` / `Docker Compose version v2.x`）。
   - ❌ 若 `command not found`：回到 Docker Desktop 確認 WSL Integration 已對該發行版開啟，並重開終端機。

> **替代方案（不裝 Docker Desktop）**：可在 WSL2 發行版內安裝原生 `docker-ce`（`apt` 安裝 docker engine + `sudo service docker start`；GPU 需另裝 `nvidia-container-toolkit`）。本文件以 Docker Desktop 為主，原生安裝指令見官方文件。

### A.2 安裝 uv 與 Node（若尚未有）

```bash
# uv（若 `uv --version` 已可用可跳過）
curl -LsSf https://astral.sh/uv/install.sh | sh
# Node（建議用 nvm 或發行版套件管理員安裝 Node ≥ 20）
node --version && npm --version
```
- ✅ **成功應看到**：`uv 0.11.x`、`node v20+`、`npm 10+`。

### A.3 取得程式碼與建立 `.env`

```bash
git clone <repo-url> anatomy-rag   # 或進入已存在的目錄
cd anatomy-rag
cp .env.example .env
```
- ✅ **成功應看到**：`.env` 出現。**dev 預設值可直接用**（`ENCODER_MOCK=true`、`LLM_MOCK=true`、`AUTH_MODE=dev`），不必先填任何金鑰。
- 📝 之後要接真實 OpenAI 時才需把 `OPENAI_API_KEY` 填入並把 `LLM_MOCK` 改 `false`（見 §B.3）。

### A.4 啟動所有服務

```bash
make up
```
（等同 `docker compose up --build -d`，第一次會 build 映像，需數分鐘。）

- ✅ **成功應看到**：`docker compose ps` 中 `postgres / pgbouncer / redis / minio / encoder / backend / frontend` 皆為 **Up (healthy)**；`minio-init` 跑完即 Exit 0（建好 bucket）。
- 觀察：`make logs`（Ctrl-C 離開）。
- ❌ 卡在 `health: starting`：給它時間（healthcheck 有 retries）；若 `backend` 一直不 healthy，看 `docker compose logs backend`。

### A.5 跑 migrations（Phase 0 為框架驗證）

```bash
make migrate
```
此指令在 **backend 容器內**執行 Alembic（連 `PG_DIRECT_URL` 指向的 `postgres:5432`，這是 §0.3 唯一允許直連 :5432 的例外）。

- ✅ **成功應看到**：Alembic 正常結束。**Phase 0 尚無 migration 腳本，顯示「無可升級」屬正常**；這步只驗證 migration 框架可執行。Phase 2 起會有建表腳本。
- ❌ 「連不到 DB」：本指令在容器內連 `postgres:5432`（compose 網路）；**請勿**在宿主機直接跑 `alembic`。

### A.6 基礎設施煙霧測試（確認真的活著）

```bash
# 後端存活
test "$(curl -s localhost:8000/healthz)" = '{"status":"ok"}' && echo "backend OK"
# encoder readiness
curl -s localhost:8001/healthz | grep -q '"ready":true' && echo "encoder OK"
# encoder mock 的「決定性」：同一 query 兩次結果必須完全相同
A=$(curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' -d '{"q":"肱二頭肌"}')
B=$(curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' -d '{"q":"肱二頭肌"}')
[ "$A" = "$B" ] && echo "encoder mock 決定性 OK"
# 前端骨架頁
curl -s localhost:3000 | grep -q "系統骨架運行中" && echo "frontend OK"
```
- ✅ **成功應看到**：四行 `... OK`。
- 📝 `encode_query` 會回 `{"tokens_bin":[...], "pooled_bin":"<16-byte base64>", "model":"mock-colpali"}`；`pooled_bin` base64 解碼後為 16 bytes（= bit(128)）。
- 📝 前端 `:3000` 目前是**骨架頁**；真正的 `/chat` 在 Phase 8。

### A.7 本機跑測試與 lint（選用，不需 Docker）

```bash
make test    # 同步 workspace 成員後以 --no-sync 跑 pytest（首次會裝完整依賴含 torch，之後快取）
make lint    # ruff check .
```
- ✅ **成功應看到**：pytest 全綠、ruff `All checks passed!`。
- 📝 為什麼是 `make test` 而不是直接 `uv run pytest`？因為本 workspace 的 root 專案不依賴各成員套件，`uv run`／`uv sync` 預設會**剪除**成員套件導致 import 失敗；`make test` 已封裝正確配方（`uv sync --all-packages` + `uv run --no-sync`）。直接下指令時請比照。

---

## §B 生產 / GPU 路徑（接真實 ColPali encoder 與 OpenAI）

### B.1 GPU 前置（Blackwell：RTX 5060 Ti = sm_120）

1. Windows 端安裝支援 CUDA 12.8 的 NVIDIA 驅動；WSL2 會透過驅動取得 GPU。
2. 在 WSL2 安裝 **nvidia-container-toolkit**（讓容器能用 GPU），並於 Docker Desktop 確認 GPU 可用。
3. 驗證 WSL2 看得到 GPU：`nvidia-smi`（應列出你的 GPU）。

> **為何是 cu128**：RTX 5060 Ti 為 Blackwell 架構（sm_120），必須用 **CUDA 12.8 + cu128 的 PyTorch wheel**，並用 SDPA attention（**不**裝 flash-attn）。GPU encoder 的 `colpali_service/Dockerfile` 已據此設定（先 `uv sync` 再把 torch 換成 cu128 wheel）。

### B.2 GPU smoke gate（production 驗收前必過）

```bash
make gpu-smoke
```
- ✅ **成功應看到**：`CUDA OK: NVIDIA GeForce RTX 5060 Ti`（或你的 GPU 名稱）。
- ❌ `CUDA 不可用`：torch 非 cu128（確認 GPU Dockerfile 用 `--index-url .../cu128`）；或驅動不支援 CUDA 12.8。
- ❌ `no kernel image is available`：驅動 / CUDA 版本與 sm_120 不匹配，升級驅動。

### B.3 啟動真實 encoder + LLM

1. 編輯 `.env`：填入 `OPENAI_API_KEY=sk-...`（**MUST 用 OpenAI 標準付費 API，禁用免費／個人版**），把 `LLM_MOCK=false`。
2. 以 GPU override 啟動（encoder 改真實 ColPali、`ENCODER_MOCK=false`）：
   ```bash
   make up-gpu
   ```
- ✅ **成功應看到**：`encoder` 容器 healthy 且 `/healthz` 的 `model` 不再是 `mock-colpali`；GPU 被佔用（`nvidia-smi`）。

### B.4 觀測（選用）

```bash
make up-obs   # 另起 LangFuse（自帶獨立 Postgres，對外 :3100，避開 Next.js :3000）
```
填好 `.env` 的 `LANGFUSE_*` 後使用；LangFuse 用自己的 DB，不直連主 DB（避免違反 §0.3 :5432 紅線）。

---

## §C 常見錯誤排解

| 症狀 | 原因 / 解法 |
|---|---|
| `docker: command not found` | Docker Desktop 未開 WSL Integration；開啟後重開終端機。或改裝 WSL2 原生 docker-ce。 |
| 服務一直 `health: starting` 或 backend 不 healthy | 看 `docker compose logs <service>`；backend 依賴 pgbouncer/redis/encoder 皆 healthy 才啟動。 |
| **PgBouncer 啟動失敗 / 連不上** | 用 `bitnamilegacy/pgbouncer` 由 env 驅動，確認 `.env` 的 `POSTGRES_*` 正確。注意 Bitnami 已下架 `docker.io/bitnami/*` 免費 latest（2025H2），故改用凍結的 `bitnamilegacy/`；若連 legacy 都不可用，fallback：改 `edoburu/pgbouncer` 或自建 `infra/pgbouncer/Dockerfile` + entrypoint 由 env 產生 userlist。 |
| **Docker build 失敗（uv sync 找不到 workspace 成員）** | 各 Dockerfile 需 COPY 全部成員（shared/backend/colpali_service/ingest/eval）——已內建，若改動勿漏。 |
| **`make migrate` 連不到 DB** | 它在 backend 容器內跑、連 `postgres:5432`（compose 網路）；勿在宿主機直接 `alembic`。 |
| `uv run pytest` import 失敗 / 找不到 `anatomy_backend` | workspace root 不依賴成員 → `uv run` 預設 sync 會剪除成員。用 `make test`，或 `uv sync --all-packages` 後 `uv run --no-sync pytest`。 |
| **golden wire bytes 與後端 emitter 不符** | 重跑 `make golden-bytes`（用實際安裝的 `ai` 版本），Phase 8 emitter 對照 `infra/golden/ai_stream_golden.jsonl`（注意 `start` chunk 需自帶 `messageId`）。 |
| `make gpu-smoke` 失敗 | 見 §B.2；多為驅動 / cu128 不匹配。 |
| MinIO 啟不動 | `S3_SECRET_KEY` 至少 8 字元；`.env` 的 `S3_ACCESS_KEY/S3_SECRET_KEY` 與 MinIO root 一致。 |

---

## §D 這次（Phase 0）到底交付了什麼

- ✅ 可重現環境：`cp .env.example .env && make up` 在全新機器上把所有服務帶到 healthy。
- ✅ 基礎設施 smoke：健康探針 + encoder 的**決定性 mock** `/encode_query` + 前端骨架頁。
- ✅ monorepo 骨架（uv workspace）、版本鎖定（`uv.lock` / `package-lock.json`）、CI（unit + db-integration + gitleaks）、治理（`docs/decisions.md` DL-014~018）。
- ⏳ **尚未**：真實檢索 / 建庫 / `/chat`（Phase 1 起逐步交付，端到端在 Phase 8）。

有任何一步「成功應看到什麼」對不上，先查 §C；仍卡住再回報，並附上該步的指令與輸出。
