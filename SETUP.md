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
- 📝 `encode_query` 會回 `{"tokens_bin":[...], "pooled_f32":"<512-byte base64>", "translated_q":"...", "lang":"zh|en", "model":"mock-colpali", "mt_model":"mock-identity"}`；`tokens_bin` 每個元素 base64 解碼後 16 bytes（= bit(128)），`pooled_f32` 解碼後 512 bytes（= float32[128]，DL-019 不二值化）；`translated_q` 在 mock 模式為原文（DL-020 identity）。
- 📝 前端 `:3000` 目前是**骨架頁**；真正的 `/chat` 在 Phase 8。

### A.7 本機跑測試與 lint（選用，不需 Docker）

```bash
make test    # 同步 workspace 成員後以 --no-sync 跑 pytest（首次會裝完整依賴含 torch，之後快取）
make lint    # ruff check .
```
- ✅ **成功應看到**：pytest 全綠、ruff `All checks passed!`。
- 📝 為什麼是 `make test` 而不是直接 `uv run pytest`？因為本 workspace 的 root 專案不依賴各成員套件，`uv run`／`uv sync` 預設會**剪除**成員套件導致 import 失敗；`make test` 已封裝正確配方（`uv sync --all-packages` + `uv run --no-sync`）。直接下指令時請比照。

### A.8 DB 整合測試與 Stage B bench（Phase 2 起）

#### A.8.1 本機跑 DB 整合測試

**前置**：`make up`（至少 `postgres` + `pgbouncer` 皆 healthy）與 `make migrate`（已建好 Phase 2 資料表）。

> **⚠️ Image 重建提醒**：backend 程式碼已烤進 Docker image。若修改了 `backend/` 或 `migrations/` 的程式碼，需先重建再 migrate：
> ```bash
> docker compose build backend
> make migrate
> ```
> 否則容器內跑的仍是舊程式碼。

匯出連線環境變數（密碼從 `.env` 讀取，不用手動複製貼上）：

```bash
export PGPW=$(grep -E "^POSTGRES_PASSWORD=" .env | cut -d= -f2)
export DATABASE_URL="postgresql://anatomy:${PGPW}@localhost:6432/anatomy_rag"
export PG_DIRECT_URL="postgresql://anatomy:${PGPW}@localhost:5432/anatomy_rag"
export ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1
```

> ⚠️ **注意**：`ANATOMY_DB_TESTS_ALLOW_DESTRUCTIVE=1` 允許測試對目標 DB 執行 TRUNCATE 與 downgrade/upgrade——**只對可拋棄的 dev/CI DB 設這個旗標**；若 DB 名稱以 `_test` 結尾則免設（conftest 自動放行）。

執行 DB 整合測試：

```bash
uv run --no-sync pytest backend/tests -q -m db   # 只跑 db marker
# 或
uv run --no-sync pytest backend/tests -q          # 跑全套（unit + db）
```

- ✅ **成功應看到**：全綠（目前 38 tests：unit + db）。
- 📝 **未設環境變數時 db 測試會自動 skip**，unit job 與裸 `make test` 不受影響。CI 的 `db-integration` job 設有 `REQUIRE_DB_TESTS=1`；若環境變數漏傳，該 job 會直接 fail（而非假裝全過）。

#### A.8.2 Stage B 延遲探針（`make bench-stageb`）

用途：驗證 DL-013 規定的 **200 ms 兩階段總延遲預算**（Stage B MaxSim 精排部分的單連線 microbenchmark；此為非正式探針，正式 gate 於 Phase 5 完成）。

**前置**：已匯出 §A.8.1 的三個環境變數，且 `make migrate` 已執行。

```bash
make bench-stageb
```

腳本會自動：seeding 2000 頁 × 1024 patches 的合成資料（約需數分鐘）→ 執行延遲量測 → 輸出 JSON 報告 → **清除合成資料**（`kb_version=999`）。

- ✅ **成功應看到**：JSON 報告含 `p50 / p95 / max / budget_ms` 欄位。2026-06-11 WSL2 實測參考值：**p50 ≈ 157 ms / p95 ≈ 161 ms**，均在 200 ms 預算內。
- 📝 合成資料用獨立的 `kb_version=999`，不污染正式知識庫資料；benchmark 結束後自動刪除，不需手動清理。

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

### B.3 GPU encoder 啟用（Phase 3）

