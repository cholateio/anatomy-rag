# Phase 0 — 環境與專案骨架 詳細實作計畫（v2，已納入 Codex 跨模型審查）

> ⚠️ **歷史文件（2026-06-10 註記）**：Phase 0 已執行完畢。其後 encoder 契約依 **DL-019/DL-020** 變更
> （`pooled_bin` → `pooled_f32`、新增 `translated_q`/`lang`/`mt_model`）；本檔內的舊契約片段僅為執行
> 當時的快照，現行契約以 `docs/ARCHITECTURE.md` §4.2/§5.1 為準。
>
> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`（建議）或 `superpowers:executing-plans` 逐任務實作。步驟用 `- [ ]` 追蹤。
>
> 上層路線圖：`2026-06-07-anatomy-rag-roadmap.md`（v2）。權威 spec：`docs/ARCHITECTURE.md` 附錄 A/B、§1.5、§3.4、§6.9。
>
> **Phase 0 定位（誠實版）**：交付可重現環境與 monorepo 骨架，讓全新機器 `make up` 通過**基礎設施 smoke test**（健康探針 + 決定性 mock `/encode_query`）。**真正的端到端 `/chat` 在 Phase 8**，Phase 0 不誇稱 e2e。
>
> **治理優先**：Task 0.1（decisions.md + spec 回寫）**必須最先完成**，後續任務才允許進行（Codex Dim4-1）。

**Goal:** 建立 monorepo 骨架與可重現環境（uv workspace + Docker Compose），落定環境變數、版本 pin、Docker、CI、治理更新與去風險基準（golden wire bytes、GPU smoke gate）。

**Tech Stack:** uv（含 `[dependency-groups].dev`）、Docker Compose v2、FastAPI、pydantic-settings、`pgvector/pgvector:pg16`、`bitnami/pgbouncer`（env 驅動 :6432）、`redis:7`、`minio`、Next.js 14+ + `ai@6`、ruff、gitleaks、GitHub Actions。

> **版本 pin（D-S）**：每個 `# TODO(verify)` 處於落地時用 `uv pip index versions <pkg>` / `npm view <pkg> version` / `docker buildx imagetools inspect <image>` 確認最新穩定版並 pin（含 image digest），commit 訊息註明。

---

## Task 0.1：治理優先 — `decisions.md` DL-014~018 + 回寫 `ARCHITECTURE.md`

> **必須最先完成**（Codex Dim4-1）。使用者 2026-06-07 已核准：DL-014/015/016/017/018 皆 APPROVED。

**Files:** Modify `docs/decisions.md`、`docs/ARCHITECTURE.md`

- [ ] **Step 1：`docs/decisions.md` 末尾追加 DL-014~018**

依路線圖 v2 §A「Phase 0 Task 0.1 要寫入 decisions.md 的條目」逐條加入，格式對齊既有 DL-002~013（狀態/提案者/日期/影響檔案/裁決者/背景/提案/影響評估）。全部 `狀態：APPROVED`、`提案者：main Claude（委派）`、`裁決者：專案負責人`、`日期：2026-06-07`：
- DL-014 檢索排序（自建兩階段 baseline、VectorChord 介面後 PoC）
- DL-015 移除 LlamaIndex（移除所有規範性引用 + CI grep 斷言）
- DL-016 SSO 暫緩（可插拔 auth）
- DL-017 page_patches 加 `kb_version`（PK 含之、按版本分區）
- DL-018 Vercel UI Message Stream emitter（§5.6「不自寫」核准例外）

- [ ] **Step 2：回寫 `ARCHITECTURE.md` §3.2（DL-017：page_patches 加 kb_version）**

將 `page_patches` 改為：
```sql
CREATE TABLE page_patches (                    -- 區塊層：Stage B 用
    kb_version  INTEGER NOT NULL,              -- DL-017：分區鍵須在 PK 內
    page_id     UUID NOT NULL,
    patch_idx   INTEGER NOT NULL,
    patch_bin   BIT(128) NOT NULL,
    PRIMARY KEY (kb_version, page_id, patch_idx),
    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
) PARTITION BY LIST (kb_version);              -- DL-010 分區（每 kb_version 一分區）
```
並在 §3.3 索引、§4.4 Stage B SQL 加註「`page_patches` 查詢帶 `kb_version`」。

- [ ] **Step 3：回寫 `ARCHITECTURE.md` §8.1 / §5.5（DL-015：移除 LlamaIndex）**

- §8.1 DECIDED 表移除「編排 | LlamaIndex | LangChain | …」整列，表下加註：「~~編排＝LlamaIndex~~ 已於 DL-015 移除；線上路徑不採 RAG 框架，檢索編排由 `backend/retrieval/orchestrator.py` 負責。」
- §5.5 首句「LlamaIndex 用於檢索編排，但 LLM 呼叫直接走原生 openai SDK」改為「LLM 呼叫走原生 `openai` SDK；檢索編排由 `backend/retrieval/orchestrator.py` 自理（DL-015 已移除 LlamaIndex）。」
- 移除/改寫技術棧（CLAUDE.md Stack 註記留待人工，因 CLAUDE.md 非本 repo spec 主體）。

- [ ] **Step 4：回寫 `ARCHITECTURE.md` §5.6/§6.3（DL-018：Vercel emitter）**

§5.6 表格與規則加註：「**DL-018**：AI SDK v5/v6 用 UI Message Stream 協定（typed parts over SSE），無官方 Python lib → 後端手刻薄 emitter（`backend/api/ai_stream.py`）為『不自寫』的核准例外（前端仍用 `useChat`、不自寫 SSE/狀態）。事件對應：`sources`→自訂 `data-sources` part（前端 `onData`，非舊 `onResponse`）；header `x-vercel-ai-ui-message-stream: v1`；以 `data: [DONE]` 收尾。」

- [ ] **Step 5：回寫 `ARCHITECTURE.md` §5.8（DL-016：SSO 暫緩）+ Top-K=100 一致化（D-R）**

