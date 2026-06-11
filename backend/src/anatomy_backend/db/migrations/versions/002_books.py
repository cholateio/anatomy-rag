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
