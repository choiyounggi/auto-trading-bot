# -*- coding: utf-8 -*-
"""Keychain service 이름(kis-openapi) 회귀 테스트.

배경(2026-07-06): 브로커는 처음부터 한투 KIS인데 Keychain service가 초기
kiwoom-mcp 시절 이름(kiwoom-openapi)이었음 → kis-openapi로 개명(항목 복사).
코드가 옛 서비스명을 다시 참조하면 launchd 잡 전체가 자격증명을 못 읽는다.
"""
from __future__ import annotations

import os

from src.util import keychain
from src.util.keychain import load_kis_keys


def _isolate_env(monkeypatch):
    """KIS_* 환경변수가 이미 세팅돼 있으면 keychain 조회를 skip하므로 격리."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("KIS_")}
    monkeypatch.setattr(os, "environ", clean)


def test_load_kis_keys_queries_kis_openapi_service(monkeypatch):
    _isolate_env(monkeypatch)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(keychain, "keychain_get",
                        lambda s, a: calls.append((s, a)) or None)
    load_kis_keys("paper")
    assert {s for s, _ in calls} == {"kis-openapi"}   # 옛 kiwoom-openapi 참조 금지
    assert {a for _, a in calls} == {"paper-appkey", "paper-secret", "paper-account"}


def test_account_10digit_splits_cano_and_prdt_cd(monkeypatch):
    _isolate_env(monkeypatch)
    values = {"paper-appkey": "AK", "paper-secret": "SEC", "paper-account": "1234567801"}
    monkeypatch.setattr(keychain, "keychain_get", lambda s, a: values.get(a))
    load_kis_keys("paper")
    assert os.environ["KIS_CANO"] == "12345678"
    assert os.environ["KIS_ACNT_PRDT_CD"] == "01"


def test_account_8digit_falls_back_to_default_prdt_cd(monkeypatch):
    # 실제 등록값이 8자리인 케이스 (2026-07-06 실측: len=8)
    _isolate_env(monkeypatch)
    values = {"paper-appkey": "AK", "paper-secret": "SEC", "paper-account": "12345678"}
    monkeypatch.setattr(keychain, "keychain_get", lambda s, a: values.get(a))
    load_kis_keys("paper")
    assert os.environ["KIS_CANO"] == "12345678"
    assert os.environ["KIS_ACNT_PRDT_CD"] == "01"


def test_missing_keys_reported(monkeypatch):
    _isolate_env(monkeypatch)
    monkeypatch.setattr(keychain, "keychain_get", lambda s, a: None)
    loaded = load_kis_keys("paper")
    assert loaded["KIS_APP_KEY"] == "missing"
    assert loaded["KIS_CANO"] == "missing"
