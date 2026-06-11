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
            cache_hit       BOOLEAN NOT NULL DEFAULT FALSE,
            model_used      TEXT,
            tool_used       JSONB NOT NULL DEFAULT '[]',
            tokens_in       INTEGER CHECK (tokens_in IS NULL OR tokens_in >= 0),
            tokens_out      INTEGER CHECK (tokens_out IS NULL OR tokens_out >= 0),
            cost_usd        NUMERIC(12, 6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
            ip              INET,
            country         TEXT CHECK (country IS NULL OR country ~ '^[A-Z]{2}$'),
            user_agent      TEXT,                      -- 應用層截斷 ≤512
            clinical_flavored BOOLEAN NOT NULL DEFAULT FALSE,  -- §6.7 MAY，預設關閉
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE query_logs")
