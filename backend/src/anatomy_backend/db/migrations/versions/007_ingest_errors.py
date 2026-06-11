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
            kb_version  INTEGER NOT NULL CHECK (kb_version >= 1),
            book_id     UUID REFERENCES books(book_id),
            page_num    INTEGER CHECK (page_num IS NULL OR page_num >= 1),
            stage       TEXT NOT NULL                    -- parse|render|encode|upload|write
                         CHECK (stage IN ('parse', 'render', 'encode', 'upload', 'write')),
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
