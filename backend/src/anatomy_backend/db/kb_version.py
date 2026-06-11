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
