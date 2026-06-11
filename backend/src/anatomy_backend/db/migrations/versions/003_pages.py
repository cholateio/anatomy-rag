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
