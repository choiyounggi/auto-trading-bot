# -*- coding: utf-8 -*-
"""신호 봇 자격증명(KRX·Brave) Keychain 로딩 테스트.

service/account 이름은 cli/keychain.ts 와의 **wire contract** 다 — 여기서 이름이
바뀌면 CLI 가 등록한 항목을 파이썬이 못 읽는다. 그래서 호출 인자까지 assert 한다.

KRX 로그인과 Brave 키는 **선택** 이다(D4). 없거나 조회가 실패해도 신호 봇은
기능을 줄여 계속 돌아야 하므로 예외를 올리지 않는다.

이 테스트는 실제 Keychain 을 건드리지 않는다 — keychain_get 을 항상 주입한다.
"""
from __future__ import annotations

import os

import pytest

from src.util import keychain
from src.util.keychain import load_signal_keys

SIGNAL_ENV_VARS = ("KRX_ID", "KRX_PW", "BRAVE_SEARCH_API_KEY")


@pytest.fixture
def clean_env(monkeypatch):
    """KRX_*/BRAVE_* 를 제거한 os.environ 사본으로 격리(테스트 간 누수 방지)."""
    env = {k: v for k, v in os.environ.items() if k not in SIGNAL_ENV_VARS}
    monkeypatch.setattr(os, "environ", env)
    return env


@pytest.fixture
def calls(monkeypatch):
    """실제 Keychain 접근 차단용 기본 스텁 — 기록만 하고 항상 miss."""
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        keychain, "keychain_get", lambda s, a: recorded.append((s, a)) or None
    )
    return recorded


def _stub(monkeypatch, values: dict, recorded: list | None = None):
    """account -> 값 매핑으로 keychain_get 을 대체한다."""

    def fake(service: str, account: str):
        if recorded is not None:
            recorded.append((service, account))
        v = values.get(account)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(keychain, "keychain_get", fake)


# ── 정상 ──────────────────────────────────────────────────────────────────

def test_all_three_present_are_injected_and_reported(clean_env, monkeypatch):
    _stub(monkeypatch, {"krx-id": "myid", "krx-pw": "pw123456", "brave-api-key": "bsk-abc"})

    report = load_signal_keys()

    assert os.environ["KRX_ID"] == "myid"
    assert os.environ["KRX_PW"] == "pw123456"
    assert os.environ["BRAVE_SEARCH_API_KEY"] == "bsk-abc"
    assert report == {
        "KRX_ID": "keychain (4 chars)",
        "KRX_PW": "keychain (8 chars)",
        "BRAVE_SEARCH_API_KEY": "keychain (7 chars)",
    }


def test_service_and_account_names_are_the_wire_contract(clean_env, calls):
    """cli/keychain.ts 가 등록하는 이름과 정확히 일치해야 한다."""
    load_signal_keys()

    assert calls == [
        ("signal-bot", "krx-id"),
        ("signal-bot", "krx-pw"),
        ("signal-bot", "brave-api-key"),
    ]
    assert keychain.SIGNAL_SERVICE == "signal-bot"


# ── 에러 ──────────────────────────────────────────────────────────────────

def test_keychain_get_raising_is_not_propagated(clean_env, monkeypatch):
    """조회 실패(ACL 차단 등)는 전파하지 않고 해당 키만 missing 으로 보고한다."""
    _stub(monkeypatch, {
        "krx-id": "myid",
        "krx-pw": RuntimeError("keychain ACL denied"),
        "brave-api-key": "bsk-abc",
    })

    report = load_signal_keys()

    assert report["KRX_PW"] == "missing"
    assert "KRX_PW" not in os.environ
    # 나머지 키는 정상 주입 — 하나의 실패가 전체를 막지 않는다
    assert os.environ["KRX_ID"] == "myid"
    assert os.environ["BRAVE_SEARCH_API_KEY"] == "bsk-abc"


def test_all_keychain_lookups_raising_still_returns(clean_env, monkeypatch):
    boom = OSError("security(1) not available")
    _stub(monkeypatch, {"krx-id": boom, "krx-pw": boom, "brave-api-key": boom})

    report = load_signal_keys()

    assert report == {k: "missing" for k in SIGNAL_ENV_VARS}
    assert not any(k in os.environ for k in SIGNAL_ENV_VARS)


def test_preset_env_var_is_left_untouched(clean_env, monkeypatch):
    """명시적 override(.env, launchd) 를 Keychain 이 덮어쓰면 안 된다."""
    os.environ["KRX_ID"] = "explicit-override"
    recorded: list[tuple[str, str]] = []
    _stub(monkeypatch, {"krx-id": "from-keychain", "krx-pw": "pw"}, recorded)

    report = load_signal_keys()

    assert os.environ["KRX_ID"] == "explicit-override"
    assert report["KRX_ID"] == "already-set"
    assert ("signal-bot", "krx-id") not in recorded  # 조회 자체를 하지 않는다


# ── 경계값 ────────────────────────────────────────────────────────────────

def test_none_present_reports_all_missing_and_sets_nothing(clean_env, calls):
    report = load_signal_keys()

    assert report == {k: "missing" for k in SIGNAL_ENV_VARS}
    assert not any(k in os.environ for k in SIGNAL_ENV_VARS)


def test_empty_string_counts_as_missing(clean_env, monkeypatch):
    _stub(monkeypatch, {"krx-id": "", "krx-pw": "", "brave-api-key": ""})

    report = load_signal_keys()

    assert report == {k: "missing" for k in SIGNAL_ENV_VARS}
    assert not any(k in os.environ for k in SIGNAL_ENV_VARS)


def test_report_never_contains_a_secret_value(clean_env, monkeypatch):
    _stub(monkeypatch, {
        "krx-id": "SEKRIT-id",
        "krx-pw": "SEKRIT-pw",
        "brave-api-key": "SEKRIT-brave",
    })

    report = load_signal_keys()

    assert "SEKRIT" not in repr(report)
    # 값 자체는 제대로 주입됐음을 함께 확인 — 빈 리포트로 통과하는 것을 막는다
    assert os.environ["KRX_PW"] == "SEKRIT-pw"


def test_env_vars_do_not_leak_between_tests():
    """앞선 테스트들이 실제 os.environ 을 오염시키지 않았는지 확인."""
    assert not any(os.environ.get(k, "").startswith("SEKRIT") for k in SIGNAL_ENV_VARS)
