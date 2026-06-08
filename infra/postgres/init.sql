-- Postgres 初始化：啟用向量與三元組擴充（ARCHITECTURE.md §3.1）。
-- 由 docker-compose 掛載到 /docker-entrypoint-initdb.d/，於資料庫首次建立時自動執行。
-- 版本要求：pgvector ≥ 0.8（bit(128) + HNSW bit_hamming_ops）；驗證方式見 SETUP.md。
-- PgBouncer 設定不在此檔——改由 docker-compose 以 env 驅動（bitnami/pgbouncer），不提交密碼。
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
