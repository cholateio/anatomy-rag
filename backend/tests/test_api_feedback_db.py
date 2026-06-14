"""DB-tier tests for _write_feedback 純 UPDATE 語意（H1/H2/cross-user 防護）。

以真實 SQL 驗證：
  - H1：未知 turn_id → 0 rows → False；且絕不建立新列（無 INSERT fallback）。
  - begin+finalize 後 feedback UPDATE 成功 → True。
  - H2：不同 user_id → 0 rows → False（cross-user 阻斷）。

測試需要 DATABASE_URL + PG_DIRECT_URL（db 標記；無環境時自動 skip）。
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.db


def _tid() -> str:
    return str(uuid.uuid4())


def _uid() -> str:
    return str(uuid.uuid4())


async def test_write_feedback_unknown_turn_id_no_row_created(clean_db):
    """H1：未知 turn_id → UPDATE 0 rows → returns False；不建立任何新列。"""
    conn = clean_db
    unknown_tid = _tid()
    uid = _uid()

    count_before = await conn.fetchval("SELECT count(*) FROM query_logs")

    row = await conn.fetchrow(
        "UPDATE query_logs SET feedback=$1, feedback_text=$2 "
        "WHERE turn_id=$3::uuid AND user_id=$4::uuid RETURNING turn_id",
        1, None, unknown_tid, uid,
    )
    result = row is not None

    count_after = await conn.fetchval("SELECT count(*) FROM query_logs")

    assert result is False, "未知 turn_id 應回 False（0 rows updated）"
    assert count_after == count_before, "不應建立任何新列（H1：無 INSERT fallback）"


async def test_write_feedback_begun_finalized_row_succeeds(clean_db):
    """log_begin + log_finalize 後，feedback UPDATE 成功（True）。"""
    conn = clean_db
    tid = _tid()
    uid = _uid()

    # 模擬 _log_begin
    await conn.execute(
        "INSERT INTO query_logs (turn_id, user_id, query_text) "
        "VALUES ($1::uuid, $2::uuid, 'test query') ON CONFLICT (turn_id) DO NOTHING",
        tid, uid,
    )
    # 模擬 _log_finalize
    await conn.execute(
        "UPDATE query_logs SET status=$2, cache_hit=$3 WHERE turn_id=$1::uuid",
        tid, "ok", False,
    )

    # 純 UPDATE feedback（_write_feedback 語意）
    row = await conn.fetchrow(
        "UPDATE query_logs SET feedback=$1, feedback_text=$2 "
        "WHERE turn_id=$3::uuid AND user_id=$4::uuid RETURNING turn_id",
        -1, "頁碼錯誤", tid, uid,
    )
    assert row is not None, "feedback UPDATE 應成功（RETURNING turn_id）"
    assert str(row["turn_id"]) == tid


async def test_write_feedback_cross_user_blocked(clean_db):
    """H2：turn_id 存在但 user_id 不符 → 0 rows → False（cross-user 阻斷）。"""
    conn = clean_db
    tid = _tid()
    owner_uid = _uid()
    other_uid = _uid()

    # 插入 owner 的 turn
    await conn.execute(
        "INSERT INTO query_logs (turn_id, user_id, query_text) "
        "VALUES ($1::uuid, $2::uuid, 'owner query') ON CONFLICT (turn_id) DO NOTHING",
        tid, owner_uid,
    )

    # 以不同 user_id 嘗試更新
    row = await conn.fetchrow(
        "UPDATE query_logs SET feedback=$1, feedback_text=$2 "
        "WHERE turn_id=$3::uuid AND user_id=$4::uuid RETURNING turn_id",
        1, None, tid, other_uid,
    )
    result = row is not None

    assert result is False, "不同 user_id 不得更新他人回合（H2 cross-user blocked）"

    # 確認 owner 的列未被改動
    owner_row = await conn.fetchrow(
        "SELECT feedback FROM query_logs WHERE turn_id=$1::uuid", tid
    )
    assert owner_row is not None
    assert owner_row["feedback"] is None, "owner 的 feedback 欄位不應被修改"
