"""pool 連線參數工廠（無 DB）：statement_cache_size=0 是 PgBouncer transaction pooling 紅線。"""
from anatomy_backend.config import Settings
from anatomy_backend.db.pool import build_pool_kwargs


def _settings(**over):
    base = dict(
        database_url="postgresql://u:p@pgbouncer:6432/anatomy_rag",
        pg_direct_url="postgresql://u:p@postgres:5432/anatomy_rag",
        redis_url="redis://redis:6379/0",
    )
    base.update(over)
    return Settings(_env_file=None, **base)


def test_build_pool_kwargs_enforces_redlines():
    kw = build_pool_kwargs(_settings())
    assert kw["dsn"] == "postgresql://u:p@pgbouncer:6432/anatomy_rag"
    assert kw["statement_cache_size"] == 0          # 禁 prepared statements（§3.4）
    assert kw["min_size"] == 2 and kw["max_size"] == 10


def test_build_pool_kwargs_sizes_configurable():
    kw = build_pool_kwargs(_settings(db_pool_min_size=1, db_pool_max_size=25))
    assert kw["min_size"] == 1 and kw["max_size"] == 25
