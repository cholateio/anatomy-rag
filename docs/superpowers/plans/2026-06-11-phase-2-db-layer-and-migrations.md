# Phase 2 — 資料庫層 + Migrations 實作計畫（含 DL-022 inference/client 紀錄欄位）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec §3 的完整資料庫層——可逆 Alembic migrations（books / pages / page_patches 分區 / query_logs / ingest_errors + 索引）、asyncpg 連線池（:6432、`statement_cache_size=0`）、kb_version 分區輔助、Stage A `SET LOCAL` 同 transaction helper（D-G），並依使用者 2026-06-11 新需求把 inference/client 紀錄（ip/country/user_agent/model_used/tool_used/tokens/cost_usd）落進 `query_logs`（DL-022）。

**Architecture:** 全部 migration 採手寫 raw SQL（`op.execute`；`target_metadata=None`，無 ORM），編號 001~007、每支含可逆 `downgrade`。`page_patches` 按 `kb_version` LIST 分區（DL-017），分區由 `ensure_kb_partition()` 於建庫時建立（不建 default 分區＝寫入未知版本 fail-fast）。應用層連線只走 PgBouncer :6432（pool.py），migrations 走 `PG_DIRECT_URL` :5432（既有 env.py，不動）。高頻 rate-limit 拒絕事件 **不入 DB**（Redis TTL 計數，Phase 8/9），DB 只記「每回合一列」的 query_logs。

**Tech Stack:** Alembic（async env.py 既有）、asyncpg（`BitString` 綁 bit(128)）、pgvector ≥0.8（`halfvec(128)` + `halfvec_cosine_ops` HNSW、`bit(128)` + `<~>`）、pytest（`db` marker，無 DB 環境自動 skip）。**不新增任何依賴**（alembic/asyncpg/sqlalchemy/numpy 皆已在 workspace）。

---

## 0. 設計定案（本計畫內生效；Task 1 寫入治理文件）

### 0.1 表的雙分類（使用者 2026-06-11 要求，寫入 spec §3.2 註記）

| 分類 | 表 | 用途 |
|---|---|---|
| 系統運作（檢索引擎資料） | `pages`、`page_patches` | Stage A/B 檢索；人不直接讀 |
| 人看的紀錄（稽核/維運/評估） | `books`、`query_logs`、`ingest_errors` | 書目稽核、查詢/回饋/成本紀錄、建庫失敗排查 |

### 0.2 DL-022（APPROVED 2026-06-11，裁決者=專案負責人）—— query_logs 擴充 + log 分層政策

- `query_logs` 擴充欄位（每回合一列，Phase 8 收尾以 `asyncio.create_task` 寫入）：
  - client 脈絡：`ip INET`、`country TEXT`（ISO 3166-1 alpha-2，**本地 GeoIP 推導、MUST NOT 呼叫外部 API**）、`user_agent TEXT`（應用層截斷 ≤512）
  - inference 紀錄：`status`、`cache_hit`、`model_used`、`tool_used JSONB`、`tokens_in`、`tokens_out`、`cost_usd NUMERIC(12,6)`
  - `clinical_flavored BOOLEAN DEFAULT FALSE`（§6.7 MAY 標記，預設關閉）
- **log 分層**：高頻事件（429 拒絕、token-bucket 狀態、abuse 計數）**MUST NOT 逐筆入 DB**；用 Redis TTL 計數器（跨 worker 一致；**禁止 per-process in-memory**），告警走 Phase 9 觀測層。
- 隱私：`ip`/`country`/`user_agent`/`user_id` 為內部觀測資料，**MUST NOT 進 LLM payload**；D-M（Sentry/LangFuse 脫敏）涵蓋這些欄位。
- schema 細節（表/欄位/索引）授權實作端自行調整（使用者 2026-06-11 核准，取代「新表先問」）；變更於 PR 說明，DECIDED schema 持續回寫 spec。

### 0.3 ingest_errors schema（spec §2 委派實作端設計，本計畫定案）

見 Task 6 的 007 migration DDL：`error_id / kb_version / book_id / page_num / stage / error_type / message / detail JSONB / resolved / created_at`，索引 `(kb_version, resolved)`。

### 0.4 Phase 2 明確不做（YAGNI）

- **不**把 pool 接進 FastAPI lifespan / `/healthz` / `/warmup`（Phase 8 串 `/chat` 時一起接，避免 unit job 與 mock 模式被 DB 依賴拖垮）。
- **不**建 `kb_versions` 表（active 版本=`settings.ACTIVE_KB_VERSION`，§6.6 DECIDED）。
- **不**寫 429 事件表、不做 GeoIP 推導實作（Phase 8）、不引入 ORM model。

---

## 1. 檔案結構地圖

```
backend/
├── alembic.ini                                  # 既有，不動
├── scripts/
│   └── bench_stage_b.py                         # Task 9：Stage B 延遲初步量測（手動，非 pytest）
├── src/anatomy_backend/
│   ├── config.py                                # Task 2：補附錄 A 缺漏欄位
│   └── db/
│       ├── __init__.py                          # 新增（空檔）
│       ├── pool.py                              # Task 7：asyncpg pool（:6432、statement_cache_size=0）
│       ├── kb_version.py                        # Task 5：active 版本 + ensure_kb_partition
│       ├── tx_helpers.py                        # Task 8：hnsw_search_txn（D-G）
│       └── migrations/
│           ├── env.py                           # 既有，不動
│           └── versions/                        # 新增目錄
│               ├── 001_extensions.py            # Task 4
│               ├── 002_books.py                 # Task 4
│               ├── 003_pages.py                 # Task 4
│               ├── 004_page_patches.py          # Task 5
│               ├── 005_query_logs.py            # Task 6
│               ├── 006_indexes.py               # Task 6
│               └── 007_ingest_errors.py         # Task 6
└── tests/
    ├── conftest.py                              # Task 3：db marker skip + alembic/conn fixtures
    ├── test_config.py                           # Task 2：補新欄位斷言（既有檔）
    ├── test_pool_unit.py                        # Task 7：無 DB 單元（kwargs 工廠）
    ├── test_migrations_db.py                    # Task 4：upgrade/downgrade roundtrip
    ├── test_schema_db.py                        # Task 4/5/6：schema 行為（分區/halfvec/bit/tsv/CHECK）
    ├── test_kb_version_unit.py                  # Task 5：參數驗證
    ├── test_pool_db.py                          # Task 7：真連線
    └── test_tx_helpers_db.py                    # Task 8：SET LOCAL 範圍
.github/workflows/ci.yml                         # Task 0：pgbouncer service 修復
Makefile                                         # Task 9：bench-stageb target
docs/decisions.md                                # Task 1：DL-022
docs/ARCHITECTURE.md                             # Task 1：§2.6 註、§3.2/§3.3 回寫、§6.8 註
.env.example                                     # Task 2：確認附錄 A 變數齊全（缺則補）
```

測試檔名維持**全域唯一**（root pyproject 的 pytest 約定）；`*_db.py` 檔首 `pytestmark = pytest.mark.db`。

執行模式：**subagent-driven**（implementer = Sonnet subagent、TDD；任務間主模型審查；終審 = Codex 跨模型）。本機跑 db 測試需 compose 起 postgres+pgbouncer 並 export `DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag`、`PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag`（CI db-integration job 已自帶）。無這兩個 env 時 db 測試自動 skip（unit job / 裸 `make test` 不受影響）。

---

### Task 0: 分支 + CI pgbouncer 前置修復

**Files:**
- Modify: `.github/workflows/ci.yml:36-46`

- [ ] **Step 0.1: 開分支**

```bash
git checkout -b feat/phase-2-db-layer
```

- [ ] **Step 0.2: 修 ci.yml pgbouncer service（缺 PGBOUNCER_DATABASE、無 health-cmd——db 測試會 race / 連錯庫）**

把 `db-integration` job 的 pgbouncer service 改為：

```yaml
      pgbouncer:
        image: bitnamilegacy/pgbouncer:latest    # Bitnami 下架 docker.io/bitnami/* latest（2025H2）→ bitnamilegacy（drop-in）。TODO(verify): pin digest
        env:
          POSTGRESQL_HOST: postgres
          POSTGRESQL_USERNAME: anatomy
          POSTGRESQL_PASSWORD: anatomy_dev_pw
          POSTGRESQL_DATABASE: anatomy_rag
          PGBOUNCER_DATABASE: anatomy_rag        # 顯式暴露 anatomy_rag（bitnami 預設只暴露 postgres 別名）
          PGBOUNCER_PORT: 6432
          PGBOUNCER_POOL_MODE: transaction
          PGBOUNCER_AUTH_TYPE: scram-sha-256
        ports: ["6432:6432"]
        options: >-
          --health-cmd "PGPASSWORD=anatomy_dev_pw psql -h 127.0.0.1 -p 6432 -U anatomy -d anatomy_rag -tAc 'SELECT 1'"
          --health-interval 5s --health-timeout 3s --health-retries 10
```

