from types import SimpleNamespace

import pytest
from anatomy_backend.api.auth import User, resolve_user


def _settings(uid="00000000-0000-0000-0000-000000000001", mode="dev"):
    return SimpleNamespace(dev_user_id=uid, auth_mode=mode)


def test_dev_stub_returns_configured_user():
    u = resolve_user(_settings(), headers={})
    assert isinstance(u, User)
    assert u.user_id == "00000000-0000-0000-0000-000000000001"
    assert u.is_admin is False


def test_dev_admin_header_grants_admin():
    u = resolve_user(_settings(), headers={"x-dev-admin": "1"})
    assert u.is_admin is True


def test_production_mode_without_oidc_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        resolve_user(_settings(mode="production"), headers={})
