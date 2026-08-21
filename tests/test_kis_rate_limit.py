# -*- coding: utf-8 -*-
"""KIS 레이트리밋 회귀 테스트 (task 14).

배경(2026-08-04 orchestrator.log): 토큰 발급 POST(09:05:00,354→,495)와
곧이은 get_balance GET(,543)이 같은 초 안에 나가 '초당 거래건수 초과'로
당일 진입 런이 죽었다. 원인 2가지:
  (a) _headers()가 _throttle() 뒤에 _get_token()을 호출해 토큰 POST가
      스로틀을 우회하고 _last_request_at 도 갱신하지 않았다.
  (b) get_balance()는 rt_cd != "0" 이면 무조건 None — 일시적 스로틀도 재시도 없음.

wiki: backend-common-reliability-timeouts-and-retries
  - rule 4: 2~3회 시도 + full jitter `random(0, min(cap, base*2**attempt))`
  - 실패 유형 표 429 행: 스로틀 응답은 백오프 후 재시도
  - edge case "Retries exist at several layers": 재시도는 한 계층에만
"""
from __future__ import annotations

import pytest
import requests

from src.broker import kis_client as kc
from src.broker.kis_client import Balance, KisClient


# ============================================================
# 헬퍼 — 네트워크/홈 디렉토리 접근 없이 클라이언트 구성
# ============================================================

def _client(monkeypatch, tmp_path, mode: str = "paper") -> KisClient:
    """토큰 캐시를 tmp_path로 격리한 KisClient (캐시 파일 없음 = 토큰 미보유)."""
    monkeypatch.setattr(kc, "_token_cache_path", lambda m: tmp_path / f"token-{m}.json")
    c = KisClient(mode=mode)
    c.base_url = "http://test"
    c.app_key = "k"
    c.app_secret = "s"
    c.cano = "12345678"
    c.acnt_prdt_cd = "01"
    return c


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict:
        return self._payload


_RATE_LIMITED = {"rt_cd": "1", "msg1": "초당 거래건수를 초과하였습니다."}
_NOT_FOUND = {"rt_cd": "1", "msg1": "해당 서비스를 찾을수 없습니다"}
_BALANCE_OK = {
    "rt_cd": "0",
    "output1": [{
        "pdno": "005930", "prdt_name": "삼성전자", "hldg_qty": "10",
        "pchs_avg_pric": "70000", "prpr": "72000", "evlu_amt": "720000",
        "evlu_pfls_rt": "2.86",
    }],
    "output2": [{"prvs_rcdl_excc_amt": "1000000", "tot_evlu_amt": "1720000"}],
}


def _stub_balance_transport(monkeypatch, c: KisClient, responses: list[_Resp]) -> list[dict]:
    """requests.get 을 고정 응답 시퀀스로 대체. 호출 기록을 돌려준다."""
    monkeypatch.setattr(c, "_headers", lambda tr_id: {"tr_id": tr_id})
    calls: list[dict] = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(kc.requests, "get", fake_get)
    return calls


# ============================================================
# 정상 — 스로틀 응답 재시도
# ============================================================