（docker-compose.yml 的 pgbouncer **不動**——實機驗收已通過，等價設定已存在。）

- [ ] **Step 0.3: db-integration job 強化（proxy 實測 + 假綠防呆；Codex 審查 MEDIUM）**

(a) 在「安裝 psql client」step 之後新增 PgBouncer 代理路徑實測（YAML parse 證明不了 service 可用）：

```yaml
      - name: 驗證 PgBouncer 代理路徑（:6432 → postgres）
        run: |
          out=$(PGPASSWORD=anatomy_dev_pw psql -h localhost -p 6432 -U anatomy -d anatomy_rag -tAc 'SELECT current_database()')
          test "$out" = "anatomy_rag" || (echo "PgBouncer 代理失敗: got '$out'" && exit 1)
```

(b) 「DB 整合測試」step 改為（**移除 exit-5 容忍**——Phase 2 起必有 db 測試，收不到=異常；
加 `REQUIRE_DB_TESTS` 防 env 漏傳時整批 skip 假綠，conftest 對應守門見 Task 3）：

```yaml
      - name: DB 整合測試（Phase 2 起生效）
        env:
          DATABASE_URL: postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag
          PG_DIRECT_URL: postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag
          REDIS_URL: redis://localhost:6379/0
          REQUIRE_DB_TESTS: "1"
        run: uv run --no-sync pytest backend/tests -q -m "db or integration"
```

- [ ] **Step 0.4: 驗證 YAML + commit**

```bash
uv run --no-sync python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml OK')"
git add .github/workflows/ci.yml
git commit -m "ci(phase-2): pgbouncer 補 PGBOUNCER_DATABASE/health-cmd + 代理實測 + REQUIRE_DB_TESTS 假綠防呆"
```

---

### Task 1: 治理 —— DL-022 寫入 decisions.md + 回寫 ARCHITECTURE.md

> 治理優先（與 Phase 0 Task 0.1 同慣例）：先改文件，後續任務才允許動 schema。

**Files:**
- Modify: `docs/decisions.md`（檔尾追加 DL-022；若檔首有決策索引表，同步加一列）
- Modify: `docs/ARCHITECTURE.md`：§2.6（line ~344）、§3.2（line ~400-411 query_logs 區塊）、§3.3（line ~437 之後）、§6.8（line ~959 之後）

- [ ] **Step 1.1: decisions.md 追加 DL-022**

先讀檔尾 DL-021 的格式（標題/狀態/裁決者/日期/內容結構），照同格式追加：

```markdown
## DL-022: query_logs 擴充 inference/client 紀錄 + log 分層政策（DB vs Redis）

- **狀態**：APPROVED（裁決者=專案負責人，2026-06-11）
- **背景**：使用者要求 AI inference 階段記錄 rate-limit 脈絡（ip/country/user_agent）與模型用量
  （model_used/tool_used/tokens/cost_usd），同時要求避免高頻 log 灌 DB 妨礙查詢；並全面授權
  實作端自行決定表/欄位設計（取代先前「新資料庫表先問」約定，原因=維護性顧慮延後處理）。
- **決議**：
  1. `query_logs` 擴充欄位（每回合一列）：`status`、`cache_hit`、`model_used`、`tool_used JSONB`、
     `tokens_in`、`tokens_out`、`cost_usd NUMERIC(12,6)`、`ip INET`、`country TEXT`（alpha-2）、
     `user_agent TEXT`（應用層截斷 ≤512）、`clinical_flavored BOOLEAN DEFAULT FALSE`（§6.7 MAY）。
     同時修 spec 內部矛盾：§6.5 MUST「👍/👎＋文字回饋」但 §3.2 `feedback` 為 SMALLINT
     存不了文字 → 加 `feedback_text TEXT`（2026-06-11 Codex 計畫審查 HIGH 發現）。
  2. 高頻事件（429 拒絕、token-bucket 狀態、abuse 計數）MUST NOT 逐筆入 DB；
     一律 Redis TTL 計數器（跨 worker 一致，禁 per-process in-memory），告警走觀測層（Phase 9）。
  3. 隱私：ip/country/user_agent/user_id MUST NOT 進 LLM payload；D-M 脫敏（Sentry/LangFuse）
     涵蓋上述欄位；country 推導 MUST 本地（GeoIP db），MUST NOT 呼叫外部 geo API。
  4. schema 細節（表/欄位/索引）授權實作端調整並於 PR 說明；DECIDED schema 持續回寫 §3.2。
- **影響**：§3.2（query_logs、ingest_errors 文件化）、§3.3（索引）、§6.8（限流紀錄分層）。
```

- [ ] **Step 1.2: ARCHITECTURE.md §3.2 回寫**

(a) 將 `query_logs` 的 CREATE TABLE 區塊替換為 Task 6 Step 6.2 的完整 DDL（含註解）；
(b) 在 §3.2 schema 區塊後追加 `ingest_errors` DDL（同 Task 6 Step 6.3）與一行說明
「`ingest_errors`（§2.6 建庫失敗紀錄；schema 依 DL-022 授權由實作定案）」；
(b2) `pages` DDL 補 `UNIQUE (kb_version, page_id)`、`page_patches` 改複合 FK
`FOREIGN KEY (kb_version, page_id) REFERENCES pages (kb_version, page_id)`
（同 Task 4 Step 4.5 / Task 5 Step 5.4——版本一致性，防錯版 patch 靜默漏檢）；
(c) 在 §3.2 開頭加表分類段：

```markdown
**表的雙分類**（維護導向，DL-022）：
- 系統運作（檢索引擎資料，人不直接讀）：`pages`、`page_patches`
- 人看的紀錄（稽核/維運/評估）：`books`、`query_logs`、`ingest_errors`
```

- [ ] **Step 1.3: ARCHITECTURE.md §3.3 / §2.6 / §6.8 回寫**

(a) §3.3 索引區塊追加：

```sql
CREATE INDEX query_logs_ip ON query_logs (ip, created_at DESC);   -- abuse 調查（DL-022）
CREATE INDEX ingest_errors_kb ON ingest_errors (kb_version, resolved);
```

(b) §2.6（「`ingest_errors` 表（schema 由實作 agent 設計）」一句）改為
「`ingest_errors` 表（schema 見 §3.2，DL-022 定案）」；
(b2) §4.3 規則清單追加一條（HIGH-1 修正的 spec 化）：

```markdown
- **MUST**：與 `SET LOCAL hnsw.ef_search` 同一 transaction 內加
  `SET LOCAL hnsw.iterative_scan = strict_order`（pgvector ≥0.8）——HNSW 為跨
  kb_version 全域索引，非 iterative 模式下帶版本過濾會撈不滿 Top-K（blue-green 期尤甚）。
```

(c) §6.8 規則清單追加一條：

```markdown
- **MUST（DL-022）**：限流拒絕等高頻事件不逐筆寫 DB；Redis TTL 計數器（跨 worker），
  每回合成功/失敗的單列紀錄才入 `query_logs`（含 ip/country/user_agent 供 abuse 調查）。
```

- [ ] **Step 1.4: commit**

```bash
git add docs/decisions.md docs/ARCHITECTURE.md
git commit -m "docs(phase-2): DL-022 query_logs 擴充 inference/client 紀錄 + log 分層政策；回寫 §2.6/§3.2/§3.3/§6.8"
```

---

### Task 2: config.py 補附錄 A 缺漏欄位（修審查遺留 medium 項）

**Files:**
- Modify: `backend/src/anatomy_backend/config.py:44-47`
- Modify: `backend/tests/test_config.py`
- Modify: `.env.example`（缺的變數補上）

- [ ] **Step 2.1: 在 test_config.py 追加失敗測試**

```python
def test_settings_has_appendix_a_fields(monkeypatch):
    """附錄 A 變數必須有對應欄位；extra=ignore 會靜默吞掉沒宣告的 key（審查遺留項）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pgbouncer:6432/db")
    monkeypatch.setenv("PG_DIRECT_URL", "postgresql://u:p@postgres:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("S3_BUCKET", "anatomy-rag-pages")
    monkeypatch.setenv("EVAL_OPENAI_API_KEY", "sk-eval-test")
    s = Settings(_env_file=None)
    assert s.s3_bucket == "anatomy-rag-pages"
    assert s.s3_endpoint == "http://minio:9000"
    assert s.eval_openai_api_key == "sk-eval-test"
    assert s.eval_openai_model == "gpt-5.5"
    assert s.langfuse_public_key == "" and s.langfuse_secret_key == ""
    assert s.sso_client_id == "" and s.sso_discovery_url == ""
    assert s.clinical_flavored_logging is False
```

（檔內既有測試的 Settings import / env 慣例照舊；`_env_file=None` 防本機 .env 干擾。）

