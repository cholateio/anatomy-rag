"""可插拔認證（DL-016）。v1 dev stub 注入固定 user_id；production 留 OIDC 介面。

MUST NOT 將 user_id/學號送 LLM（由 chat.py 的 forbidden_identifiers 護欄保證）。
admin（教師）不受限流（§6.8）：dev 用 x-dev-admin 標頭；production 由 OIDC claim 判定。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from anatomy_backend.config import Settings, get_settings


@dataclass(frozen=True)
class User:
    user_id: str
    is_admin: bool


def resolve_user(settings: Settings, headers: dict) -> User:
    if settings.auth_mode == "dev":
        is_admin = headers.get("x-dev-admin") == "1"
        return User(user_id=settings.dev_user_id, is_admin=is_admin)
    # production：接回校內 SSO（OIDC）時實作——驗證 token、取 sub 與 role claim
    raise NotImplementedError("production OIDC 未接：請設定 SSO 或用 auth_mode=dev")


async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> User:
    return resolve_user(settings, {k.lower(): v for k, v in request.headers.items()})