> **前置確認**
> - Phase 0 的 `make gpu-smoke` 已通過（cu128/driver 正常）。
> - 首次拉模型約需 **7 GB** 下載：`vidore/colpali-v1.3-hf` ≈ 6.6 GB、`Helsinki-NLP/opus-mt-zh-en` ≈ 312 MB；請確認已取得使用者授權再執行步驟 1。

#### B.3.1 步驟 1：預拉模型權重（`make encoder-models`）

```bash
make encoder-models
```

此指令會先 build GPU image（首次 build 含 cu128 torch wheel 下載，**約 10–20 分鐘**；之後 BuildKit cache 秒回），再把兩個模型下載進 named volume `anatomy-rag_hfcache`（容器內掛 `/hf-cache`，`HF_HOME` 指向它）。

- ✅ **成功應看到**：輸出結尾 `models cached`。
- 📝 實測：ColPali 10 檔約 7.5 分鐘、Marian 12 檔約 1.5 分鐘（依網速）；之後重建 image／重啟容器不需重抓。
- ❌ 下載中斷：直接重跑（`snapshot_download` 自動續傳）。

#### B.3.2 步驟 2：以 GPU 模式啟動 encoder（`make up-gpu`）

```bash
make up-gpu
```

encoder 以真實模式啟動（`ENCODER_MOCK=false`）。模型載入期間 `/healthz` 回 **503**（`{"ready": false, ...}`），這是預期行為——compose healthcheck（10s × 60、start_period 60s）會等它。

- ✅ **成功應看到**（權重已快取時載入約 30–60 秒；實測容器啟動後 ~30 秒內 healthy）：
  ```bash
  curl -s localhost:8001/healthz
  # → {"ready":true,"model":"vidore/colpali-v1.3-hf","mt_model":"Helsinki-NLP/opus-mt-zh-en"}
  ```
- ❌ healthz 持續 503 且 body 有 `error` 欄：`docker logs anatomy-rag-encoder-1` 看載入失敗原因（loading_info 守門 fail-fast 會在此呈現）。
- ❌ CUDA 不可用：回跑 `make gpu-smoke`（§B.2）確認驅動。
- ❌ VRAM 不足：關閉其他 GPU 程式（ColPali bf16 約需 **7 GB** VRAM）。

#### B.3.3 步驟 3：真實 `/encode_query` 契約驗收（2026-06-13 實測通過）

```bash
curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' \
  -d '{"q": "肱二頭肌的起止點"}' | python3 -c "
import json,sys,base64
j = json.load(sys.stdin)
assert len(base64.b64decode(j['pooled_f32'])) == 512
assert all(len(base64.b64decode(t)) == 16 for t in j['tokens_bin'])
assert j['lang'] == 'zh' and j['model'] == 'vidore/colpali-v1.3-hf'
print('契約 OK | tokens:', len(j['tokens_bin']), '| translated_q:', j['translated_q'])"
curl -s -X POST localhost:8001/warmup     # → {"warmed":true}
```

- ✅ **成功應看到**：`契約 OK | tokens: 20 | translated_q: biceps brachii origin and insertion`
- 📝 glossary 命中、虛詞「的」丟棄；token 數隨 query 而異，**下游不可假設固定值**。

#### B.3.4 步驟 4：gpu/mt 測試與 smoke gate

```bash
# GPU image 為 --no-dev（無 pytest）→ 容器內先補 dev 群組再跑
docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps encoder \
  sh -c "uv sync --group dev --inexact && uv run --no-sync pytest colpali_service/tests/test_real_runtime_gpu.py -q"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm --no-deps -e RUN_MT_TESTS=1 encoder \
  sh -c "uv sync --group dev --inexact && uv run --no-sync pytest colpali_service/tests/test_marian_mt.py -q"
make encoder-gate
```

- ✅ **成功應看到**：`encoder-gate` 結尾 `GATE PASS`（exit 0）。
  - gate 行為：16 偽頁面 + 24 題 zh/en，MaxSim 與 pooled cosine 雙軌 recall@3 皆須過門檻（en ≥ 0.9/0.75、zh ≥ 0.75/0.6）。
  - 實測基準（2026-06-13，RTX 5060 Ti/WSL2）：**四軌 recall@3 全為 1.000**；`encode_query`
    延遲（含 MT）p50=56ms／max=140ms；模型載入 12.5s（權重已快取）。gpu 測試 6/6、mt 測試 2/2 通過。
  - 已知 MT 粗糙點（gate 已吸收、Phase 11 真實教材 gate 再校）：裸 Marian 對單字/碎片段
    品質差（如「幹」「韌帶」誤譯）；glossary 詞條命中者不受影響。