- [ ] **Step 2.2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_config.py -q`
Expected: FAIL（`Settings` 無 `s3_bucket` 等屬性）

- [ ] **Step 2.3: config.py 補欄位**

在「觀測服務（選填）」段之前插入：

```python
    # 物件儲存（MinIO/S3；ingest 寫入、backend Phase 8 取頁圖）
    s3_bucket: str = "anatomy-rag-pages"
    s3_endpoint: str = "http://minio:9000"

    # Eval LLM（獨立 key，與生產分離；附錄 A）
    eval_openai_api_key: str = ""
    eval_openai_model: str = "gpt-5.5"

    # SSO（DL-016 暫緩；接回校內 SSO 時啟用）
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_discovery_url: str = ""

    # §6.7 MAY 旗標（預設關閉）：第一人稱症狀類 query 的 log 標記
    clinical_flavored_logging: bool = False
```

並把既有「觀測服務」段補齊：

```python
    # 觀測服務（選填）
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    sentry_dsn: str = ""
```

- [ ] **Step 2.4: 跑測試確認通過 + .env.example 核對**

Run: `uv run --no-sync pytest backend/tests/test_config.py -q` → PASS
Run: `grep -E "^(S3_BUCKET|S3_ENDPOINT|EVAL_OPENAI_API_KEY|EVAL_OPENAI_MODEL|LANGFUSE_PUBLIC_KEY|LANGFUSE_SECRET_KEY|SSO_CLIENT_ID|CLINICAL_FLAVORED_LOGGING|MT_MODEL|TRANSLATE_ENABLED)=" .env.example`
缺哪個就在 .env.example 對應段補上（附用途註解，值留空或 dev 預設）。

> 範圍註記（Codex 審查 MEDIUM 的裁決）：`MT_MODEL`/`TRANSLATE_ENABLED` 是 **colpali_service**
> 的設定（DL-020 翻譯在 encoder 服務內，Phase 3 實作其 config），**不加進 backend Settings**
> ——backend 不讀它們，加了就是死欄位。本 task 只保證 .env.example 文件齊全。

- [ ] **Step 2.5: commit**

```bash
git add backend/src/anatomy_backend/config.py backend/tests/test_config.py .env.example
git commit -m "fix(phase-2): config.py 補附錄 A 欄位（S3/EVAL/LANGFUSE/SSO/clinical flag；extra=ignore 靜默吞 key 審查遺留項）"
```

---

### Task 3: 測試基礎設施 —— conftest（db marker skip + alembic/連線 fixtures）

**Files:**
- Create: `backend/tests/conftest.py`
- Create: `backend/src/anatomy_backend/db/__init__.py`（空檔）
- Create: `backend/src/anatomy_backend/db/migrations/versions/`（目錄）

- [ ] **Step 3.1: 寫 conftest.py**

```python
"""backend 測試共用 fixtures。

db 標記測試需要 DATABASE_URL（:6432 經 PgBouncer）+ PG_DIRECT_URL（:5432，僅 alembic）。
兩者未設定時自動 skip——unit job 與裸 `make test`（無 compose）不受影響；
CI db-integration job 與本機 compose 環境會真跑。
"""
import os
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

_DB_ENV_READY = bool(os.environ.get("DATABASE_URL")) and bool(os.environ.get("PG_DIRECT_URL"))


def pytest_configure(config):
    # CI db-integration 設 REQUIRE_DB_TESTS=1：env 漏傳時直接 fail，
    # 不允許 db 測試整批 skip 還回綠燈（假綠防呆，Codex 審查 MEDIUM）
    if os.environ.get("REQUIRE_DB_TESTS") == "1" and not _DB_ENV_READY:
        raise pytest.UsageError("REQUIRE_DB_TESTS=1 但缺 DATABASE_URL / PG_DIRECT_URL")


def pytest_collection_modifyitems(config, items):
    skip_db = pytest.mark.skip(reason="需要 DATABASE_URL + PG_DIRECT_URL（CI db-integration 或本機 compose）")
    for item in items:
        if "db" in item.keywords and not _DB_ENV_READY:
            item.add_marker(skip_db)


@pytest.fixture(scope="session")
def alembic_cfg():
    """alembic Config，script_location 設為絕對路徑——不依賴 cwd（CI 在 repo 根跑 pytest）。"""
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(BACKEND_DIR / "src" / "anatomy_backend" / "db" / "migrations")
    )
    return cfg


@pytest.fixture(scope="session")
def migrated_db(alembic_cfg):
    """整個測試 session 先 upgrade 到 head（冪等；CI 的獨立 alembic step 已跑過也無妨）。"""
    from alembic import command

    command.upgrade(alembic_cfg, "head")
    yield


@pytest.fixture
async def db_conn(migrated_db):
    """單一 asyncpg 連線（經 PgBouncer :6432；transaction pooling 必須 statement_cache_size=0）。"""
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def clean_db(db_conn):
    """每測試前清空資料（TRUNCATE 沿 FK 連到 pages 與 page_patches 各分區）。"""
    await db_conn.execute("TRUNCATE books, query_logs, ingest_errors RESTART IDENTITY CASCADE")
    return db_conn
```

- [ ] **Step 3.2: 建空 package 檔與 versions 目錄**

```bash
touch backend/src/anatomy_backend/db/__init__.py
mkdir -p backend/src/anatomy_backend/db/migrations/versions
```

- [ ] **Step 3.3: 驗證 unit 環境不受影響（db 測試 0 收集、既有測試綠）**

Run: `uv run --no-sync pytest backend/tests -q`
Expected: 既有測試全 PASS（目前尚無 db 標記測試；conftest 匯入無錯）

- [ ] **Step 3.4: commit**

```bash
git add backend/tests/conftest.py backend/src/anatomy_backend/db/__init__.py
git commit -m "test(phase-2): conftest——db marker 環境守門 + alembic/asyncpg fixtures"
```

---

### Task 4: Migrations 001–003（extensions / books / pages）+ roundtrip 測試

**Files:**
- Create: `backend/src/anatomy_backend/db/migrations/versions/001_extensions.py`
- Create: `backend/src/anatomy_backend/db/migrations/versions/002_books.py`
- Create: `backend/src/anatomy_backend/db/migrations/versions/003_pages.py`
- Test: `backend/tests/test_migrations_db.py`

- [ ] **Step 4.1: 寫失敗測試 test_migrations_db.py**

```python
"""Alembic 可逆性（§3.5）：upgrade head ↔ downgrade base 無殘留。

同步測試（非 async）：env.py 內部 asyncio.run() 不能在已有 event loop 的協程內呼叫。
"""
import asyncio
import os

import pytest
from alembic import command

pytestmark = pytest.mark.db

TABLES = ["books", "pages", "page_patches", "query_logs", "ingest_errors"]


def _fetchval(sql: str):
    import asyncpg

    async def go():
        conn = await asyncpg.connect(os.environ["PG_DIRECT_URL"], statement_cache_size=0)
        try:
            return await conn.fetchval(sql)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_upgrade_downgrade_roundtrip(alembic_cfg):
    try:
        command.upgrade(alembic_cfg, "head")
        for t in TABLES:
            assert _fetchval(f"SELECT to_regclass('public.{t}')") is not None, f"{t} 應存在"

        command.downgrade(alembic_cfg, "base")
        for t in TABLES:
            assert _fetchval(f"SELECT to_regclass('public.{t}')") is None, f"{t} 應已移除"
        leftover = _fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
            "AND tablename NOT IN ('alembic_version')"
        )
        assert leftover == 0, "downgrade base 後不得殘留任何表"
    finally:
        # 不論成敗都回 head：中途 assert 失敗不可把 base 狀態留給同 session 的其他 db 測試
        command.upgrade(alembic_cfg, "head")
    assert _fetchval("SELECT to_regclass('public.pages')") is not None


def test_pgvector_version_at_least_0_8(migrated_db):
    """spec §3.1 MUST pgvector ≥0.8（halfvec/HNSW iterative_scan 皆依賴；映像為可變 tag 需實測）。"""
    ver = _fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    major, minor = (int(x) for x in ver.split(".")[:2])
    assert (major, minor) >= (0, 8), f"pgvector {ver} < 0.8"
```

- [ ] **Step 4.2: 跑測試確認失敗**

Run（需本機 compose 或 CI 環境的兩個 DB env）:
`uv run --no-sync pytest backend/tests/test_migrations_db.py -q`
Expected: FAIL（versions/ 空，upgrade head 後 `to_regclass('books')` 為 None）

- [ ] **Step 4.3: 001_extensions.py**

```python
"""001: PostgreSQL 擴充——vector(pgvector) + pg_trgm（§3.1）。"""
from alembic import op

revision = "001_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def downgrade() -> None:
    # 可逆（§3.5）；若仍有依賴物件，PostgreSQL 會擋下（fail-fast 即預期行為）
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
```

- [ ] **Step 4.4: 002_books.py**

```python
"""002: books——教材主檔（人看的紀錄表：書目 + 授權稽核；§3.2）。"""
from alembic import op

revision = "002_books"
down_revision = "001_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE books (
            book_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            title      TEXT NOT NULL,
            edition    TEXT,
            isbn       TEXT,
            license    TEXT,
            added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE books")
