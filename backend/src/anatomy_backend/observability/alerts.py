"""§7.5 告警條件邏輯 + 可插拔 notifier（DL-026）。

v1 只交付條件評估與介面：metrics 來源彙整、連續時間窗排程、真實 Slack/email webhook
為 ops 後續（DL-011 Prometheus 延後）。預設 LogNotifier（log）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    name: str
    severity: str   # "must" | "should"
    message: str
    channels: tuple[str, ...]


def evaluate_alerts(metrics: dict) -> list[Alert]:
    """依 §7.5 條件回傳觸發告警。metrics 由上游彙整提供（v1 未排程，邏輯先就位）。"""
    out: list[Alert] = []
    if metrics.get("p95_latency_s", 0) > 8 and metrics.get("p95_breach_minutes", 0) >= 10:
        out.append(Alert("p95_latency", "must", "p95 latency > 8s 連續 ≥10 分鐘", ("slack",)))
    if metrics.get("model_error_rate", 0) > 0.05 and metrics.get("model_error_minutes", 0) >= 5:
        out.append(Alert("model_error_rate", "must", "模型錯誤率 > 5% 連續 ≥5 分鐘",
                         ("slack", "email")))
    if metrics.get("usage_ratio", 0) >= 0.80:
        out.append(Alert("usage_ratio", "must", "RPM/TPM 用量達 80%", ("slack",)))
    if metrics.get("citation_fail_rate", 0) > 0.10 and metrics.get("citation_fail_minutes", 0) >= 30:
        out.append(Alert("citation_fail_rate", "should", "引文驗證失敗率 > 10% 連續 ≥30 分鐘",
                         ("slack",)))
    return out


class Notifier(Protocol):
    def notify(self, alert: Alert) -> None: ...


class LogNotifier:
    """v1 預設：寫 log。真實 Slack/email webhook 為 ops 後續（新連線，先問）。"""

    def notify(self, alert: Alert) -> None:
        logger.warning("ALERT[%s/%s] %s → %s", alert.severity, alert.name,
                       alert.message, ",".join(alert.channels))
