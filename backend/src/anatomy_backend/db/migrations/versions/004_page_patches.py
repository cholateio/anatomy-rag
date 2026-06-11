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