```

- [ ] **Step 4.5: 003_pages.py**

```python
"""003: pages——頁面層（系統運作表：Stage A 粗排 + LLM payload 來源；§3.2、DL-019）。"""
from alembic import op

revision = "003_pages"
down_revision = "002_books"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pages (
            page_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            book_id         UUID NOT NULL REFERENCES books(book_id),
            page_num        INTEGER NOT NULL,
            page_image_uri  TEXT NOT NULL,
            docling_md      TEXT NOT NULL,
            metadata        JSONB NOT NULL DEFAULT '{}',
            pooled          HALFVEC(128) NOT NULL,
            text_tsv        TSVECTOR
                             GENERATED ALWAYS AS (to_tsvector('simple', docling_md)) STORED,
            kb_version      INTEGER NOT NULL,
            embed_model     TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (book_id, page_num, kb_version),
            -- 供 page_patches 複合 FK（版本一致性；page_id 已是 PK 故此約束必然成立，
            -- 純為讓 (kb_version, page_id) 可被 REFERENCES）
            UNIQUE (kb_version, page_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE pages")
```

- [ ] **Step 4.6: 中途驗證（001–003 可往返；004+ 未到，roundtrip 測試仍 FAIL 屬預期）**

Run: `cd backend && PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag uv run --no-sync alembic -c alembic.ini upgrade head && PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag uv run --no-sync alembic -c alembic.ini downgrade base && cd ..`
Expected: 兩方向皆成功、無錯誤輸出

- [ ] **Step 4.7: commit**

```bash
git add backend/src/anatomy_backend/db/migrations/versions backend/tests/test_migrations_db.py
git commit -m "feat(phase-2): migrations 001-003——extensions/books/pages（halfvec pooled DL-019、tsv 生成欄）"
```

---

### Task 5: Migration 004（page_patches 分區）+ kb_version.py

**Files:**
- Create: `backend/src/anatomy_backend/db/migrations/versions/004_page_patches.py`
- Create: `backend/src/anatomy_backend/db/kb_version.py`
- Test: `backend/tests/test_kb_version_unit.py`、`backend/tests/test_schema_db.py`（本 task 先建檔，Task 6 再追加）

- [ ] **Step 5.1: 寫失敗測試 test_kb_version_unit.py（無 DB）**

```python
"""kb_version helper 參數驗證（無 DB；表名拼接前必須擋非 int——SQL injection 防線）。"""
import pytest

from anatomy_backend.db.kb_version import ensure_kb_partition, get_active_kb_version


class _FakeConn:
    def __init__(self):
        self.sql = None

    async def execute(self, sql):
        self.sql = sql


async def test_ensure_kb_partition_builds_expected_ddl():
    conn = _FakeConn()
    await ensure_kb_partition(conn, 7)
    assert conn.sql == (
        "CREATE TABLE IF NOT EXISTS page_patches_v7 "
        "PARTITION OF page_patches FOR VALUES IN (7)"
    )


@pytest.mark.parametrize("bad", ["7", 7.0, True, 0, -1, None])
async def test_ensure_kb_partition_rejects_non_positive_int(bad):
    with pytest.raises(ValueError):
        await ensure_kb_partition(_FakeConn(), bad)


def test_get_active_kb_version_reads_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:6432/db")
    monkeypatch.setenv("PG_DIRECT_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("ACTIVE_KB_VERSION", "4")
    from anatomy_backend.config import Settings

    assert get_active_kb_version(Settings(_env_file=None)) == 4
```

- [ ] **Step 5.2: 跑測試確認失敗**

Run: `uv run --no-sync pytest backend/tests/test_kb_version_unit.py -q`
Expected: FAIL（module 不存在）

- [ ] **Step 5.3: kb_version.py**

```python
"""kb_version 輔助：active 版本（§6.6 settings 驅動）+ 分區建立（DL-010/DL-017）。

page_patches 為 LIST 分區、不建 default 分區：寫入未知版本直接報
「no partition of relation」——fail-fast 優於靜默落錯區。分區由 ingest（Phase 4）
寫入前呼叫 ensure_kb_partition 建立；DDL 經 PgBouncer transaction pooling 亦可執行。
"""
from anatomy_backend.config import Settings, get_settings


def get_active_kb_version(settings: Settings | None = None) -> int:
    """目前服務中的知識庫版本（§6.6：settings.ACTIVE_KB_VERSION，blue-green 切換點）。"""
    return (settings or get_settings()).active_kb_version


def _validate_kb_version(kb_version: int) -> int:
    # bool 是 int 子類，明確排除；表名拼接前必為正整數（injection 防線）
    if type(kb_version) is not int or kb_version < 1:
        raise ValueError(f"kb_version 必須為正整數，收到 {kb_version!r}")
    return kb_version


async def ensure_kb_partition(conn, kb_version: int) -> None:
    """建立（冪等）page_patches 的 kb_version 分區：page_patches_v{N}。"""
    v = _validate_kb_version(kb_version)
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS page_patches_v{v} "
        f"PARTITION OF page_patches FOR VALUES IN ({v})"
    )
```

Run: `uv run --no-sync pytest backend/tests/test_kb_version_unit.py -q` → PASS

- [ ] **Step 5.4: 004_page_patches.py**

```python
"""004: page_patches——區塊層（系統運作表：Stage B MaxSim；§3.2、DL-017 分區）。

LIST 分區 by kb_version（分區鍵必在 PK 內）；分區本體由
anatomy_backend.db.kb_version.ensure_kb_partition 於建庫時建立，
本 migration 不建任何分區也不建 default（未知版本寫入 fail-fast）。
DROP TABLE 父表會一併移除所有分區（downgrade 可逆無殘留）。
FK 用複合 (kb_version, page_id)：單欄 FK 擋不住「v1 patch 指到 v2 page」的
跨版本錯配——錯配列會被路由進錯誤分區，之後所有帶 kb_version 的查詢靜默漏檢。
"""
from alembic import op

revision = "004_page_patches"
down_revision = "003_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE page_patches (
            kb_version  INTEGER NOT NULL,
            page_id     UUID NOT NULL,
            patch_idx   INTEGER NOT NULL,
            patch_bin   BIT(128) NOT NULL,
            PRIMARY KEY (kb_version, page_id, patch_idx),
            FOREIGN KEY (kb_version, page_id)
                REFERENCES pages (kb_version, page_id) ON DELETE CASCADE
        ) PARTITION BY LIST (kb_version)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE page_patches")
```

- [ ] **Step 5.5: 寫 test_schema_db.py（本 task 範圍：分區行為 + bit(128) 對照 shared oracle）**

```python
"""schema 行為整合測試（真 Postgres，經 PgBouncer :6432）。"""
import json
import os
import uuid

import numpy as np
import pytest
from anatomy_shared.binary import binarize, hamming_distance, to_pg_bits

from anatomy_backend.db.kb_version import ensure_kb_partition

pytestmark = pytest.mark.db

BOOK_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _vec_text(seed: int) -> str:
    rng = np.random.default_rng(seed)
    return "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"


async def _seed_book_and_page(conn, page_num=1, kb_version=1, md="biceps brachii origin"):
    await conn.execute(
        "INSERT INTO books (book_id, title) VALUES ($1, 'Gray''s Anatomy') "
        "ON CONFLICT (book_id) DO NOTHING",
        BOOK_ID,
    )
    return await conn.fetchval(
        "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
        " pooled, kb_version, embed_model)"
        " VALUES ($1, $2, 's3://x.png', $3, $4::jsonb, $5::halfvec, $6, 'colpali-v1.3-hf')"
        " RETURNING page_id",
        BOOK_ID, page_num, md, json.dumps({"page_type": "pure_text"}),
        _vec_text(page_num), kb_version,
    )


async def test_partition_routing_per_kb_version(clean_db):
    conn = clean_db
    await ensure_kb_partition(conn, 1)
    await ensure_kb_partition(conn, 2)
    p1 = await _seed_book_and_page(conn, page_num=1, kb_version=1)
    p2 = await _seed_book_and_page(conn, page_num=1, kb_version=2)
    tok = binarize(np.random.default_rng(0).standard_normal(128))
    await conn.execute(
        "INSERT INTO page_patches VALUES (1, $1, 0, $2::bit(128))", p1, to_pg_bits(tok)
    )
    await conn.execute(
        "INSERT INTO page_patches VALUES (2, $1, 0, $2::bit(128))", p2, to_pg_bits(tok)
    )
    rows = await conn.fetch(
        "SELECT kb_version, tableoid::regclass::text AS part FROM page_patches ORDER BY kb_version"
    )
    assert [(r["kb_version"], r["part"]) for r in rows] == [
        (1, "page_patches_v1"), (2, "page_patches_v2"),
    ]


async def test_insert_without_partition_fails_fast(clean_db):
    conn = clean_db
    pid = await _seed_book_and_page(conn, page_num=9, kb_version=3)  # pages 不分區，可插
    import asyncpg

    # v3 分區未建：PostgreSQL 拋 SQLSTATE 23514（no partition of relation … found for row）
    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await conn.execute(
            "INSERT INTO page_patches VALUES (3, $1, 0, $2::bit(128))",
            pid, "0" * 128,
        )
    assert exc.value.sqlstate == "23514"


async def test_fk_rejects_cross_version_mismatch(clean_db):
    """複合 FK：patch 的 (kb_version, page_id) 必須整組存在於 pages——
    防止 v1 patch 指到 v2 page 後被路由進錯誤分區而靜默漏檢。"""
    conn = clean_db
    await ensure_kb_partition(conn, 1)
    await ensure_kb_partition(conn, 2)
    pid_v2 = await _seed_book_and_page(conn, page_num=5, kb_version=2)
    import asyncpg

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(
            "INSERT INTO page_patches VALUES (1, $1, 0, $2::bit(128))", pid_v2, "1" * 128
        )


async def test_fk_cascade_deletes_patches(clean_db):
    conn = clean_db
    await ensure_kb_partition(conn, 1)
    pid = await _seed_book_and_page(conn, page_num=2, kb_version=1)
    await conn.execute(
        "INSERT INTO page_patches VALUES (1, $1, 0, $2::bit(128))", pid, "1" * 128
    )
    await conn.execute("DELETE FROM pages WHERE page_id = $1", pid)
    assert await conn.fetchval("SELECT count(*) FROM page_patches WHERE page_id=$1", pid) == 0


async def test_hamming_operator_matches_shared_oracle(db_conn):
    """SQL `<~>` 必須與 shared/binary.hamming_distance 一致（位序約定 to_pg_bits 單一來源）。"""
    rng = np.random.default_rng(42)
    for _ in range(5):
        a = binarize(rng.standard_normal(128))
        b = binarize(rng.standard_normal(128))
        sql_dist = await db_conn.fetchval(
            "SELECT $1::bit(128) <~> $2::bit(128)", to_pg_bits(a), to_pg_bits(b)
        )
        assert int(sql_dist) == hamming_distance(a, b)
```

- [ ] **Step 5.6: 跑 db 測試**

Run（compose / CI 環境）: `uv run --no-sync pytest backend/tests/test_schema_db.py backend/tests/test_migrations_db.py -q`
Expected: 全 PASS（roundtrip 含 004；分區路由/fail-fast/CASCADE/hamming 對照皆綠）

- [ ] **Step 5.7: commit**

```bash
git add backend/src/anatomy_backend/db backend/tests/test_kb_version_unit.py backend/tests/test_schema_db.py
git commit -m "feat(phase-2): 004 page_patches LIST 分區（DL-017）+ ensure_kb_partition；hamming SQL 對照 shared oracle"
```

---

### Task 6: Migrations 005–007（query_logs 擴充 / 索引 / ingest_errors）

**Files:**
- Create: `backend/src/anatomy_backend/db/migrations/versions/005_query_logs.py`
- Create: `backend/src/anatomy_backend/db/migrations/versions/006_indexes.py`
- Create: `backend/src/anatomy_backend/db/migrations/versions/007_ingest_errors.py`
- Modify: `backend/tests/test_schema_db.py`（追加測試）

- [ ] **Step 6.1: 追加失敗測試到 test_schema_db.py**

```python
async def test_query_logs_inference_and_client_columns(clean_db):
    """DL-022：每回合一列，含 client 脈絡與 inference 用量。"""
    conn = clean_db
    log_id = await conn.fetchval(
        "INSERT INTO query_logs (user_id, conversation_id, query_text, retrieved, answer,"
        " feedback, feedback_text, latency_ms, kb_version, status, cache_hit, model_used,"
        " tool_used, tokens_in, tokens_out, cost_usd, ip, country, user_agent)"
        " VALUES ($1, $2, '肱二頭肌起止點?', $3::jsonb, '…', 1, '引文頁碼正確', 1234, 1,"
        " 'ok', FALSE, 'gpt-5.5', $4::jsonb, 1500, 320, 0.012345, $5::inet, 'TW',"
        " 'Mozilla/5.0')"
        " RETURNING log_id",
        uuid.uuid4(), uuid.uuid4(),
        json.dumps([{"page_id": "x", "score": 0.9}]), json.dumps(["retrieval"]),
        "140.112.1.1",
    )
    row = await conn.fetchrow("SELECT * FROM query_logs WHERE log_id=$1", log_id)
    assert row["model_used"] == "gpt-5.5"
    assert row["feedback"] == 1 and row["feedback_text"] == "引文頁碼正確"  # §6.5
    assert row["tokens_in"] == 1500 and row["tokens_out"] == 320
    assert float(row["cost_usd"]) == pytest.approx(0.012345)
    assert str(row["ip"]) == "140.112.1.1" and row["country"] == "TW"
    assert row["clinical_flavored"] is False  # §6.7 預設關閉


async def test_query_logs_quality_checks(clean_db):
    """資料品質 CHECK（DL-022）：呼叫端餵錯值要在 DB 層被擋，否則成本/觀測資料不可分析。"""
    import asyncpg

    bad_inserts = [
        "INSERT INTO query_logs (user_id, query_text, feedback) VALUES ($1, 'q', 2)",
        "INSERT INTO query_logs (user_id, query_text, country) VALUES ($1, 'q', 'Taiwan')",
        "INSERT INTO query_logs (user_id, query_text, tokens_in) VALUES ($1, 'q', -5)",
        "INSERT INTO query_logs (user_id, query_text, cost_usd) VALUES ($1, 'q', -0.01)",
        "INSERT INTO query_logs (user_id, query_text, status) VALUES ($1, 'q', 'whatever')",
    ]
    for sql in bad_inserts:
        with pytest.raises(asyncpg.CheckViolationError):
            await clean_db.execute(sql, uuid.uuid4())


async def test_ingest_errors_unresolved_lookup(clean_db):
    conn = clean_db
    await conn.execute(
        "INSERT INTO books (book_id, title) VALUES ($1, 'Gray''s') ON CONFLICT DO NOTHING",
        BOOK_ID,
    )
    await conn.execute(
        "INSERT INTO ingest_errors (kb_version, book_id, page_num, stage, error_type, message)"
        " VALUES (1, $1, 812, 'encode', 'RuntimeError', 'CUDA OOM')",
        BOOK_ID,
    )
    rows = await conn.fetch(
        "SELECT page_num, stage FROM ingest_errors WHERE kb_version=1 AND NOT resolved"
    )
    assert [(r["page_num"], r["stage"]) for r in rows] == [(812, "encode")]


async def test_required_indexes_exist(db_conn):
    """§3.3 + DL-022 索引齊全；HNSW 用 halfvec_cosine_ops（DL-019）。"""
    names = {
        r["indexname"]
        for r in await db_conn.fetch("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
    }
    for expected in [
        "pages_pooled_hnsw", "pages_meta_gin", "pages_tsv_gin", "pages_kb_version",
        "query_logs_created", "query_logs_user", "query_logs_ip", "ingest_errors_kb",
    ]:
        assert expected in names, f"缺索引 {expected}"
    hnsw_def = await db_conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname='pages_pooled_hnsw'"
    )
    assert "hnsw" in hnsw_def and "halfvec_cosine_ops" in hnsw_def


async def test_tsvector_generated_and_cosine_query(clean_db):
    conn = clean_db
    await ensure_kb_partition(conn, 1)
    pid = await _seed_book_and_page(conn, page_num=3, kb_version=1, md="deltoid insertion humerus")
    hit = await conn.fetchval(
        "SELECT page_id FROM pages WHERE kb_version=1"
        " AND text_tsv @@ plainto_tsquery('simple', 'deltoid')"
    )
    assert hit == pid
    top = await conn.fetchval(
        "SELECT page_id FROM pages WHERE kb_version=1"
        " ORDER BY pooled <=> $1::halfvec LIMIT 1",
        _vec_text(3),  # 與該頁 pooled 同 seed → cosine 距離最小
    )
    assert top == pid
```

Run: `uv run --no-sync pytest backend/tests/test_schema_db.py -q`
Expected: 新增測試 FAIL（query_logs/ingest_errors/索引不存在）

- [ ] **Step 6.2: 005_query_logs.py**

```python
"""005: query_logs——人看的紀錄表（觀測/評估/回饋 + DL-022 inference/client 紀錄）。

每回合一列（Phase 8 收尾 asyncio.create_task 寫入）。高頻事件（429 等）不入本表
（DL-022：Redis TTL 計數）。ip/country/user_agent 僅供內部 abuse 調查與限流分析，
MUST NOT 進 LLM payload（D-M 脫敏涵蓋）。
"""
from alembic import op

revision = "005_query_logs"
down_revision = "004_page_patches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE query_logs (
            log_id          BIGSERIAL PRIMARY KEY,
            user_id         UUID NOT NULL,
            conversation_id UUID,                      -- DL-021：多輪分組（nullable）
            query_text      TEXT NOT NULL,
            retrieved       JSONB,                     -- top-3 page_ids + scores
            answer          TEXT,
            feedback        SMALLINT CHECK (feedback IN (-1, 0, 1)),
            feedback_text   TEXT,                      -- §6.5 MUST：👍/👎 附文字回饋
            latency_ms      INTEGER,
            kb_version      INTEGER,
            status          TEXT NOT NULL DEFAULT 'ok'
                             CHECK (status IN ('ok', 'llm_error', 'encoder_error',
                                               'retrieval_error', 'cancelled')),
            cache_hit       BOOLEAN NOT NULL DEFAULT FALSE,    -- 語意快取命中（命中時 model_used/tokens 為 NULL、cost=0）
            model_used      TEXT,                              -- 實際出話模型（含 fallback 後）
            tool_used       JSONB NOT NULL DEFAULT '[]',       -- 推理用到的工具名清單
            tokens_in       INTEGER CHECK (tokens_in IS NULL OR tokens_in >= 0),
            tokens_out      INTEGER CHECK (tokens_out IS NULL OR tokens_out >= 0),
            cost_usd        NUMERIC(12, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
            ip              INET,
            country         TEXT CHECK (country IS NULL OR country ~ '^[A-Z]{2}$'),  -- ISO 3166-1 alpha-2；本地 GeoIP 推導
            user_agent      TEXT,                              -- 應用層截斷 ≤512
            clinical_flavored BOOLEAN NOT NULL DEFAULT FALSE,  -- §6.7 MAY，預設關閉
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE query_logs")
```

- [ ] **Step 6.3: 007_ingest_errors.py（先寫 007 再寫 006 也可，revision 鏈固定即可）**

```python
"""007: ingest_errors——人看的紀錄表（建庫失敗排查 + --resume 依據；§2.6、DL-022 定案）。"""
from alembic import op

revision = "007_ingest_errors"
down_revision = "006_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ingest_errors (
            error_id    BIGSERIAL PRIMARY KEY,
            kb_version  INTEGER NOT NULL,
            book_id     UUID REFERENCES books(book_id),
            page_num    INTEGER,                       -- NULL = 整書層級失敗
            stage       TEXT NOT NULL,                 -- parse|render|encode|upload|write
            error_type  TEXT NOT NULL,                 -- 例外類別名
            message     TEXT NOT NULL,
            detail      JSONB NOT NULL DEFAULT '{}',   -- traceback 摘要 / batch 資訊
            resolved    BOOLEAN NOT NULL DEFAULT FALSE,-- 重跑成功後標記；--resume 跳過已 resolved
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ingest_errors_kb ON ingest_errors (kb_version, resolved)")


def downgrade() -> None:
    op.execute("DROP TABLE ingest_errors")
```

- [ ] **Step 6.4: 006_indexes.py**

```python
"""006: 索引——Stage A HNSW（halfvec cosine，DL-019）+ GIN + 版本/紀錄查詢（§3.3、DL-022）。"""
from alembic import op

revision = "006_indexes"
down_revision = "005_query_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX pages_pooled_hnsw ON pages USING hnsw (pooled halfvec_cosine_ops)"
        " WITH (m = 16, ef_construction = 64)"
    )
    op.execute("CREATE INDEX pages_meta_gin ON pages USING gin (metadata)")
    op.execute("CREATE INDEX pages_tsv_gin ON pages USING gin (text_tsv)")
    op.execute("CREATE INDEX pages_kb_version ON pages (kb_version)")
    op.execute("CREATE INDEX query_logs_created ON query_logs (created_at DESC)")
    op.execute("CREATE INDEX query_logs_user ON query_logs (user_id, created_at DESC)")
    # DL-022：abuse 調查（依 IP 回看時間序）
    op.execute("CREATE INDEX query_logs_ip ON query_logs (ip, created_at DESC)")


def downgrade() -> None:
    for idx in [
        "query_logs_ip", "query_logs_user", "query_logs_created",
        "pages_kb_version", "pages_tsv_gin", "pages_meta_gin", "pages_pooled_hnsw",
    ]:
        op.execute(f"DROP INDEX IF EXISTS {idx}")
```

- [ ] **Step 6.5: 跑全部 db 測試**

Run: `uv run --no-sync pytest backend/tests -q -m db`
Expected: 全 PASS（roundtrip 001–007 可逆、query_logs/ingest_errors/索引測試綠）

- [ ] **Step 6.6: commit**

```bash
git add backend/src/anatomy_backend/db/migrations/versions backend/tests/test_schema_db.py
git commit -m "feat(phase-2): 005-007——query_logs 擴充（DL-022）/ 索引（halfvec HNSW）/ ingest_errors"
```

---

### Task 7: pool.py —— asyncpg 連線池（:6432、statement_cache_size=0）

**Files:**
- Create: `backend/src/anatomy_backend/db/pool.py`
- Test: `backend/tests/test_pool_unit.py`、`backend/tests/test_pool_db.py`

- [ ] **Step 7.1: 寫失敗測試 test_pool_unit.py（無 DB；kwargs 工廠可純單元驗證紅線）**

```python
"""pool 連線參數工廠（無 DB）：statement_cache_size=0 是 PgBouncer transaction pooling 紅線。"""
from anatomy_backend.config import Settings
from anatomy_backend.db.pool import build_pool_kwargs


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@pgbouncer:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@postgres:5432/anatomy_rag",
        redis_url="redis://redis:6379/0",
    )
    base.update(over)
    return Settings(_env_file=None, **base)