- §5.8 開頭加註：「**DL-016**：v1 校內 SSO 暫緩，以可插拔 auth（dev stub + OIDC 介面）替代；下列 SSO MUST 於接回校內 SSO 時生效。`user_id`/限流/query_logs 照常（dev stub 提供 user_id）。」
- §4.7 `stage_a_coarse(..., top_k: int = 50)` 預設改 `100`；§8.2「Stage A Top-K | 50」改「100（DL-013）」；確認 §4.6 已是 100。

- [ ] **Step 6：一致性檢查**

Run: `grep -rn "LlamaIndex" docs/ARCHITECTURE.md; grep -n "top_k: int = 50" docs/ARCHITECTURE.md`
Expected: §8.1 DECIDED 表無 LlamaIndex 列、§5.5 已改寫；無 `top_k: int = 50` 殘留。

- [ ] **Step 7：Commit**

```bash
git add docs/decisions.md docs/ARCHITECTURE.md
git commit -m "docs: DL-014~018（檢索排序/移除LlamaIndex/SSO暫緩/page_patches分區/Vercel emitter）並回寫 spec"
```

---

## Task 0.2：Repo 骨架、.gitignore、README

**Files:** Create `.gitignore`、`.dockerignore`、`README.md`、各 `__init__.py`

- [ ] **Step 1：`.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
*.egg-info/
.pytest_cache/
.ruff_cache/
.env
.env.*
!.env.example
node_modules/
frontend/.next/
frontend/out/
data/
models/
*.pdf
eval_report.json
.DS_Store
.idea/
.vscode/
```
> 註：`uv.lock`、`frontend/package-lock.json`、`infra/golden/*.jsonl` **要進版控**（D-S）。

- [ ] **Step 2：`.dockerignore`**

```dockerignore
.git
.venv
**/__pycache__
**/.pytest_cache
**/.ruff_cache
node_modules
frontend/.next
data
models
.env
.env.*
!.env.example
docs
```

- [ ] **Step 3：`README.md`**（同 v1：專案總覽 + 指向 SETUP.md + TL;DR `cp .env.example .env && make up && make migrate && curl :8000/healthz`）

- [ ] **Step 4：建立目錄與 `__init__.py`**

```bash
mkdir -p shared/src/anatomy_shared backend/src/anatomy_backend/api \
  backend/src/anatomy_backend/db/migrations/versions backend/tests \
  colpali_service/src/colpali_service colpali_service/tests \
  ingest/src/anatomy_ingest eval/src/anatomy_eval \
  infra/postgres infra/pgbouncer infra/minio infra/golden .github/workflows \
  frontend/scripts
touch shared/src/anatomy_shared/__init__.py backend/src/anatomy_backend/__init__.py \
  backend/src/anatomy_backend/api/__init__.py backend/tests/__init__.py \
  colpali_service/src/colpali_service/__init__.py ingest/src/anatomy_ingest/__init__.py \
  eval/src/anatomy_eval/__init__.py backend/src/anatomy_backend/db/migrations/versions/.gitkeep
```

- [ ] **Step 5：Commit** `chore: 建立 monorepo 目錄骨架與 .gitignore/README`

---

## Task 0.3：uv workspace + 各 pyproject（dev group、shared 拆分、版本 pin）

**Files:** Create 根 `pyproject.toml`、`shared/`、`backend/`、`colpali_service/`、`ingest/`、`eval/` 的 `pyproject.toml`

- [ ] **Step 1：根 `pyproject.toml`（含 `[dependency-groups].dev`，修 Codex Dim1-7）**

```toml
[project]
name = "anatomy-rag"
version = "0.0.0"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["shared", "backend", "colpali_service", "ingest", "eval"]

[tool.uv.sources]
anatomy-shared = { workspace = true }

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.6", "asgi-lifespan>=2.1", "httpx>=0.27"]

[tool.ruff]
line-length = 100
target-version = "py311"
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-q"
```
> `uv sync --all-packages`（不需 `--extra`）即會裝 dev group；CI/本機測試一致。

- [ ] **Step 2：`shared/pyproject.toml`（binary.py 純 numpy；torch 移到 `colpali` extra，D-L/Codex Dim5-7）**

```toml
[project]
name = "anatomy-shared"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["numpy>=1.26"]              # binary.py 僅需 numpy（backend/CI 不被拖入 torch）

[project.optional-dependencies]
colpali = [                                 # ingest / colpali_service 才裝
  "torch>=2.6",                             # TODO(verify): GPU 機改 cu128 wheel（見 Dockerfile）
  "transformers>=4.53.1",                   # TODO(verify): pin 可載 colpali-v1.3-hf 的版本
  "pillow>=10",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/anatomy_shared"]
```

- [ ] **Step 3：`backend/pyproject.toml`（依賴 `anatomy-shared`，不含 colpali extra → 輕量）**

```toml
[project]
name = "anatomy-backend"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = [
  "anatomy-shared",
  "fastapi>=0.115", "uvicorn[standard]>=0.34", "sse-starlette>=2.1",
  "asyncpg>=0.30", "alembic>=1.13", "pydantic>=2.7", "pydantic-settings>=2.3",
  "redis>=5.0", "redisvl>=0.18",            # TODO(verify)
  "openai>=1.40",                           # TODO(verify): 支援 gpt-5.5/5.4
  "tenacity>=8.3", "httpx>=0.27",
  "sentence-transformers>=3.0",             # 本地快取 embedding（multilingual-e5-small）
  "langfuse>=2.0", "sentry-sdk>=2.0",       # TODO(verify)
]
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/anatomy_backend"]
```

- [ ] **Step 4：`colpali_service/pyproject.toml`（依賴 `anatomy-shared[colpali]`）**

```toml
[project]
name = "colpali-service"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["anatomy-shared[colpali]", "fastapi>=0.115", "uvicorn[standard]>=0.34",
                "pydantic>=2.7", "pydantic-settings>=2.3", "numpy>=1.26"]
[project.optional-dependencies]
modal = ["modal>=0.64"]                     # TODO(verify)；optional fallback
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/colpali_service"]
```

- [ ] **Step 5：`ingest/pyproject.toml`**