- ❌ gate 失敗：先看每題印出的 `translated_q` 品質（MT 問題 → 補 `colpali_service/glossary_zh_en.tsv` 詞條後重跑）；仍不過 → 依 DL-020 升級序（NLLB-600M → 跨語言 encoder）回報專案負責人裁決。**門檻不可為過關調低**（調整須附理由記入 PR）。
- 📝 與 `make up-gpu` 的服務容器同時跑會雙載模型（~13 GB VRAM）；16 GB 卡可行，VRAM 吃緊時先 `docker compose stop encoder` 再跑測試。

#### B.3.5 回退 mock

無 GPU 或測試環境需 mock encoder 時：

```bash
make up    # 核心 compose 即 mock encoder，無 GPU 需求
```

### B.4 啟動真實 encoder + LLM

> ⚠️ Phase 3 的 encoder 啟用詳見 §B.3；本節補充接上真實 LLM 的步驟。

1. 編輯 `.env`：填入 `OPENAI_API_KEY=sk-...`（**MUST 用 OpenAI 標準付費 API，禁用免費／個人版**），把 `LLM_MOCK=false`。
2. 以 GPU override 啟動（encoder 改真實 ColPali、`ENCODER_MOCK=false`）：
   ```bash
   make up-gpu
   ```
- ✅ **成功應看到**：`encoder` 容器 healthy 且 `/healthz` 的 `model` 不再是 `mock-colpali`；GPU 被佔用（`nvidia-smi`）。

### B.5 觀測（選用）

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

---

## §F 離線建庫管線（ingest，Phase 4）

> 離線批次：PDF + 書籍 YAML → Docling 逐頁 Markdown/metadata、pdf2image PNG、ColPali 編碼 →
> 二值化 → 寫 Postgres（pages/page_patches，帶 kb_version）+ 上傳 PNG 到 MinIO。
> **離線管線 MUST NOT 呼叫任何雲端 LLM API**（CI grep 守門 + socket guard 測試）。

### F.1 系統前置（poppler）

pdf2image 需 poppler 後端（`pdftoppm`）。host GPU venv 與任何真實建庫環境須安裝：

```bash
sudo apt-get update && sudo apt-get install -y poppler-utils
pdftoppm -v   # 驗證有輸出版本
```

> mock smoke（`make ingest-sample`，§F.2）走 `--synthetic` 合成頁面源，**不需 poppler/GPU**。

### F.2 mock smoke（無 GPU / 無 poppler / 無真 PDF）

合成 N 頁 + 決定性 mock runtime，寫入真 DB/MinIO，驗端到端寫入路徑：

```bash
make up && make migrate      # 起服務 + migrate（首次）
make ingest-sample           # synthetic 6 頁 → kb_version=1
```

成功應看到多行 `[batch] 寫入 [...]` 與 `[done] 共寫入 6 頁、失敗 0 頁；抽樣校驗 {'sampled': 1, 'mismatches': []}`。

### F.3 真實建庫 / GPU gate

```bash
# 端到端 GPU gate（3 頁可區辨 PDF + real ColPali + 真 MinIO/PG；需 poppler + GPU venv）
make ingest-gate

# 正式建庫（首次：不帶 --book-id）
uv run --no-sync python -m anatomy_ingest.cli \
    --pdf /data/books/gray_42e.pdf --book-meta /data/books/gray_42e.yaml \
    --kb-version 1 --batch-size 8

# 重建同一本書（先刪該 book+kb_version 既有頁）
uv run --no-sync python -m anatomy_ingest.cli \
    --pdf ... --book-meta ... --kb-version 1 --book-id <UUID>

# 從失敗頁續跑（須帶 --book-id；跳過 pages 已存在的頁）
uv run --no-sync python -m anatomy_ingest.cli \
    --pdf ... --book-meta ... --kb-version 1 --book-id <UUID> --resume
```

`make ingest-gate` 成功應看到逐頁 `[gate] page N patches=... png=...B OK` 與最後 `[gate] PASS`。