def test_build_pool_kwargs_enforces_redlines():
    kw = build_pool_kwargs(_settings())
    assert kw["dsn"] == "postgresql://u:p@pgbouncer:6432/anatomy_rag"
    assert kw["statement_cache_size"] == 0          # 禁 prepared statements（§3.4）
    assert kw["min_size"] == 2 and kw["max_size"] == 10


def test_build_pool_kwargs_sizes_configurable():
    kw = build_pool_kwargs(_settings(db_pool_min_size=1, db_pool_max_size=25))
    assert kw["min_size"] == 1 and kw["max_size"] == 25
```

Run: `uv run --no-sync pytest backend/tests/test_pool_unit.py -q` → FAIL（module 不存在）

- [ ] **Step 7.2: config.py 加 pool 大小欄位（緊接資料庫連線段）**

```python
    # asyncpg pool 大小（§3.4 PgBouncer default_pool_size=25 上游守恆：max_size*workers ≤ 25）
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
```

- [ ] **Step 7.3: pool.py**

```python
"""asyncpg 連線池——應用層唯一 DB 入口（PgBouncer :6432；§3.4）。

transaction pooling 紅線：statement_cache_size=0（禁 prepared statements）、
禁 LISTEN/NOTIFY、禁 temp tables、DB 連線不得跨 LLM 串流持有（DL-012，Phase 8 落實）。
Phase 2 不接 FastAPI lifespan（Phase 8 串 /chat 時一起接）；模組級單例 + 工廠分離，
測試可注入 Settings。
"""
import asyncio