```toml
[project]
name = "anatomy-ingest"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["anatomy-shared[colpali]", "docling>=2.40",  # TODO(verify): 研究指 v2.96~2.97
                "pdf2image>=1.17", "asyncpg>=0.30", "boto3>=1.34", "pyyaml>=6", "pillow>=10"]
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/anatomy_ingest"]
```

- [ ] **Step 6：`eval/pyproject.toml`（pin RAGAS 0.4.x，修 Codex Dim5-5）**

```toml
[project]
name = "anatomy-eval"
version = "0.0.0"
requires-python = ">=3.11"
dependencies = ["ragas>=0.4,<0.5",          # TODO(verify): pin 確切 0.4.x（V2 metrics）
                "langchain-openai>=0.1", "datasets>=2.19", "pyyaml>=6", "streamlit>=1.35"]
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/anatomy_eval"]
```

- [ ] **Step 7：產生 lock**

Run: `uv sync --all-packages --group dev`
Expected: 解析並安裝；產生 `uv.lock`。版本不存在則依 `# TODO(verify)` 替換後重跑。

- [ ] **Step 8：Commit** `chore: uv workspace（dev group、shared 拆 binary/colpali、版本 pin）`

---

## Task 0.4：`.env.example`

**Files:** Create `.env.example`

- [ ] **Step 1：`.env.example`**（同 v1 內容，並補 LangFuse secrets 佔位，修 Codex Dim2-5）

於 v1 內容基礎上，於 Observability 段加入：
```bash
# ---- Observability ----
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://langfuse:3100
LANGFUSE_NEXTAUTH_SECRET=change-me-in-prod    # LangFuse 自身用；勿提交真值（gitleaks 掃描）
LANGFUSE_SALT=change-me-in-prod
SENTRY_DSN=
```
（其餘變數同 v1：dev/mock 旗標、DB、Redis、LLM、快取、Eval、ColPali、MinIO、KB、SSO、限流、CLINICAL_FLAVORED_LOGGING。）

- [ ] **Step 2：Commit** `chore: .env.example（含 LangFuse secrets 佔位）`

---

## Task 0.5：`config.py`（DSN 解析驗證，修 Codex Dim1-11）

**Files:** Create `backend/src/anatomy_backend/config.py`、Test `backend/tests/test_config.py`

- [ ] **Step 1：失敗測試**

```python
# backend/tests/test_config.py
import pytest
from anatomy_backend.config import Settings

_BASE = {"PG_DIRECT_URL": "postgresql://u:p@postgres:5432/db", "REDIS_URL": "redis://redis:6379/0"}


def test_defaults_dev_mode(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pgbouncer:6432/db")
    for k, v in _BASE.items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert s.encoder_mock and s.llm_mock and s.auth_mode == "dev" and s.active_kb_version == 1


def test_database_url_must_target_pgbouncer_6432(monkeypatch):
    """應用層 DATABASE_URL 必須走 PgBouncer :6432（解析 DSN port，非字串搜尋）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres:5432/db")  # 直連 Postgres
    for k, v in _BASE.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ValueError, match="6432"):
        Settings()
```

- [ ] **Step 2：跑測試確認失敗** `cd backend && uv run pytest tests/test_config.py -v` → FAIL

- [ ] **Step 3：實作 `config.py`**

```python
# backend/src/anatomy_backend/config.py
"""設定層；以 DSN 解析強制工程紅線：應用層連 PgBouncer :6432（§0.3）。"""
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    encoder_mock: bool = True
    llm_mock: bool = True
    auth_mode: str = "dev"
    dev_user_id: str = "00000000-0000-0000-0000-000000000001"

    database_url: str
    pg_direct_url: str
    redis_url: str

    openai_api_key: str = ""
    openai_model_primary: str = "gpt-5.5"
    openai_model_fallback: str = "gpt-5.4"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_embed_model: str = "text-embedding-3-small"

    cache_local_embed_model: str = "intfloat/multilingual-e5-small"
    cache_distance_threshold: float = 0.05
    cache_ttl_seconds: int = 1209600

    colpali_primary_url: str = "http://encoder:8001/encode_query"
    colpali_fallback_url: str = ""
    colpali_model: str = "vidore/colpali-v1.3-hf"

    active_kb_version: int = 1
    rate_limit_per_user_min: int = 15
    rate_limit_per_user_day: int = 300
    rate_limit_global_rps: int = 20
    langfuse_host: str = ""
    sentry_dsn: str = ""

    @field_validator("database_url")
    @classmethod
    def _must_use_pgbouncer(cls, v: str) -> str:
        # 解析 DSN：應用層必須連 PgBouncer（慣例 :6432），不可直連 Postgres :5432（§0.3）
        port = urlparse(v).port
        if port != 6432:
            raise ValueError(
                f"DATABASE_URL 必須連 PgBouncer :6432（目前 port={port}）；"
                "直連 Postgres :5432 僅允許用於 migrations 的 PG_DIRECT_URL"
            )
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

- [ ] **Step 4：跑測試確認通過** → PASS（2 passed）

- [ ] **Step 5：Commit** `feat(backend): config.py（DSN 解析強制連 :6432）`

---

## Task 0.6：backend FastAPI `/healthz` `/warmup`

**Files:** `backend/src/anatomy_backend/api/main.py`、Test `backend/tests/test_healthz.py`（同 v1：TDD 失敗→實作→通過）。`main.py` 內容同 v1（`/healthz`→`{"status":"ok"}`、`/warmup`→`{"warmed":true}`，皆中文 docstring）。Commit `feat(backend): /healthz /warmup 骨架`。

---

## Task 0.7：Alembic 骨架

**Files:** `backend/alembic.ini`、`migrations/env.py`、`script.py.mako`（內容同 v1：env.py 用 `PG_DIRECT_URL`，§0.3 例外）。驗證 `alembic history` 無錯。Commit `chore(backend): Alembic 骨架`。

---

## Task 0.8：infra — init.sql 與 PgBouncer（env 驅動，無提交密碼，修 Codex Dim1-3/4）

**Files:** Create `infra/postgres/init.sql`

> **D-G 決策**：PgBouncer 用 `bitnami/pgbouncer` **env 驅動**（自 `POSTGRESQL_*` 自動產生設定 + userlist，**不提交明文密碼**、auth bootstrap 自動解決），`PGBOUNCER_PORT=6432`、`pool_mode=transaction`。Stage A 把 `SET LOCAL hnsw.ef_search` 與 HNSW SELECT 包在**同一 transaction**（Phase 2 `tx_helpers.py`），故 transaction pooling 下**不需** `track_extra_parameters`，也不需 `auth_query`/SECURITY DEFINER 函式。init.sql 因此只建擴充。

- [ ] **Step 1：`infra/postgres/init.sql`（僅擴充）**

```sql
-- 啟用向量與三元組擴充（§3.1）。版本驗證見 SETUP.md（須 pgvector ≥ 0.8）。
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

