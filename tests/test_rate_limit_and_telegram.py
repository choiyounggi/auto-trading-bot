# -*- coding: utf-8 -*-
"""rate limit 스로틀/재시도 + telegram Markdown 폴백 회귀 테스트.

배경(2026-07-06): ① 모니터 get_quote가 '초당 거래건수 초과'로 실패 →
⚠️ 시세 조회 실패 경고 + 해당 사이클 SL/TP 체크 skip.
② BUY 알림 본문의 strategy_id 밑줄(_)이 Markdown entity로 오파싱 → 400 → 알림 유실.

2026-08-24 이후 throttle 상태는 계좌 단위 공유 파일(`_throttle_path`)이며
시계는 벽시계(time.time())다 — 인스턴스 단위 monotonic이 아니다.
"""
from __future__ import annotations

import time

from src.agent.telegram_agent import Agent
from src.broker import kis_client as kc
from src.broker.kis_client import KisClient
from src.notify import telegram


# ============================================================
# KisClient — 초당 거래건수 스로틀 / 재시도
# ============================================================

def _client() -> KisClient:
    c = KisClient(mode="paper")
    c.base_url = "http://test"
    c.app_key = "k"
    c.app_secret = "s"
    c.cano = "12345678"
    return c


def test_throttle_sleeps_when_called_back_to_back(monkeypatch):
    c = _client()
    slept = []
    monkeypatch.setattr("src.broker.kis_client.time.sleep", lambda s: slept.append(s))
    kc._throttle_path(c.mode, c.cano).write_text(f"{time.time():.6f}", encoding="utf-8")  # 직전에 호출한 상태
    c._throttle()
    assert len(slept) == 1
    assert 0 < slept[0] <= 1.05


def test_throttle_no_sleep_after_interval(monkeypatch):
    c = _client()
    slept = []
    monkeypatch.setattr("src.broker.kis_client.time.sleep", lambda s: slept.append(s))
    kc._throttle_path(c.mode, c.cano).write_text(f"{time.time() - 10.0:.6f}", encoding="utf-8")  # 오래 전
    c._throttle()
    assert slept == []


def test_throttle_interval_by_mode():
    paper = KisClient(mode="paper")
    real = KisClient(mode="real")
    # 모의: 공칭 2건/초지만 실측상 같은 초 창 2번째 호출도 거부 → 초 경계 회피 1.05s
    assert paper._min_request_interval == 1.05
    assert real._min_request_interval == 0.06   # 실전 20건/초


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


_RATE_LIMITED = {"rt_cd": "1", "msg1": "초당 거래건수를 초과하였습니다."}
_QUOTE_OK = {
    "rt_cd": "0",
    "output": {"stck_prpr": "152000", "stck_oprc": "150000", "stck_hgpr": "153000",
               "stck_lwpr": "149000", "stck_sdpr": "151000", "prdy_ctrt": "0.66",
               "acml_vol": "1000000"},
}


def test_get_quote_retries_once_on_rate_limit(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_headers", lambda tr_id: {})
    monkeypatch.setattr("src.broker.kis_client.time.sleep", lambda s: None)
    responses = [_Resp(_RATE_LIMITED), _Resp(_QUOTE_OK)]
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return responses[len(calls) - 1]

    monkeypatch.setattr("src.broker.kis_client.requests.get", fake_get)
    q = c.get_quote("000270")
    assert len(calls) == 2          # 재시도 1회
    assert q is not None
    assert q.current_price == 152000


def test_get_quote_gives_up_after_one_retry(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_headers", lambda tr_id: {})
    monkeypatch.setattr("src.broker.kis_client.time.sleep", lambda s: None)
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return _Resp(_RATE_LIMITED)

    monkeypatch.setattr("src.broker.kis_client.requests.get", fake_get)
    q = c.get_quote("000270")
    assert q is None
    assert len(calls) == 2          # 무한 재시도 금지


def test_get_quote_non_rate_limit_error_no_retry(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "_headers", lambda tr_id: {})
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        return _Resp({"rt_cd": "1", "msg1": "조회할 자료가 없습니다."})

    monkeypatch.setattr("src.broker.kis_client.requests.get", fake_get)
    q = c.get_quote("000270")
    assert q is None
    assert len(calls) == 1          # rate limit 외 오류는 재시도 없음


# ============================================================
# telegram_agent — 명령 간 KisClient 캐싱 (이슈 완료 기준 5)
# ============================================================

def _agent(mode: str = "paper") -> Agent:
    """Repo()/keychain 접근 없이 _client() 만 격리 검증하는 인스턴스."""
    a = object.__new__(Agent)
    a.mode = mode
    a._kis = None
    return a


def test_agent_reuses_the_kis_client_between_commands():
    a = _agent()
    assert a._client() is a._client()


def test_agent_rebuilds_the_client_after_a_mode_switch():
    a = _agent()
    first = a._client()
    a.mode = "real"
    second = a._client()
    assert second is not first
    assert second.mode == "real"


def test_agent_builds_a_client_on_first_use():
    a = _agent()
    assert a._kis is None
    c = a._client()
    assert isinstance(c, KisClient)
    assert a._kis is c


# ============================================================
# telegram — Markdown 파싱 실패 시 plain text 폴백
# ============================================================

class _TgResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _patch_credentials(monkeypatch):
    monkeypatch.setattr(telegram, "_get_credentials", lambda: ("tok", "chat"))


def test_telegram_plain_fallback_on_parse_error(monkeypatch):
    _patch_credentials(monkeypatch)
    payloads = []

    def fake_post(url, json=None, timeout=None):
        payloads.append(json)
        if "parse_mode" in json:
            return _TgResp(400, '{"description":"Bad Request: can\'t parse entities: '
                                'Can\'t find end of the entity starting at byte offset 96"}')
        return _TgResp(200)

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    ok = telegram.send("전략: short_cover (+6)")
    assert ok is True
    assert len(payloads) == 2
    assert "parse_mode" in payloads[0]
    assert "parse_mode" not in payloads[1]        # 폴백은 plain text
    assert payloads[1]["text"] == payloads[0]["text"]  # 본문 동일


def test_telegram_success_first_try_single_call(monkeypatch):
    _patch_credentials(monkeypatch)
    payloads = []

    def fake_post(url, json=None, timeout=None):
        payloads.append(json)
        return _TgResp(200)

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    assert telegram.send("정상 메시지") is True
    assert len(payloads) == 1


def test_telegram_non_parse_400_no_fallback(monkeypatch):
    _patch_credentials(monkeypatch)
    payloads = []

    def fake_post(url, json=None, timeout=None):
        payloads.append(json)
        return _TgResp(400, '{"description":"Bad Request: chat not found"}')

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    assert telegram.send("메시지") is False
    assert len(payloads) == 1       # parse 오류가 아니면 재전송 없음


def test_telegram_noop_without_credentials(monkeypatch):
    monkeypatch.setattr(telegram, "_get_credentials", lambda: (None, None))
    called = []
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: called.append(1))
    assert telegram.send("메시지") is False
    assert called == []