import asyncpg

from anatomy_backend.config import Settings, get_settings

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def build_pool_kwargs(settings: Settings) -> dict:
    """組 asyncpg.create_pool 參數；紅線集中此處供單元測試斷言。"""
    return {
        "dsn": settings.database_url,
        "min_size": settings.db_pool_min_size,
        "max_size": settings.db_pool_max_size,
        "statement_cache_size": 0,
    }


async def create_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """建立新 pool（不走單例；測試/腳本用）。"""
    return await asyncpg.create_pool(**build_pool_kwargs(settings or get_settings()))


async def get_pool() -> asyncpg.Pool:
    """應用層共享單例（lazy；首呼叫建立）。"""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = await create_pool()
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
```

Run: `uv run --no-sync pytest backend/tests/test_pool_unit.py backend/tests/test_config.py -q` → PASS

- [ ] **Step 7.4: 寫 test_pool_db.py（真連線經 PgBouncer）**

```python
"""pool 整合：經 PgBouncer :6432 真連線；同 SQL 跑兩次驗證 transaction pooling 相容。"""
import os

import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool

pytestmark = pytest.mark.db


async def test_pool_roundtrip_via_pgbouncer(migrated_db):
    pool = await create_pool(Settings(_env_file=None,
                                      database_url=os.environ["DATABASE_URL"],
                                      pg_direct_url=os.environ["PG_DIRECT_URL"],
                                      redis_url=os.environ.get("REDIS_URL", "redis://x:6379/0")))
    try:
        async with pool.acquire() as conn:
            assert await conn.fetchval("SELECT 1") == 1
        async with pool.acquire() as conn:
            # 同句再跑：statement_cache_size=0 下無 named prepared statement 殘留問題
            assert await conn.fetchval("SELECT 1") == 1
            assert await conn.fetchval("SELECT to_regclass('public.pages')") is not None
    finally:
        await pool.close()
```

Run: `uv run --no-sync pytest backend/tests/test_pool_db.py -q` → PASS

- [ ] **Step 7.5: commit**

```bash
git add backend/src/anatomy_backend/db/pool.py backend/src/anatomy_backend/config.py \
        backend/tests/test_pool_unit.py backend/tests/test_pool_db.py