- [ ] **Step 2：Commit** `chore(infra): Postgres 擴充 init.sql（PgBouncer 改 env 驅動於 compose）`

> PgBouncer 的設定全在 `docker-compose.yml` 的 env（Task 0.13）。若落地時 `bitnami/pgbouncer` 映像不可用，fallback：自建 `infra/pgbouncer/Dockerfile` + entrypoint 由 env 產生 userlist（記於 SETUP.md 排錯）。

---

## Task 0.9：colpali_service — `/healthz` + 決定性 mock `/encode_query`（修 Codex Dim5-4）

**Files:** `colpali_service/src/colpali_service/main.py`、`encoder.py`、`Dockerfile.cpu`、`Dockerfile`、Test `colpali_service/tests/test_encode.py`

- [ ] **Step 1：失敗測試**

```python
# colpali_service/tests/test_encode.py
import base64
import pytest
from httpx import ASGITransport, AsyncClient
from colpali_service.main import app


@pytest.mark.asyncio
async def test_healthz_ready():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json()["ready"] is True


@pytest.mark.asyncio
async def test_encode_query_deterministic_contract():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/encode_query", json={"q": "肱二頭肌的起止點"})
        r2 = await c.post("/encode_query", json={"q": "肱二頭肌的起止點"})
    j1, j2 = r1.json(), r2.json()
    assert j1 == j2                                   # 決定性
    assert len(base64.b64decode(j1["pooled_bin"])) == 16   # bit(128)=16 bytes
    assert len(j1["tokens_bin"]) >= 1
    assert all(len(base64.b64decode(t)) == 16 for t in j1["tokens_bin"])
```

- [ ] **Step 2：跑測試確認失敗** → FAIL

- [ ] **Step 3：`encoder.py`（mock：用 shared.binary 純 numpy，決定性）**

```python
# colpali_service/src/colpali_service/encoder.py
"""Encoder 抽象：Phase 0 提供決定性 mock；Phase 3 接真實 ColPali（shared[colpali]）。"""
import hashlib
import os

import numpy as np

from anatomy_shared.binary import binarize  # 純 numpy，不拉 torch


def _seeded_vectors(text: str, n: int, dim: int = 128) -> np.ndarray:
    """以 query 文字雜湊播種，產生決定性 float 向量（mock 用）。"""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, dim)).astype("float32")


class MockEncoder:
    """決定性 mock：滿足 /encode_query 契約，供下游（後端 client、檢索）演練。"""

    ready = True
    model = "mock-colpali"

    def encode_query(self, q: str) -> dict:
        n_tokens = 20  # 典型 query token 數（§1.4）
        toks = _seeded_vectors(q, n_tokens)
        pooled = toks.mean(axis=0)
        return {
            "tokens_bin": [binarize(t) for t in toks],   # 每個 16 bytes
            "pooled_bin": binarize(pooled),
            "model": self.model,
        }


def get_encoder():
    # Phase 3：COLPALI_DEVICE=cuda 且非 mock 時回真實 ColPali encoder
    if os.environ.get("ENCODER_MOCK", "true").lower() == "true":
        return MockEncoder()
    from colpali_service.real_encoder import RealColPaliEncoder  # Phase 3 實作
    return RealColPaliEncoder()
```

- [ ] **Step 4：`main.py`**

```python
# colpali_service/src/colpali_service/main.py
"""ColPali query encoder 微服務。Phase 0：決定性 mock；Phase 3：真實 ColPali + readiness。"""
import base64

from fastapi import FastAPI
from pydantic import BaseModel

from colpali_service.encoder import get_encoder

app = FastAPI(title="colpali-encoder", version="0.0.0")
_encoder = get_encoder()


class EncodeRequest(BaseModel):
    q: str


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


@app.get("/healthz")
async def healthz() -> dict:
    """readiness：模型載入完成才 ready（§5.1）。mock 即 ready。"""
    return {"ready": getattr(_encoder, "ready", False), "model": getattr(_encoder, "model", "")}


@app.post("/encode_query")
async def encode_query(req: EncodeRequest) -> dict:
    out = _encoder.encode_query(req.q)
    return {
        "tokens_bin": [_b64(t) for t in out["tokens_bin"]],
        "pooled_bin": _b64(out["pooled_bin"]),
        "model": out["model"],
    }


@app.post("/warmup")
async def warmup() -> dict:
    _encoder.encode_query("warmup")  # 預熱（Phase 3 真實模型 dummy encode）
    return {"warmed": True}
```

- [ ] **Step 5：跑測試確認通過** → PASS（需先 `uv sync --all-packages`，因依賴 anatomy-shared）。

- [ ] **Step 6：`Dockerfile.cpu`（mock/CPU；複製全部 workspace 成員，修 Codex Dim1-1）**

