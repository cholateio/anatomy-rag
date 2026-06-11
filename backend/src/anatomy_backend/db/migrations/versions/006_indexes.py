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