git commit -m "feat(phase-2): asyncpg pool（:6432、statement_cache_size=0 紅線入廠測）"
```

---

### Task 8: tx_helpers.py —— Stage A SET LOCAL 同 transaction（D-G）

**Files:**
- Create: `backend/src/anatomy_backend/db/tx_helpers.py`
- Test: `backend/tests/test_tx_helpers_db.py`

- [ ] **Step 8.1: 寫失敗測試**

```python
"""D-G：SET LOCAL hnsw.ef_search 必須與 HNSW SELECT 同一 transaction
（transaction pooling 下同 txn = 同一後端連線，設定才作用到該查詢）。"""
import os

import pytest

from anatomy_backend.config import Settings
from anatomy_backend.db.pool import create_pool
from anatomy_backend.db.tx_helpers import hnsw_search_txn

pytestmark = pytest.mark.db


@pytest.fixture
async def pool(migrated_db):
    p = await create_pool(Settings(_env_file=None,
                                   database_url=os.environ["DATABASE_URL"],
                                   pg_direct_url=os.environ["PG_DIRECT_URL"],
                                   redis_url=os.environ.get("REDIS_URL", "redis://x:6379/0")))
    yield p
    await p.close()


async def test_set_local_scoped_to_txn(pool):
    async with pool.acquire() as conn:
        baseline = await conn.fetchval("SELECT current_setting('hnsw.ef_search')")
    async with hnsw_search_txn(pool, ef_search=100) as conn:
        assert await conn.fetchval("SELECT current_setting('hnsw.ef_search')") == "100"
        assert (
            await conn.fetchval("SELECT current_setting('hnsw.iterative_scan')")
            == "strict_order"
        )
    async with pool.acquire() as conn:
        # txn 結束後恢復 baseline（SET LOCAL 不外洩——D-G 的隔離；不綁死 pgvector 預設值）
        assert await conn.fetchval("SELECT current_setting('hnsw.ef_search')") == baseline


@pytest.mark.parametrize("bad", ["100", 0, -5, True, 1001])
async def test_ef_search_validation(pool, bad):
    with pytest.raises(ValueError):
        async with hnsw_search_txn(pool, ef_search=bad):
            pass


async def test_stage_a_query_fills_topk_across_versions(pool):
    """HIGH-1 回歸：HNSW 是跨版本全域索引，blue-green 雙版本並存時
    active 版本仍須回滿 Top-K（iterative_scan=strict_order 生效證明）。"""
    import json

    import numpy as np

    rng = np.random.default_rng(7)

    def vec() -> str:
        return "[" + ",".join(f"{x:.4f}" for x in rng.standard_normal(128)) + "]"

    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE books RESTART IDENTITY CASCADE")
        book = await conn.fetchval(
            "INSERT INTO books (title) VALUES ('topk-fill') RETURNING book_id"
        )
        rows = [
            (book, n, "s3://x.png", f"page {n}", json.dumps({}), vec(), kb, "colpali-v1.3-hf")
            for kb in (1, 2)
            for n in range(120)
        ]
        await conn.executemany(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1, $2, $3, $4, $5::jsonb, $6::halfvec, $7, $8)",
            rows,
        )
    async with hnsw_search_txn(pool, ef_search=100) as conn:
        # 小資料集 planner 會偏好 seq scan（那就測不到索引行為）；強制走 HNSW
        await conn.execute("SET LOCAL enable_seqscan = off")
        hits = await conn.fetch(
            "SELECT page_id FROM pages WHERE kb_version = 1"
            " ORDER BY pooled <=> $1::halfvec LIMIT 100",
            vec(),
        )
    assert len(hits) == 100  # 非 iterative 模式下雙版本約只回 ~50 筆
```

Run: `uv run --no-sync pytest backend/tests/test_tx_helpers_db.py -q` → FAIL（module 不存在）

- [ ] **Step 8.2: tx_helpers.py**

```python
"""Stage A 查詢的 transaction helper（D-G）。

PgBouncer transaction pooling 下，只有「同一 transaction」能保證 SET LOCAL 與
後續 SELECT 落在同一個 Postgres 後端連線；分開執行會各自拿到不同 server conn，
ef_search 形同未設。Stage A（Phase 5）MUST 經本 helper 跑 HNSW 查詢。

iterative_scan=strict_order（pgvector ≥0.8）：pages_pooled_hnsw 是跨 kb_version 的
全域索引，Stage A 必帶 WHERE kb_version 過濾；非 iterative 模式下 HNSW 先取
ef_search 個候選、過濾後才 LIMIT——blue-green 雙版本期會撈不滿 Top-K=100，
直接傷 DL-013 recall gate。strict_order 讓索引持續掃描直到湊滿 LIMIT。
"""
from contextlib import asynccontextmanager

_EF_SEARCH_MAX = 1000  # pgvector 合法範圍 1..1000；超過必為呼叫端錯誤


@asynccontextmanager
async def hnsw_search_txn(pool, ef_search: int = 100):
    """取得連線並開啟 transaction，SET LOCAL ef_search + iterative_scan 後交出 conn。"""
    if type(ef_search) is not int or not (1 <= ef_search <= _EF_SEARCH_MAX):
        raise ValueError(f"ef_search 必須為 1..{_EF_SEARCH_MAX} 的整數，收到 {ef_search!r}")
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 數值已驗證為純 int；SET 不支援參數綁定故用 f-string
            await conn.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
            await conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            yield conn
```

Run: `uv run --no-sync pytest backend/tests/test_tx_helpers_db.py -q` → PASS

- [ ] **Step 8.3: commit**

```bash
git add backend/src/anatomy_backend/db/tx_helpers.py backend/tests/test_tx_helpers_db.py
git commit -m "feat(phase-2): hnsw_search_txn——SET LOCAL 與 HNSW 查詢同 transaction（D-G）"
```

---

### Task 9: Stage B 延遲初步量測（DL-013 風險探針；手動、非 gate）

> 目的：對「Top-K=100 × ~18 tokens × 1024 patches/頁 ≈ 1.8M pair 的 SQL 聚合 <200ms」
> 做第一次實測數據（正式 gate 在 Phase 5 壓測；§4.4 已允許退路=應用層 numpy MaxSim）。
> 結果寫進 PR 描述供 Phase 5 參考，**不阻擋本 phase 驗收**。

**Files:**
- Create: `backend/scripts/bench_stage_b.py`
- Modify: `Makefile`

- [ ] **Step 9.1: bench_stage_b.py**

```python
"""Stage B MaxSim SQL 延遲初步量測（手動執行；DL-013 探針，非 CI gate）。

用法（需 migrations 已跑、compose 起 postgres+pgbouncer）：
  DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag \
  PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag \
  uv run --no-sync python backend/scripts/bench_stage_b.py [--pages 2000] [--candidates 100]

以 kb_version=999 建合成資料（跑完清除）；asyncpg.BitString 走 COPY 快速灌入。
"""
import argparse
import asyncio
import json
import os
import statistics
import time
import uuid

import asyncpg
import numpy as np

PATCHES_PER_PAGE = 1024
QUERY_TOKENS = 18
BENCH_KB = 999

STAGE_B_SQL = """
WITH query_tokens AS (
    SELECT token_idx, q_bits::bit(128) AS q_bin
    FROM unnest($1::text[]) WITH ORDINALITY AS qt(q_bits, token_idx)
),
token_max_per_page AS (
    SELECT pp.page_id, qt.token_idx,
           MAX(128 - (pp.patch_bin <~> qt.q_bin))::float AS sim
    FROM page_patches pp
    JOIN query_tokens qt ON true
    WHERE pp.page_id = ANY($2::uuid[])
      AND pp.kb_version = $3
    GROUP BY pp.page_id, qt.token_idx
)
SELECT page_id, SUM(sim) AS maxsim_score
FROM token_max_per_page
GROUP BY page_id
ORDER BY maxsim_score DESC
LIMIT 10
"""


def _rand_bits(rng) -> asyncpg.BitString:
    return asyncpg.BitString.frombytes(rng.bytes(16), bitlength=128)


async def seed(conn, n_pages: int) -> list[uuid.UUID]:
    rng = np.random.default_rng(0)
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS page_patches_v999 "
        "PARTITION OF page_patches FOR VALUES IN (999)"
    )
    book_id = await conn.fetchval(
        "INSERT INTO books (title) VALUES ('bench-only') RETURNING book_id"
    )
    page_ids = []
    for i in range(n_pages):
        pid = await conn.fetchval(
            "INSERT INTO pages (book_id, page_num, page_image_uri, docling_md, metadata,"
            " pooled, kb_version, embed_model)"
            " VALUES ($1, $2, 'bench', 'bench', '{}'::jsonb, $3::halfvec, $4, 'bench')"
            " RETURNING page_id",
            book_id, i, "[" + ",".join("0.01" for _ in range(128)) + "]", BENCH_KB,
        )
        page_ids.append(pid)
        records = [(BENCH_KB, pid, j, _rand_bits(rng)) for j in range(PATCHES_PER_PAGE)]
        await conn.copy_records_to_table(
            "page_patches", records=records,
            columns=["kb_version", "page_id", "patch_idx", "patch_bin"],
        )
        if (i + 1) % 200 == 0:
            print(f"  seeded {i + 1}/{n_pages} pages")
    return page_ids


