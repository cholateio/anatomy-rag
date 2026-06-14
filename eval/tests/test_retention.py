"""retention.py 測試（UTC robust；≥90d；壞檔名 skip）。"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from anatomy_eval.retention import prune_old, save_report


def _write_report_with_ts(directory: Path, ts: datetime, content: dict | None = None) -> Path:
    """在 directory 寫入一個帶指定時戳的假報告檔案。"""
    suffix = ts.strftime("%Y%m%dT%H%M%S%f") + "Z"
    fname = directory / f"eval_report_{suffix}.json"
    fname.write_text(json.dumps(content or {"score": 0.9}), encoding="utf-8")
    return fname


def test_save_report_creates_file(tmp_path):
    report = {"faithfulness": 0.92, "answer_relevancy": 0.88}
    p = save_report(report, tmp_path)
    assert p.exists()
    loaded = json.loads(p.read_text())
    assert loaded == report


def test_save_report_filename_utc_microseconds(tmp_path):
    """檔名格式：eval_report_<UTCYYYYmmddTHHMMSSffffff>Z.json"""
    p = save_report({"x": 1}, tmp_path)
    name = p.name
    assert name.startswith("eval_report_")
    assert name.endswith("Z.json")
    # 確認時戳部分可解析為 UTC datetime（含微秒）
    ts_str = name.removeprefix("eval_report_").removesuffix("Z.json")
    dt = datetime.strptime(ts_str, "%Y%m%dT%H%M%S%f")
    assert dt is not None  # 解析成功


def test_save_report_no_collision(tmp_path):
    """同一秒連續寫兩份報告檔名不得相同（微秒保護）。"""
    p1 = save_report({"a": 1}, tmp_path)
    p2 = save_report({"b": 2}, tmp_path)
    assert p1 != p2


def test_prune_old_deletes_old_keeps_new(tmp_path):
    now = datetime.now(tz=timezone.utc)  # noqa: UP017
    old_ts = now - timedelta(days=95)
    new_ts = now - timedelta(days=30)
    old_file = _write_report_with_ts(tmp_path, old_ts)
    new_file = _write_report_with_ts(tmp_path, new_ts)
    deleted = prune_old(tmp_path, retain_days=90)
    assert not old_file.exists(), ">90d 的舊檔應被刪除"
    assert new_file.exists(), "<90d 的新檔應保留"
    assert old_file in deleted


def test_prune_old_keeps_exactly_90d(tmp_path):
    """89 天 23 小時前的檔案（在 90d 保留窗內）不應被刪。

    注：無法用 exactly now-90d 測試（prune_old 本身的 now 略後於 test 的 now 會造成 race）；
    用「比 90d 少 1 小時」確保穩定地落在保留區。
    """
    now = datetime.now(tz=timezone.utc)  # noqa: UP017
    # 89d 23h 前 = clearly within retain window even if prune_old runs 1s later
    boundary_ts = now - timedelta(days=90) + timedelta(hours=1)
    f = _write_report_with_ts(tmp_path, boundary_ts)
    prune_old(tmp_path, retain_days=90)
    assert f.exists()


def test_prune_old_bad_filename_skip_no_crash(tmp_path, caplog):
    """壞檔名（無法解析時戳）→ logging.warning + skip，不中止整批。

    壞檔名格式：須符合 eval_report_<...>Z.json 前後綴，但時戳部分不可解析，
    才會進入 strptime 路徑並觸發 warning（前後綴不符的檔案靜默略過）。
    """
    # 前後綴符合但時戳不可解析 → 觸發 warning
    bad = tmp_path / "eval_report_NOT_A_TIMESTAMPZ.json"
    bad.write_text("{}", encoding="utf-8")
    now = datetime.now(tz=timezone.utc)  # noqa: UP017
    old_ts = now - timedelta(days=100)
    old_file = _write_report_with_ts(tmp_path, old_ts)
    with caplog.at_level(logging.WARNING, logger="anatomy_eval.retention"):
        prune_old(tmp_path, retain_days=90)
    assert bad.exists(), "壞檔名不應被刪除"
    assert not old_file.exists(), "合法舊檔應被刪除"
    # 使用 caplog.messages（呼叫 getMessage()）確保 lazy % 格式化已展開
    assert any(
        "NOT_A_TIMESTAMP" in msg or "eval_report_NOT_A_TIMESTAMP" in msg
        for msg in caplog.messages
    )


def test_prune_old_raises_on_retain_days_under_90(tmp_path):
    with pytest.raises(ValueError, match="90"):
        prune_old(tmp_path, retain_days=89)


def test_prune_old_accepts_exactly_90(tmp_path):
    prune_old(tmp_path, retain_days=90)  # 不應 raise


def test_save_report_creates_parent_directory(tmp_path):
    nested = tmp_path / "nested" / "subdir"
    p = save_report({"x": 1}, nested)
    assert p.exists()
