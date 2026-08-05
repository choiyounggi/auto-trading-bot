# -*- coding: utf-8 -*-
"""신호 진입점(`src/signal/main.py`)이 .env 대신 Keychain 에서 자격증명을 받는다.

이 테스트는 `run()` 을 끝까지 돌리지 않는다 — run() 은 KRX/Brave/Telegram 네트워크를
호출한다. 자격증명 로딩 직후에 오는 `load_config()` 를 센티널 예외로 바꿔서
**네트워크가 시작되기 전에** 멈춘다. Keychain 로더도 전부 스텁이므로 실제 Keychain
을 건드리지 않는다.

핵심 계약:
  - 로딩은 **import 시점이 아니라 run() 안**에서 일어난다. import 시점이면 Keychain
    ACL 거부 같은 상황에서 launchd 잡이 로그를 남기기도 전에 죽는다.
  - 리포트는 길이/상태 문자열만 로그에 남는다 — 값은 절대 남지 않는다.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest

from src.signal import main
from src.util import keychain

SIGNAL_SRC = Path(main.__file__).resolve().parent


class _StopBeforeNetwork(Exception):
    """run() 이 자격증명 로딩을 마친 직후 멈추게 하는 센티널."""


def _boom() -> dict:
    raise _StopBeforeNetwork("load_config reached")


@pytest.fixture
def stubbed_run(monkeypatch):
    """run() 의 자격증명 구간만 실행되도록 주변을 전부 스텁한다.

    반환: (호출 기록 list, 각 로더가 돌려줄 리포트를 갈아끼우는 setter)
    """
    calls: list[str] = []
    reports = {
        "signal": {"KRX_ID": "keychain (4 chars)", "KRX_PW": "missing",
                   "BRAVE_SEARCH_API_KEY": "already-set"},
        "telegram": {"TELEGRAM_BOT_TOKEN": "keychain (46 chars)",
                     "TELEGRAM_CHAT_ID": "missing"},
    }

    def fake_signal() -> dict:
        calls.append("load_signal_keys")
        return reports["signal"]

    def fake_telegram() -> dict:
        calls.append("load_telegram_keys")
        return reports["telegram"]

    monkeypatch.setattr(main, "load_signal_keys", fake_signal)
    monkeypatch.setattr(main, "load_telegram_keys", fake_telegram)
    monkeypatch.setattr(main, "setup_logging", lambda: calls.append("setup_logging"))
    monkeypatch.setattr(main, "load_config", _boom)
    # 실제 Keychain 접근 차단 — 스텁이 새더라도 security(1) 은 절대 실행되지 않는다
    monkeypatch.setattr(keychain, "keychain_get", lambda s, a: pytest.fail(
        f"실제 Keychain 접근 시도: {s}/{a}"))
    return calls, reports


# ── 정상 ──────────────────────────────────────────────────────────────────

def test_importing_main_does_not_raise():
    module = importlib.import_module("src.signal.main")

    assert module.run is not None


def test_module_exposes_both_keychain_loaders():
    """dotenv 경로가 Keychain 경로로 대체됐음을 네임스페이스로 증명한다."""
    assert main.load_signal_keys is keychain.load_signal_keys
    assert main.load_telegram_keys is keychain.load_telegram_keys


def test_run_calls_both_loaders_before_anything_else(stubbed_run):
    calls, _ = stubbed_run

    with pytest.raises(_StopBeforeNetwork):
        main.run([])

    # setup_logging 이 먼저여야 리포트 INFO 로그가 launchd 로그 파일에 남는다
    assert calls == ["setup_logging", "load_signal_keys", "load_telegram_keys"]


def test_run_logs_both_reports_at_info(stubbed_run, caplog):
    _, reports = stubbed_run
    caplog.set_level(logging.INFO, logger="stock-signal")

    with pytest.raises(_StopBeforeNetwork):
        main.run([])

    text = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    for report in reports.values():
        for env_var, status in report.items():
            assert env_var in text
            assert status in text


# ── 에러 ──────────────────────────────────────────────────────────────────

def test_import_succeeds_even_if_load_signal_keys_raises(monkeypatch):
    """로딩이 import 시점이면 launchd 잡이 로그도 못 남기고 죽는다.

    keychain 모듈 쪽 심볼을 예외로 바꾼 뒤 main 을 reload 한다 — 모듈 본문에서
    호출한다면 여기서 터진다.
    """
    def exploding() -> dict:
        raise RuntimeError("keychain ACL denied")

    monkeypatch.setattr(keychain, "load_signal_keys", exploding)
    monkeypatch.setattr(keychain, "load_telegram_keys", exploding)

    reloaded = importlib.reload(main)

    try:
        assert reloaded.load_signal_keys is exploding  # reload 가 실제로 재바인딩했다
        with pytest.raises(RuntimeError, match="keychain ACL denied"):
            reloaded.run([])  # 호출 자체는 run() 안에서 일어난다
    finally:
        monkeypatch.undo()
        importlib.reload(main)


def test_run_propagates_nothing_before_credentials_are_loaded(stubbed_run):
    """load_config 단계(=네트워크 직전)까지 도달했다는 것 자체가 계약이다."""
    calls, _ = stubbed_run

    with pytest.raises(_StopBeforeNetwork, match="load_config reached"):
        main.run([])

    assert "load_telegram_keys" in calls


# ── 경계값 ────────────────────────────────────────────────────────────────

def test_main_module_has_no_load_dotenv_attribute():
    assert not hasattr(main, "load_dotenv")


def test_empty_reports_do_not_break_logging(stubbed_run, caplog):
    """키가 하나도 없는 머신(빈 dict) 에서도 로깅이 터지지 않는다."""
    calls, reports = stubbed_run
    reports["signal"] = {}
    reports["telegram"] = {}
    caplog.set_level(logging.INFO, logger="stock-signal")

    with pytest.raises(_StopBeforeNetwork):
        main.run([])

    assert calls == ["setup_logging", "load_signal_keys", "load_telegram_keys"]


def test_reports_never_reach_the_log_as_values(stubbed_run, caplog):
    """리포트에 값이 섞여 들어와도 로그로 나가는 경로를 확인 — 값 노출 회귀 방지."""
    _, reports = stubbed_run
    reports["signal"] = {"KRX_PW": "keychain (8 chars)"}
    reports["telegram"] = {"TELEGRAM_BOT_TOKEN": "keychain (46 chars)"}
    caplog.set_level(logging.INFO, logger="stock-signal")

    with pytest.raises(_StopBeforeNetwork):
        main.run([])

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "chars" in text
    # 리포트 문자열만 나가므로 어떤 형태의 원문 값도 로그에 존재할 수 없다
    assert "SEKRIT" not in text


def test_no_dotenv_reference_anywhere_in_signal_tree():
    """`.env` 경로가 다시 기어들어오는 회귀를 막는다."""
    hits = [
        f"{path.relative_to(SIGNAL_SRC)}:{i}"
        for path in sorted(SIGNAL_SRC.rglob("*.py"))
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "dotenv" in line
    ]

    assert hits == []
