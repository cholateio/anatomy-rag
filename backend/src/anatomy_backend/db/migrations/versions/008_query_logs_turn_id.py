"""008: query_logs 加 turn_id（per-turn 回饋粒度，DL-027）。

turn_id = app 端每回合產生的 UUID（= AI SDK start.messageId = 前端 message.id）。
/feedback 以 turn_id 精準更新單列。nullable 向後相容；UNIQUE 索引允許多個 NULL。
"""
from alembic import op

revision = "008_query_logs_turn_id"
down_revision = "007_ingest_errors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE query_logs ADD COLUMN turn_id UUID")
    op.execute("CREATE UNIQUE INDEX uq_query_logs_turn_id ON query_logs (turn_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_query_logs_turn_id")
    op.execute("ALTER TABLE query_logs DROP COLUMN turn_id")
