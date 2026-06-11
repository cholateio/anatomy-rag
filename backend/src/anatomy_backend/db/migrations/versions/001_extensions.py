"""001: PostgreSQL 擴充——vector(pgvector) + pg_trgm（§3.1）。

本 migration 擁有 extensions（CI 由此建立）；本機 compose 的 infra/postgres/init.sql
亦可能先建（IF NOT EXISTS 容忍）。
downgrade 會 DROP——dev 可逆性測試 OK；生產環境不應 downgrade 001
（extensions 可能被其他物件依賴，PostgreSQL 會擋）。
"""
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