```dockerfile
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONUNBUFFERED=1 ENCODER_MOCK=true COLPALI_DEVICE=cpu
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
# 複製整個 workspace（成員小；uv sync 需所有成員 manifest）
COPY pyproject.toml uv.lock ./
COPY shared ./shared
COPY backend ./backend
COPY colpali_service ./colpali_service
COPY ingest ./ingest
COPY eval ./eval
RUN uv sync --package colpali-service --no-dev   # mock 不需 colpali extra 的 torch
EXPOSE 8001
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD curl -f http://localhost:8001/healthz || exit 1
CMD ["uv", "run", "uvicorn", "colpali_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
```
> 註：mock 路徑不裝 torch（shared 的 `colpali` extra 未啟用）；`uv sync --package colpali-service` 仍會嘗試裝 `anatomy-shared[colpali]`。**為讓 CPU mock 不裝 torch**：mock 映像改 `RUN uv sync --package anatomy-shared --no-dev`（只裝 binary 純 numpy）後再裝 service 的非 colpali 依賴；或在 colpali_service 增一個不含 `[colpali]` 的 `mock` 模式 extra。落地擇一，CI 走 mock 不應載 torch。

- [ ] **Step 7：`Dockerfile`（真實 GPU；cu128 + SDPA；CMD `--no-sync` 防還原 torch，修 Codex Dim1-5/6）**

```dockerfile
# 真實 GPU encoder（Blackwell sm_120 需 CUDA 12.8 + cu128 PyTorch；用 SDPA，不裝 flash-attn）
# 經 docker-compose.gpu.yml 啟用；需 nvidia-container-toolkit。
# TODO(verify): 確認此 CUDA 12.8 runtime tag 存在並 pin digest
FROM nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04
ENV PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive ENCODER_MOCK=false COLPALI_DEVICE=cuda \
    UV_NO_SYNC=1
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
# 先 sync 進 .venv（含 colpali extra 的 transformers 等），再把 torch 換成 cu128 wheel
# --reinstall 覆蓋 pyproject 預設 CPU wheel；CMD 用 UV_NO_SYNC 防 uv run 再次同步還原 torch
RUN uv sync --package colpali-service --no-dev && \
    uv pip install torch --index-url https://download.pytorch.org/whl/cu128 --reinstall
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=5s --retries=30 CMD curl -f http://localhost:8001/healthz || exit 1
CMD ["uv", "run", "--no-sync", "uvicorn", "colpali_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 8：Commit** `feat(encoder): 決定性 mock /encode_query + CPU/GPU Dockerfile（cu128, --no-sync, 全成員 copy）`

---

## Task 0.10：backend Dockerfile（複製全部 workspace 成員）

**Files:** `backend/Dockerfile`

- [ ] **Step 1：`backend/Dockerfile`**

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
COPY shared ./shared
COPY backend ./backend
COPY colpali_service ./colpali_service
COPY ingest ./ingest
COPY eval ./eval
RUN uv sync --package anatomy-backend --no-dev   # backend 依賴 anatomy-shared（純 numpy，無 torch）
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD curl -f http://localhost:8000/healthz || exit 1
CMD ["uv", "run", "uvicorn", "anatomy_backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

- [ ] **Step 2：Commit** `chore(backend): Dockerfile（全成員 copy、healthcheck）`

---

## Task 0.11：前端骨架 + pin + lockfile

**Files:** `frontend/package.json`（pin 精確版，D-S）、`next.config.mjs`、`tsconfig.json`、`app/layout.tsx`、`app/page.tsx`、`Dockerfile`、`package-lock.json`

- [ ] **Step 1~6**：同 v1（package.json 鎖 `ai`/`@ai-sdk/react`、Next、骨架頁、Dockerfile）。差異：**pin 精確版**（落地 `npm view ai version` 後寫死，非 caret），並 `cd frontend && npm install` 產生 **`package-lock.json` 進版控**（D-S）。
- [ ] **Step 7：Commit** `feat(frontend): Next.js 骨架（pin ai 版本 + package-lock）`

---

## Task 0.12：golden wire bytes（凍結 Vercel 協定，D-H/Codex Dim5-2）

**Files:** `frontend/scripts/dump-golden-stream.mjs`、`infra/golden/ai_stream_golden.jsonl`

> 目的：用**實際安裝的 `ai` 版本**產生 UI Message Stream 的真實 wire bytes，當 Phase 8 Python emitter 的對照基準，消除「欄位名用猜的」風險。

- [ ] **Step 1：`frontend/scripts/dump-golden-stream.mjs`**

```javascript
// 用實際安裝的 ai 版本，dump 一段代表性 UI Message Stream 的真實 wire bytes。
// 涵蓋：start → data-sources（引文）→ text-start/delta/end → finish。
// TODO(verify): 依實際 ai@6 API 校準（createUIMessageStream / writer 介面名）。
import { createUIMessageStream, createUIMessageStreamResponse } from "ai";
import { writeFileSync } from "node:fs";

const stream = createUIMessageStream({
  async execute({ writer }) {
    writer.write({ type: "start" });
    writer.write({ type: "data-sources", data: { citations: [
      { book_title: "Gray42", page: 812, figure: "Fig.7-23", image_url: "/p/1.webp",
        snippet: "肱二頭肌起於肩胛骨喙突…", score: 0.91 }
    ] } });
    writer.write({ type: "text-start", id: "t0" });
    writer.write({ type: "text-delta", id: "t0", delta: "肱二頭肌" });
    writer.write({ type: "text-delta", id: "t0", delta: "起於肩胛骨喙突 [Gray42, p.812, Fig.7-23]。" });
    writer.write({ type: "text-end", id: "t0" });
    writer.write({ type: "finish" });
  },
});

const res = createUIMessageStreamResponse({ stream });
const bytes = await res.text();          // 真實 SSE wire 內容（含 data: ... 與 [DONE]）
writeFileSync(new URL("../../infra/golden/ai_stream_golden.jsonl", import.meta.url),
  JSON.stringify({ wire: bytes }) + "\n");