async def cleanup(conn):
    await conn.execute("DROP TABLE IF EXISTS page_patches_v999")
    await conn.execute("DELETE FROM pages WHERE kb_version = $1", BENCH_KB)
    await conn.execute("DELETE FROM books WHERE title = 'bench-only'")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2000)
    ap.add_argument("--candidates", type=int, default=100)
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=5)
    args = ap.parse_args()

    rng = np.random.default_rng(1)
    direct = await asyncpg.connect(os.environ["PG_DIRECT_URL"], statement_cache_size=0)
    pooled = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        print(f"seeding {args.pages} pages × {PATCHES_PER_PAGE} patches（首次約數分鐘）…")
        page_ids = await seed(direct, args.pages)

        def make_query():
            cand = list(rng.choice(np.array(page_ids), size=args.candidates, replace=False))
            tokens = ["".join(f"{b:08b}" for b in rng.bytes(16)) for _ in range(QUERY_TOKENS)]
            return tokens, cand

        for _ in range(args.warmup):  # 排除冷 cache / plan 首跑的離群值
            tokens, cand = make_query()
            await pooled.fetch(STAGE_B_SQL, tokens, cand, BENCH_KB)

        latencies = []
        for _ in range(args.iters):
            tokens, cand = make_query()
            t0 = time.perf_counter()
            rows = await pooled.fetch(STAGE_B_SQL, tokens, cand, BENCH_KB)
            latencies.append((time.perf_counter() - t0) * 1000)
            assert len(rows) == 10
        latencies.sort()
        report = {
            "pages": args.pages, "candidates": args.candidates,
            "tokens": QUERY_TOKENS, "patches_per_page": PATCHES_PER_PAGE,
            "iters": args.iters,
            "p50_ms": round(statistics.median(latencies), 1),
            "p95_ms": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 1),
            "max_ms": round(latencies[-1], 1),
            "budget_ms": 200,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            "（單連線 microbenchmark、合成隨機 bits：只能回答『SQL 聚合本體量級』，"
            "不代表並發/真實資料 p95——DL-013 預算 200ms 的正式 gate 留 Phase 5；"
            "未達標 → 評估應用層 numpy MaxSim 退路，§4.4）"
        )
    finally:
        print("cleaning up bench data…")
        await cleanup(direct)
        await direct.close()
        await pooled.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 9.2: Makefile 加 target（help 同步補一行）**

```makefile
# Stage B MaxSim 延遲探針（手動；需 compose 起 DB + 已 migrate；DL-013，非 CI gate）
bench-stageb:
	uv sync --package anatomy-backend --inexact
	uv run --no-sync python backend/scripts/bench_stage_b.py
```

- [ ] **Step 9.3: 實機跑一次、記錄結果**

Run: `make migrate && DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag make bench-stageb`
Expected: 輸出 JSON 報告；把 p50/p95 數字記入 PR 描述與 phase 總結（不論是否達 200ms）。

- [ ] **Step 9.4: commit**

```bash
git add backend/scripts/bench_stage_b.py Makefile
git commit -m "feat(phase-2): Stage B MaxSim 延遲探針（DL-013；手動 bench，數據供 Phase 5 裁決）"
```

---

### Task 10: 收尾 —— 全綠驗證 + SETUP.md 補充 + Codex 終審

**Files:**
- Modify: `SETUP.md`（db 測試/bench 段）
- Modify: `docs/superpowers/plans/2026-06-07-anatomy-rag-roadmap.md`（Phase 2 勾稽，如有 checkbox）

- [ ] **Step 10.1: 全套驗證**

```bash
make lint                                   # ruff 乾淨
uv run --no-sync pytest -q                  # 無 DB env：unit 全綠、db 測試 skip
# compose 環境（或 CI）下：
docker compose up -d postgres pgbouncer redis
DATABASE_URL=postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag \
PG_DIRECT_URL=postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag \
uv run --no-sync pytest backend/tests -q -m db   # db 整合全綠
make migrate                                # container 內 upgrade head 成功
```

- [ ] **Step 10.2: SETUP.md 補「DB 整合測試與 bench」小節**

內容：本機跑 db 測試需 export 的兩個 URL（localhost 版）、`-m db` 用法、
`make bench-stageb` 用途與預期輸出、「無 env 自動 skip」說明。

- [ ] **Step 10.3: 終審（full profile）**

對整個 change set 跑 **Codex 跨模型審查**（schema migration 屬 MUST review 項）；
critical/high 必須解決或升級給使用者裁決；medium 記錄取捨。

- [ ] **Step 10.4: commit + 推分支開 PR**

```bash
git add SETUP.md docs/superpowers/plans/
git commit -m "docs(phase-2): SETUP.md db 測試/bench 段；phase 2 收尾"
git push -u origin feat/phase-2-db-layer
```

PR 描述需含：DL-022 摘要、表雙分類、bench 數據、CI db-integration 結果連結。

---

## 驗收標準（roadmap Phase 2 對照）

- [x→測試對應] `upgrade head` / `downgrade base` 可逆無殘留 → `test_upgrade_downgrade_roundtrip`
- [x] 真實 Postgres：`bit(128) <~>` 可運算且=shared oracle → `test_hamming_operator_matches_shared_oracle`
- [x] HNSW（halfvec_cosine_ops）/ GIN 存在 → `test_required_indexes_exist`
- [x] 分區生效（不同 kb_version 落不同分區）→ `test_partition_routing_per_kb_version`
- [x] pool 連 :6432 + `statement_cache_size=0` → `test_build_pool_kwargs_enforces_redlines` + `test_pool_roundtrip_via_pgbouncer`
- [x] SET LOCAL 同 transaction（D-G）→ `test_set_local_scoped_to_txn`
- [x] 新需求（DL-022）ip/country/user_agent/model_used/tool_used/tokens/cost_usd → `test_query_logs_inference_and_client_columns`
- [x] CI db-integration job 跑 migration + 上述測試（Task 0 修復後自動生效）
- [x] DL-013 延遲探針數據產出 → Task 9（非 gate）

Codex 計畫審查（2026-06-11）追加的驗收項：

- [x] 文字回饋可儲存（§6.5 MUST）→ `feedback_text` 欄 + `test_query_logs_inference_and_client_columns`
- [x] 跨版本錯配被 FK 擋下（HIGH-3）→ `test_fk_rejects_cross_version_mismatch`
- [x] 雙版本並存 Top-K 回滿（HIGH-1，iterative_scan）→ `test_stage_a_query_fills_topk_across_versions`
- [x] pgvector 實際版本 ≥0.8 → `test_pgvector_version_at_least_0_8`
- [x] DL-022 欄位資料品質 CHECK → `test_query_logs_quality_checks`
- [x] CI 假綠防呆（REQUIRE_DB_TESTS）+ PgBouncer 代理實測 → Task 0/Task 3

## 風險與註記

- **bitnamilegacy pgbouncer 在 GH Actions 的行為**：Task 0 加了 health-cmd 後若 image 內 `psql` 路徑/權限異常，fallback=改用 `pg_isready` 不可行（image 無此 bin），可改 `--health-cmd "nc -z 127.0.0.1 6432"`（busybox nc 存在性需驗）；最後手段=移除 health-cmd 僅留 PGBOUNCER_DATABASE（Step 0.3(a) 的代理實測 step 仍會把真問題擋下）。映像 digest pin 仍為 TODO（全映像一次處理）。
- **`no partition` 錯誤類別**：PostgreSQL 拋 SQLSTATE 23514 → asyncpg `CheckViolationError`；測試同時斷言 `sqlstate == "23514"`。若目標 PG16 實測映射不同，先改斷言並留註記（Codex 提醒此行為值得實測）。
- **`hnsw.iterative_scan`**：GUC 需 pgvector ≥0.8（`test_pgvector_version_at_least_0_8` 守住前提）。strict_order 在過濾稀疏時會多掃 tuple（上限 `hnsw.max_scan_tuples` 預設 20000）——延遲影響在 Phase 5 壓測時與 ef_search 一起調校；正確性（回滿 Top-K）優先於延遲是 DL-013 的既定取捨。
- **HNSW 建索引在空表上**：006 在空 `pages` 上建 HNSW 沒問題（增量插入自動進索引）；大量灌資料後 build 較優，但那是 Phase 4 ingest 的考量（必要時 `REINDEX`）。
- **status CHECK 列舉**：Phase 8 若需要新狀態值，加一支小 migration 改 CHECK 即可（資料乾淨優先於免改動）。
- **uv/pytest 坑（Phase 0 已知）**：一律 `uv sync --package anatomy-backend --inexact` 後 `uv run --no-sync`；測試檔名全域唯一。