def test_get_balance_retries_on_rate_limit_then_succeeds(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    calls = _stub_balance_transport(monkeypatch, c, [_Resp(_RATE_LIMITED), _Resp(_BALANCE_OK)])
    slept: list[float] = []

    b = c.get_balance(sleep=slept.append, rand=lambda: 1.0)

    assert isinstance(b, Balance)
    assert b.cash == 1000000
    assert b.total_eval == 1720000
    assert b.positions[0]["ticker"] == "005930"
    assert b.positions[0]["qty"] == 10
    assert len(calls) == 2                      # 스로틀 1회 → 재시도 1회로 성공
    assert slept == [kc.RATE_LIMIT_BASE_SEC]    # attempt 0, rand=1.0 → base


def test_get_balance_first_try_success_calls_once_and_never_sleeps(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    calls = _stub_balance_transport(monkeypatch, c, [_Resp(_BALANCE_OK)])
    slept: list[float] = []

    b = c.get_balance(sleep=slept.append, rand=lambda: 1.0)

    assert isinstance(b, Balance)
    assert len(calls) == 1
    assert slept == []                          # 성공 경로에서 대기 없음


def test_get_balance_signature_stays_callable_without_arguments(monkeypatch, tmp_path):
    """task 11b가 get_balance()를 인자 없이 호출한다 — 시그니처 유지."""
    c = _client(monkeypatch, tmp_path)
    calls = _stub_balance_transport(monkeypatch, c, [_Resp(_BALANCE_OK)])
    monkeypatch.setattr(kc.time, "sleep", lambda s: pytest.fail("실제 sleep 발생"))

    b = c.get_balance()

    assert isinstance(b, Balance)
    assert len(calls) == 1


# ============================================================
# 에러 — 재시도 예산 상한 / 비-스로틀 실패 / 네트워크 오류
# ============================================================

def test_get_balance_gives_up_after_max_attempts(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    calls = _stub_balance_transport(monkeypatch, c, [_Resp(_RATE_LIMITED)])
    slept: list[float] = []

    b = c.get_balance(sleep=slept.append, rand=lambda: 1.0)

    assert b is None
    assert len(calls) == kc.RATE_LIMIT_MAX_ATTEMPTS == 3     # 예산 상한, 무한 재시도 금지
    assert slept == [0.5, 1.0]                               # 마지막 실패 뒤에는 대기 없음


def test_get_balance_non_rate_limit_error_is_not_retried(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    calls = _stub_balance_transport(monkeypatch, c, [_Resp(_NOT_FOUND), _Resp(_BALANCE_OK)])
    slept: list[float] = []

    b = c.get_balance(sleep=slept.append, rand=lambda: 1.0)

    assert b is None
    assert len(calls) == 1      # wiki 실패 유형 표: 스로틀이 아닌 거절은 재시도 안 함
    assert slept == []


def test_get_balance_network_error_retries_then_succeeds(monkeypatch, tmp_path):
    """2026-08 실측: VTS가 정각 배치 시간대에 read timeout(10s)을 자주 냄 —
    일시 네트워크 오류도 스로틀과 같은 백오프 예산으로 재시도한다."""
    c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(c, "_headers", lambda tr_id: {})
    calls: list[str] = []

    def flaky(url, headers=None, params=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise requests.Timeout("read timed out")
        return _Resp(_BALANCE_OK)

    monkeypatch.setattr(kc.requests, "get", flaky)
    slept: list[float] = []

    b = c.get_balance(sleep=slept.append, rand=lambda: 1.0)

    assert isinstance(b, Balance)
    assert len(calls) == 2
    assert slept == [kc.RATE_LIMIT_BASE_SEC]


def test_get_balance_network_error_gives_up_after_max_attempts(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(c, "_headers", lambda tr_id: {})
    calls: list[str] = []

    def boom(url, headers=None, params=None, timeout=None):
        calls.append(url)
        raise requests.RequestException("connection reset")

    monkeypatch.setattr(kc.requests, "get", boom)
    slept: list[float] = []

    b = c.get_balance(sleep=slept.append, rand=lambda: 1.0)

    assert b is None
    assert len(calls) == kc.RATE_LIMIT_MAX_ATTEMPTS   # 예산 상한 공유, 무한 재시도 금지
    assert slept == [0.5, 1.0]                        # 마지막 실패 뒤에는 대기 없음


def test_get_balance_empty_output_returns_zeroed_balance(monkeypatch, tmp_path):
    """경계: output1/output2 가 빈 배열이어도 None 이 아니라 0 잔고."""
    c = _client(monkeypatch, tmp_path)
    _stub_balance_transport(monkeypatch, c, [_Resp({"rt_cd": "0", "output1": [], "output2": []})])

    b = c.get_balance(sleep=lambda s: None, rand=lambda: 1.0)

    assert isinstance(b, Balance)
    assert b.cash == 0
    assert b.total_eval == 0
    assert b.positions == []


def test_get_balance_missing_msg1_is_not_treated_as_throttle(monkeypatch, tmp_path):
    """경계: msg1 자체가 없는 실패 응답 — 마커 판정이 None 에서 터지면 안 된다."""
    c = _client(monkeypatch, tmp_path)
    calls = _stub_balance_transport(monkeypatch, c, [_Resp({"rt_cd": "1"})])

    b = c.get_balance(sleep=lambda s: None, rand=lambda: 1.0)

    assert b is None
    assert len(calls) == 1


# ============================================================
# 모드별 HTTP timeout — 모의(VTS)는 정각 시간대 응답 지연이 잦아 넉넉히,
# 실전은 죽은 서버에 오래 매달리지 않게 짧게
# ============================================================

@pytest.mark.parametrize("mode,expected_timeout", [("paper", 20), ("real", 10)])
def test_get_balance_passes_mode_specific_http_timeout(monkeypatch, tmp_path, mode, expected_timeout):
    c = _client(monkeypatch, tmp_path, mode=mode)
    monkeypatch.setattr(c, "_headers", lambda tr_id: {})
    seen: list[float] = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append(timeout)
        return _Resp(_BALANCE_OK)

    monkeypatch.setattr(kc.requests, "get", fake_get)

    b = c.get_balance(sleep=lambda s: None, rand=lambda: 1.0)

    assert isinstance(b, Balance)
    assert seen == [expected_timeout]


# ============================================================
# 경계 — full jitter 백오프 공식
# ============================================================

def test_backoff_full_jitter_spans_whole_interval():
    # attempt 0 상한 = base * 2**0 = 0.5
    assert kc._rate_limit_backoff(0, rand=lambda: 1.0) == 0.5
    assert kc._rate_limit_backoff(0, rand=lambda: 0.0) == 0.0   # 0 포함 = full jitter


def test_backoff_grows_exponentially_then_caps():
    assert kc._rate_limit_backoff(1, rand=lambda: 1.0) == 1.0    # base * 2**1
    assert kc._rate_limit_backoff(2, rand=lambda: 1.0) == 2.0    # base * 2**2
    assert kc._rate_limit_backoff(10, rand=lambda: 1.0) == kc.RATE_LIMIT_CAP_SEC == 4.0


def test_backoff_default_rand_stays_within_bounds():
    for attempt in range(4):
        upper = min(kc.RATE_LIMIT_CAP_SEC, kc.RATE_LIMIT_BASE_SEC * 2 ** attempt)
        for _ in range(50):
            assert 0.0 <= kc._rate_limit_backoff(attempt) <= upper


# ============================================================
# 회귀 — 토큰 POST 가 스로틀을 우회하지 않는다
# ============================================================

class _FakeClock:
    """주입된 단조 시계 — sleep 이 시계를 전진시킨다 (벽시계 의존 없음)."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.now += seconds


_TOKEN_OK = {"access_token": "tok-abc", "expires_in": 86400}


def test_token_post_and_balance_get_are_throttled_apart(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    assert c._token is None      # 토큰 캐시 비어 있음 = 발급 경로

    clock = _FakeClock()
    monkeypatch.setattr(kc.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(kc.time, "sleep", clock.sleep)
    c._last_request_at = 0.0

    stamps: dict[str, float] = {}

    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/oauth2/tokenP")
        stamps["token"] = clock.monotonic()
        return _Resp(_TOKEN_OK)

    def fake_get(url, headers=None, params=None, timeout=None):
        stamps["balance"] = clock.monotonic()
        assert headers["authorization"] == "Bearer tok-abc"
        return _Resp(_BALANCE_OK)

    monkeypatch.setattr(kc.requests, "post", fake_post)
    monkeypatch.setattr(kc.requests, "get", fake_get)

    b = c.get_balance(sleep=lambda s: pytest.fail("성공 응답에 백오프 대기 발생"),
                      rand=lambda: 1.0)

    assert isinstance(b, Balance)
    assert set(stamps) == {"token", "balance"}
    gap = stamps["balance"] - stamps["token"]
    # 1e-6 은 시계 누적의 부동소수 오차 허용치 — 결함(간격 0.0s)과는 6자리 차이.
    assert gap >= c._min_request_interval - 1e-6, (
        f"토큰 POST와 잔고 GET 간격 {gap}s < {c._min_request_interval}s "
        "— 토큰 발급이 스로틀을 우회했다"
    )


def test_token_post_updates_last_request_at(monkeypatch, tmp_path):
    """토큰 POST 자체가 스로틀 타임스탬프를 갱신해야 다음 호출이 띄워진다."""
    c = _client(monkeypatch, tmp_path)
    clock = _FakeClock()
    monkeypatch.setattr(kc.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(kc.time, "sleep", clock.sleep)
    c._last_request_at = 0.0

    monkeypatch.setattr(kc.requests, "post",
                        lambda url, json=None, timeout=None: _Resp(_TOKEN_OK))

    token = c._get_token()

    assert token == "tok-abc"
    assert c._last_request_at == clock.now      # POST 직전 스로틀 스탬프