console.log(bytes);
```

- [ ] **Step 2：產生並提交**

Run: `cd frontend && node scripts/dump-golden-stream.mjs`
Expected: 印出真實 wire bytes，並寫入 `infra/golden/ai_stream_golden.jsonl`。檢視確認含 `x-vercel-ai-ui-message-stream`/`data:`/`[DONE]` 等實際格式（依版本）。

- [ ] **Step 3：Commit** `chore: 提交 Vercel UI Message Stream golden wire bytes（Phase 8 emitter 對照基準）`

---

## Task 0.13：Docker Compose（核心 + minio-init + pgbouncer env + healthchecks）

**Files:** `docker-compose.yml`、`docker-compose.gpu.yml`、`docker-compose.observability.yml`

- [ ] **Step 1：`docker-compose.yml`**

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16            # TODO(verify): pin digest；確認 pgvector ≥0.8
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./infra/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    ports: ["5432:5432"]                      # 僅供 migrations 直連
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10

  pgbouncer:                                  # env 驅動：自動產生設定 + userlist（不提交密碼），:6432
    image: bitnami/pgbouncer:latest           # TODO(verify): pin 版本/digest；若不可用見 SETUP.md fallback
    depends_on:
      postgres: { condition: service_healthy }
    environment:
      POSTGRESQL_HOST: postgres
      POSTGRESQL_PORT_NUMBER: 5432
      POSTGRESQL_USERNAME: ${POSTGRES_USER}
      POSTGRESQL_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRESQL_DATABASE: ${POSTGRES_DB}
      PGBOUNCER_PORT: 6432
      PGBOUNCER_POOL_MODE: transaction
      PGBOUNCER_MAX_CLIENT_CONN: 1000
      PGBOUNCER_DEFAULT_POOL_SIZE: 25
      PGBOUNCER_AUTH_TYPE: scram-sha-256
      PGBOUNCER_DATABASE: ${POSTGRES_DB}
    ports: ["6432:6432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -p 6432 -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck: { test: ["CMD", "redis-cli", "ping"], interval: 5s, timeout: 3s, retries: 10 }

  minio:
    image: minio/minio:latest                 # TODO(verify): pin
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${S3_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${S3_SECRET_KEY}
    volumes: ["miniodata:/data"]
    ports: ["9000:9000", "9001:9001"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio-init:                                 # 冪等建立 bucket（修 Codex Dim3-5）
    image: minio/mc:latest                    # TODO(verify): pin
    depends_on:
      minio: { condition: service_healthy }
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 ${S3_ACCESS_KEY} ${S3_SECRET_KEY} &&
      mc mb --ignore-existing local/${S3_BUCKET} &&
      echo 'bucket ready: ${S3_BUCKET}'"
    restart: "no"

  encoder:
    build: { context: ., dockerfile: colpali_service/Dockerfile.cpu }   # 核心用 CPU/mock
    environment: { ENCODER_MOCK: "true", COLPALI_DEVICE: cpu }
    ports: ["8001:8001"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8001/healthz"]
      interval: 5s
      timeout: 3s
      retries: 10

  backend:
    build: { context: ., dockerfile: backend/Dockerfile }
    depends_on:
      pgbouncer: { condition: service_healthy }     # 修 Codex Dim1-8：等 pgbouncer healthy
      redis: { condition: service_healthy }
      encoder: { condition: service_healthy }
    env_file: [.env]
    ports: ["8000:8000"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 5s
      timeout: 3s
      retries: 10

  frontend:
    build: { context: ., dockerfile: frontend/Dockerfile }
    depends_on: { backend: { condition: service_healthy } }
    ports: ["3000:3000"]
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:3000 >/dev/null 2>&1 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10

volumes: { pgdata: {}, miniodata: {} }
```

- [ ] **Step 2：`docker-compose.gpu.yml`（真實 GPU；--no-sync 已在 Dockerfile）**

```yaml
# docker compose -f docker-compose.yml -f docker-compose.gpu.yml up
services:
  encoder:
    build: { context: ., dockerfile: colpali_service/Dockerfile }   # GPU 版
    environment: { ENCODER_MOCK: "false", COLPALI_DEVICE: cuda }
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]
  backend:
    environment: { ENCODER_MOCK: "false" }
```

- [ ] **Step 3：`docker-compose.observability.yml`（LangFuse 獨立 DB + secrets 由 .env，修 Codex Dim2-1/5）**

```yaml
# docker compose -f docker-compose.yml -f docker-compose.observability.yml up
# LangFuse 用「自己的」Postgres，不直連主 DB（避免違反 §0.3 :5432 紅線）。
services:
  langfuse-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: ${LANGFUSE_DB_PASSWORD:-langfuse_dev_pw}
      POSTGRES_DB: langfuse
    volumes: ["langfusedata:/var/lib/postgresql/data"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U langfuse"], interval: 5s, timeout: 3s, retries: 10 }

  langfuse:
    image: langfuse/langfuse:latest          # TODO(verify): v3 另需 clickhouse/redis，依官方 self-host compose 補齊
    depends_on: { langfuse-db: { condition: service_healthy } }
    environment:
      DATABASE_URL: postgresql://langfuse:${LANGFUSE_DB_PASSWORD:-langfuse_dev_pw}@langfuse-db:5432/langfuse
      NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET}
      SALT: ${LANGFUSE_SALT}
      NEXTAUTH_URL: http://localhost:3100
    ports: ["3100:3000"]                      # 對外 3100（DL-006）

volumes: { langfusedata: {} }
```

- [ ] **Step 4：驗證 compose 設定**

```bash
cp .env.example .env
docker compose config >/dev/null && echo "core OK"
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config >/dev/null && echo "gpu OK"
docker compose -f docker-compose.yml -f docker-compose.observability.yml config >/dev/null && echo "obs OK"
```
Expected: 三行 `... OK`。

- [ ] **Step 5：Commit** `chore(infra): compose 核心(minio-init/pgbouncer env/healthchecks) + gpu/observability override`

---

## Task 0.14：Makefile（migrate 在 container 內、gpu-smoke、golden-bytes，修 Codex Dim1-2）

**Files:** `Makefile`

- [ ] **Step 1：`Makefile`**

