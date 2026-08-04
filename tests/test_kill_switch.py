"""Kill Switch 테스트."""
from __future__ import annotations

import pytest

from src.guardrails import kill_switch


@pytest.fixture
def ks_file(tmp_path):
    return tmp_path / "KILL_SWITCH"


# 정상: 비활성 상태
def test_inactive_by_default(ks_file):
    assert kill_switch.is_active(ks_file) is False


# 정상: 활성 → is_active True
def test_activate(ks_file):
    kill_switch.activate("test reason", ks_file)
    assert kill_switch.is_active(ks_file) is True
    status = kill_switch.get_status(ks_file)
    assert status is not None
    assert status["reason"] == "test reason"


# 안전 규칙: deactivate 호출 시 user_confirmed=False면 거부
def test_deactivate_requires_confirmation(ks_file):
    kill_switch.activate("test", ks_file)
    with pytest.raises(RuntimeError, match="명시 확인"):
        kill_switch.deactivate(False, ks_file)
    assert kill_switch.is_active(ks_file) is True


# 정상: user_confirmed=True면 해제됨
def test_deactivate_with_confirmation(ks_file):
    kill_switch.activate("test", ks_file)
    kill_switch.deactivate(True, ks_file)
    assert kill_switch.is_active(ks_file) is False


# 경계값: 없는 파일에 deactivate (idempotent)
def test_deactivate_nonexistent_file(ks_file):
    kill_switch.deactivate(True, ks_file)
    # 에러 없이 종료


# 정상: get_status가 없는 파일에 None
def test_get_status_inactive(ks_file):
    assert kill_switch.get_status(ks_file) is None
