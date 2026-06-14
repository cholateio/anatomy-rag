"""評估報告持久化與保留策略（§7.6，DL-028）。

save_report：將 RAGAS report dict 以 UTC + 微秒時戳的 JSON 檔名寫到指定目錄。
prune_old：刪除超過 retain_days（必須 ≥90）天的報告；解析失敗的檔名 warning + skip。

檔名格式：eval_report_<UTCYYYYmmddTHHMMSSffffff>Z.json
  - UTC 避免時區歧義（[M-4]）
  - 微秒（%f）防同秒覆寫
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_FNAME_PREFIX = "eval_report_"
_FNAME_SUFFIX = "Z.json"
_TS_FMT = "%Y%m%dT%H%M%S%f"
_MINIMUM_RETAIN_DAYS = 90


def save_report(report: dict, directory: str | Path) -> Path:
    """RAGAS report dict を JSON として保存する。

    Args:
        report: 保存する評估結果 dict。
        directory: 保存先ディレクトリ（存在しない場合は作成）。

    Returns:
        作成したファイルのパス。
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime(_TS_FMT)  # noqa: UP017
    fname = d / f"{_FNAME_PREFIX}{ts}{_FNAME_SUFFIX}"
    fname.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return fname


def prune_old(directory: str | Path, retain_days: int = 90) -> list[Path]:
    """retain_days 天前的報告檔刪除；回傳已刪清單。

    Args:
        directory: 報告檔所在目錄。
        retain_days: 保留天數（必須 ≥90；設計紅線）。

    Returns:
        已刪除的檔案路徑列表。

    Raises:
        ValueError: retain_days < 90。
    """
    if retain_days < _MINIMUM_RETAIN_DAYS:
        raise ValueError(
            f"retain_days 必須 ≥{_MINIMUM_RETAIN_DAYS}，收到 {retain_days}（設計紅線，防誤刪）"
        )
    d = Path(directory)
    if not d.exists():
        return []

    now = datetime.now(tz=timezone.utc)  # noqa: UP017
    cutoff = now - timedelta(days=retain_days)
    deleted: list[Path] = []

    for p in d.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if not (name.startswith(_FNAME_PREFIX) and name.endswith(_FNAME_SUFFIX)):
            continue
        # 取出時戳部分
        ts_str = name.removeprefix(_FNAME_PREFIX).removesuffix(_FNAME_SUFFIX)
        try:
            ts = datetime.strptime(ts_str, _TS_FMT).replace(tzinfo=timezone.utc)  # noqa: UP017
        except ValueError:
            logger.warning("無法解析報告檔名時戳，略過：%s", name)
            continue
        if ts < cutoff:
            p.unlink()
            deleted.append(p)

    return deleted