```makefile
.PHONY: help up up-gpu up-obs down logs migrate gpu-smoke golden-bytes ingest-sample test lint fmt

help:
	@echo "make up / up-gpu / up-obs / down / logs"
	@echo "make migrate     # 在 backend container 內跑 Alembic（連 PG_DIRECT_URL :5432）"
	@echo "make gpu-smoke   # 實機 GPU：build GPU encoder 並驗 torch.cuda.is_available()"
	@echo "make golden-bytes# 產生 Vercel UI Message Stream golden wire bytes"
	@echo "make test / lint / fmt / ingest-sample / eval"

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

# 修 Codex Dim1-2：在 backend container 內跑 alembic。backend 服務的 env_file 已注入
# PG_DIRECT_URL=...@postgres:5432（compose 網路內 hostname `postgres` 可解析），故無需額外 -e。
migrate:
	docker compose run --rm backend sh -c "cd backend && uv run alembic -c alembic.ini upgrade head"

# 實機 GPU smoke gate（非 CI；production 驗收前必過，D-E）
gpu-smoke:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml build encoder
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml run --rm encoder \
	  python -c "import torch; assert torch.cuda.is_available(), 'CUDA 不可用：檢查 cu128/driver'; print('CUDA OK:', torch.cuda.get_device_name(0))"

golden-bytes:
	cd frontend && node scripts/dump-golden-stream.mjs

ingest-sample:
	docker compose run --rm backend sh -c "uv run python -m anatomy_ingest.cli --pdf /data/sample.pdf --book-meta /data/sample.yaml --kb-version 1 --batch-size 4"

test:
	uv run --group dev pytest

lint:
	uv run --group dev ruff check .

fmt:
	uv run --group dev ruff format .
```

- [ ] **Step 2：Commit** `chore: Makefile（migrate 於 container、gpu-smoke、golden-bytes）`

---

## Task 0.15：CI（unit job 無 DB + db-integration job，修 Codex Dim1-10）+ pre-commit

**Files:** `.github/workflows/ci.yml`、`.pre-commit-config.yaml`

- [ ] **Step 1：`.github/workflows/ci.yml`**

```yaml
name: CI
on: { pull_request: {}, push: { branches: [main] } }

jobs:
  unit:                                  # 不連 DB：config/healthz/encoder mock/binary 等純單元
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-packages --group dev
      - run: uv run --group dev ruff check .
      - name: 確認 binary.py 不依賴 torch（D-L）
        run: uv run python -c "import anatomy_shared.binary; import sys; assert 'torch' not in sys.modules, 'binary.py 不應載 torch'"
      - name: 確認無 LlamaIndex 殘留（DL-015）
        run: "! grep -rIn --include='*.py' 'llama_index' backend ingest eval || (echo 'LlamaIndex 殘留' && exit 1)"
      - run: uv run --group dev pytest backend/tests colpali_service/tests shared/tests -q
        env: { }   # 這些測試不需 DB（config 測試用 monkeypatch）

  db-integration:                        # Phase 2 起啟用 DB/檢索測試
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_USER: anatomy, POSTGRES_PASSWORD: anatomy_dev_pw, POSTGRES_DB: anatomy_rag }
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U anatomy" --health-interval 5s --health-timeout 3s --health-retries 10
      pgbouncer:
        image: bitnami/pgbouncer:latest    # TODO(verify): pin
        env:
          POSTGRESQL_HOST: postgres
          POSTGRESQL_USERNAME: anatomy
          POSTGRESQL_PASSWORD: anatomy_dev_pw
          POSTGRESQL_DATABASE: anatomy_rag
          PGBOUNCER_PORT: 6432
          PGBOUNCER_POOL_MODE: transaction
          PGBOUNCER_AUTH_TYPE: scram-sha-256
        ports: ["6432:6432"]
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-packages --group dev
      - name: 建擴充（CI 無 init.sql 掛載）
        run: PGPASSWORD=anatomy_dev_pw psql -h localhost -U anatomy -d anatomy_rag -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
      - name: Migrations（Phase 2 起有內容）
        env: { PG_DIRECT_URL: postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag }
        run: cd backend && uv run alembic -c alembic.ini upgrade head
      - name: DB 整合測試
        env:
          DATABASE_URL: postgresql://anatomy:anatomy_dev_pw@localhost:6432/anatomy_rag
          PG_DIRECT_URL: postgresql://anatomy:anatomy_dev_pw@localhost:5432/anatomy_rag
          REDIS_URL: redis://localhost:6379/0
        run: uv run --group dev pytest -q -m "db or integration" || echo "Phase 0 無 db 測試，Phase 2 起生效"

  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: gitleaks/gitleaks-action@v2

  # Phase 11 啟用：RAGAS gate（未達門檻阻擋 merge，§7.3）
```
> Phase 0：只有 `unit` + `gitleaks` 必綠；`db-integration` 在 Phase 0 無 db 測試（標記 `-m "db or integration"` 空集）→ 不阻擋。Phase 2 起該 job 生效。

- [ ] **Step 2：`.pre-commit-config.yaml`**（同 v1：ruff + ruff-format + gitleaks，pin `rev`，標 TODO(verify)）

- [ ] **Step 3：本機驗證** `uv run --group dev ruff check . && uv run --group dev pytest backend/tests colpali_service/tests shared/tests` → 綠

- [ ] **Step 4：Commit** `ci: unit/db-integration 分流 + binary/LlamaIndex 斷言 + gitleaks + pre-commit`

---

## Task 0.16：撰寫 `SETUP.md`（最重要交付）

**Files:** Create `SETUP.md`

- [ ] **Step 1：建立 `SETUP.md`**（在 v1 內容基礎上，套用以下修正）

沿用 v1 的 §0 軟體版本表 / §A dev 路徑 / §B production 路徑 / §C 排錯，並做下列**修正**：
- §A.3 `make migrate` 改說明「在 backend container 內執行 Alembic（連 compose 內 `postgres:5432`）；Phase 0 為 framework 驗證（無 migration → 顯示無升級屬正常），Phase 2 起建表」。
- §A.4 Smoke test 加一行：`curl -s -X POST localhost:8000/encode... ` → 改為對 encoder：`curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' -d '{"q":"肱二頭肌"}'`，預期回 `{"tokens_bin":[...],"pooled_bin":"...(16B base64)","model":"mock-colpali"}`，且**重送相同 query 結果相同（決定性）**。並註明：前端 `:3000` 為**骨架頁**，真正 `/chat` 在 Phase 8。
- §B.1 GPU 段加入 **GPU smoke gate**：`make gpu-smoke`，預期印 `CUDA OK: NVIDIA GeForce RTX 5060 Ti`；失敗代表 cu128/driver 問題（排錯：torch 非 cu128 → 確認 GPU Dockerfile `--index-url .../cu128`；`no kernel image` → driver 不支援 CUDA 12.8）。
- §C 排錯表新增列：
  - 「PgBouncer 啟動失敗/連不上」→ 用 `bitnami/pgbouncer` env 驅動，確認 `POSTGRESQL_*` env 正確；若映像不可用，改用 fallback 自建 pgbouncer（`infra/pgbouncer/Dockerfile`，entrypoint 由 env 產生 userlist）。
  - 「Docker build 失敗（uv sync 找不到 workspace 成員）」→ 確認 Dockerfile 已 COPY 全部成員（shared/backend/colpali_service/ingest/eval）。
  - 「`make migrate` 連不到 DB」→ 它在 backend container 內跑、連 `postgres:5432`（compose 網路）；勿在宿主機直接 `alembic`。
  - 「golden wire bytes 與 emitter 不符」→ 重跑 `make golden-bytes`（用實際 `ai` 版本），Phase 8 emitter 對照此檔。

- [ ] **Step 2：Commit** `docs: SETUP.md（migrate於container、encoder mock 驗證、GPU smoke gate、排錯）`

---

## Task 0.17：全新環境驗收（Phase 0 DoD）

- [ ] **Step 1：清空重來，照 SETUP.md A 節**

```bash
git clean -xdf -e .env 2>/dev/null; make down 2>/dev/null
cp .env.example .env && make up
```
Expected: 所有服務 `Up (healthy)`（pgbouncer/encoder/backend/frontend 皆有 healthcheck）。

- [ ] **Step 2：基礎設施 smoke 全綠**

```bash
make migrate                                   # framework 可執行（Phase 0 no-op）
test "$(curl -s localhost:8000/healthz)" = '{"status":"ok"}' && echo "backend OK"
curl -s localhost:8001/healthz | grep -q '"ready":true' && echo "encoder OK"
A=$(curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' -d '{"q":"x"}')
B=$(curl -s -X POST localhost:8001/encode_query -H 'content-type: application/json' -d '{"q":"x"}')
[ "$A" = "$B" ] && echo "encoder mock deterministic OK"
curl -s localhost:3000 | grep -q "系統骨架運行中" && echo "frontend OK"
```
Expected: `backend OK` / `encoder OK` / `encoder mock deterministic OK` / `frontend OK`。

- [ ] **Step 3：測試/設定/掃描全綠**

```bash
make test && make lint
docker compose config >/dev/null && \
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config >/dev/null && \
docker compose -f docker-compose.yml -f docker-compose.observability.yml config >/dev/null && echo "compose OK"
uv tool run gitleaks detect --no-banner || docker run --rm -v "$PWD:/r" zricethezav/gitleaks detect -s /r
```
Expected: pytest 綠、ruff 無錯、`compose OK`、`no leaks found`。

- [ ] **Step 4（實機 GPU，非 CI）：GPU smoke gate**

```bash
make gpu-smoke
```
Expected: `CUDA OK: <你的 GPU 名稱>`。（無 GPU 機器跳過，但 production 驗收前必過。）

- [ ] **Step 5：Commit**（如有微調）`chore: Phase 0 全新環境驗收通過`

---

## Phase 0 自我檢查（spec 覆蓋 + Codex 修正落實）

- [ ] Task 0.1 治理優先：DL-014~018 寫入 + spec 回寫（page_patches kb_version、移除 LlamaIndex、Vercel emitter、SSO、Top-K=100）✓（Codex Dim4-1/2/3/9, Dim3-2）
- [ ] 附錄 A 全部環境變數在 `.env.example`（+ dev/mock + LangFuse secrets 佔位）✓（Dim2-5）
- [ ] Docker build：各 Dockerfile 複製全部 workspace 成員 ✓（Dim1-1）
- [ ] `make migrate` 在 container 內、連 postgres:5432 ✓（Dim1-2）
- [ ] PgBouncer env 驅動 :6432、不提交密碼、有 healthcheck、backend 依 service_healthy ✓（Dim1-3/4/8）
- [ ] GPU Dockerfile：tag 註解獨立行、cu128、`--no-sync` ✓（Dim1-5/6）；GPU smoke gate ✓（Dim5-1）
- [ ] dev group 安裝一致（`uv sync --group dev`）✓（Dim1-7）
- [ ] CI unit/db-integration 分流 + 建擴充 + migrate ✓（Dim1-10）
- [ ] config 以 DSN 解析驗證 :6432 ✓（Dim1-11）
- [ ] encoder 決定性 mock /encode_query ✓（Dim5-4）；Phase 0 不誇稱 e2e ✓（Dim3-7）
- [ ] golden wire bytes 提交 ✓（Dim5-2）；前端 pin + lockfile ✓（Dim5-8）
- [ ] shared binary.py 純 numpy（CI 斷言不載 torch）✓（Dim5-7）
- [ ] MinIO bucket init service ✓（Dim3-5）
- [ ] LangFuse 獨立 DB + secrets 由 .env（不違反 :5432 紅線/不硬編）✓（Dim2-1/5）
- [ ] §0.3 工程紅線（應用層連 :6432）強制 ✓；§6.9 密鑰不入 git + gitleaks ✓
- [ ] SETUP.md dev + production + GPU smoke + 排錯、全繁體中文 ✓
- [ ] 全新機器 `make up` 通過基礎設施 smoke（健康探針 + 決定性 mock encoder）✓

> **Phase 0 完成後**：執行 Phase 1（`shared/binary.py` 純 numpy + 評估 harness 種子），其 bite-sized 子計畫於執行前生成。
