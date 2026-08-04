# -*- coding: utf-8 -*-
"""KIS 연결성 프로브(D18) 계약 테스트.

probe()는 TS doctor가 파싱하는 유일한 계약이므로 ① 키 6개 고정,
② stdout은 어떤 실패에서도 유효한 JSON, ③ 앱키/시크릿/전체 CANO 미포함을
회귀로 고정한다. 실제 네트워크 호출은 하지 않는다 — KisClient는 전부 스텁.
"""
from __future__ import annotations

import json

import pytest
import requests

from src.broker import probe as probe_mod
from src.broker.kis_client import BASE_URL_PAPER, BASE_URL_PROD, Balance

CONTRACT_KEYS = {"ok", "mode", "base_url", "cano_masked", "reason", "detail"}
KIS_ENV_VARS = ("KIS_MODE", "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_CANO", "KIS_ACNT_PRDT_CD")

APP_KEY = "APPKEY-1234567890"
APP_SECRET = "SUPERSECRET"
CANO = "50120180"


def _env(monkeypatch, **overrides):
    """KIS_* 환경변수를 완전히 격리한 뒤 지정한 값만 세팅."""
    for name in KIS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    # Keychain 조회 금지 — 환경변수는 테스트가 이미 주입한 상태로 본다.
    monkeypatch.setattr(probe_mod, "load_kis_keys", lambda mode=None: {})


def _creds(monkeypatch, **overrides):
    base = {"KIS_APP_KEY": APP_KEY, "KIS_APP_SECRET": APP_SECRET, "KIS_CANO": CANO}
    base.update(overrides)
    _env(monkeypatch, **base)


def _install_client(monkeypatch, *, balance=None, error=None, on_init=None):
    """KisClient 스텁 설치 — get_balance가 balance를 반환하거나 error를 raise."""
    calls: list[str] = []

    class _StubClient:
        def __init__(self, mode: str = "paper", **kwargs):
            calls.append(mode)
            if on_init is not None:
                raise on_init
            self.mode = mode
            self.base_url = "https://stub.invalid"

        def get_balance(self):
            if error is not None:
                raise error
            return balance

    monkeypatch.setattr(probe_mod, "KisClient", _StubClient)
    return calls


# ============================================================
# 정상 경로
# ============================================================

def test_successful_balance_is_ok_with_masked_cano(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=Balance(cash=1000, total_eval=2000))

    result = probe_mod.probe("paper")

    assert result["ok"] is True
    assert result["reason"] == ""
    assert result["mode"] == "paper"
    assert result["base_url"] == BASE_URL_PAPER
    assert result["cano_masked"] == "****0180"
    assert result["cano_masked"].endswith(CANO[-4:])


def test_result_has_exactly_the_six_contract_keys(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=Balance())

    assert set(probe_mod.probe("paper")) == CONTRACT_KEYS


def test_result_json_round_trips(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=Balance())

    result = probe_mod.probe()
    assert json.loads(json.dumps(result)) == result


def test_real_mode_uses_prod_base_url(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=Balance())

    result = probe_mod.probe("real")

    assert result["mode"] == "real"
    assert result["base_url"] == BASE_URL_PROD


# ============================================================
# 에러 경로 — reason 슬러그 6종
# ============================================================

@pytest.mark.parametrize("missing", ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_CANO"])
def test_missing_credentials(monkeypatch, missing):
    _creds(monkeypatch, **{missing: ""})
    _install_client(monkeypatch, balance=Balance())

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "missing_credentials"
    assert missing in result["detail"]


def test_network_error_from_get_balance(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=requests.ConnectionError("connection refused"))

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "network"
    assert "connection refused" in result["detail"]


def test_token_issuance_failure_is_auth_failed(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=RuntimeError("KIS token 발급 실패: HTTP 403"))

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "auth_failed"
    assert "403" in result["detail"]


def test_rate_limit_message_is_rate_limited(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=RuntimeError("초당 거래건수를 초과하였습니다"))

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "rate_limited"


def test_unclassified_exception_is_unknown(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=ValueError("무언가 이상함"))

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "unknown"


def test_client_construction_failure_is_unknown(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, on_init=OSError("boom"))

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "unknown"
    assert result["cano_masked"] == "****0180"


def test_load_kis_keys_failure_is_unknown(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=Balance())

    def _boom(mode=None):
        raise OSError("keychain unavailable")

    monkeypatch.setattr(probe_mod, "load_kis_keys", _boom)

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "unknown"
    assert set(result) == CONTRACT_KEYS


# ============================================================
# 경계값
# ============================================================

def test_balance_none_is_rejected_not_unknown(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=None)

    result = probe_mod.probe("paper")

    assert result["ok"] is False
    assert result["reason"] == "rejected"


def test_detail_longer_than_200_chars_is_truncated_to_exactly_200(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=ValueError("X" * 500))

    result = probe_mod.probe("paper")

    assert len(result["detail"]) == 200
    assert result["detail"] == "X" * 200


def test_detail_shorter_than_limit_is_not_padded(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=ValueError("짧은 오류"))

    assert probe_mod.probe("paper")["detail"] == "짧은 오류"


def test_unset_cano_masks_to_empty_string(monkeypatch):
    _creds(monkeypatch, KIS_CANO="")
    _install_client(monkeypatch, balance=Balance())

    assert probe_mod.probe("paper")["cano_masked"] == ""


def test_short_cano_never_exposes_the_whole_value(monkeypatch):
    # 계좌번호는 8자리가 정상이지만, 4자리 이하가 들어와도 전체가 노출되면 안 된다.
    _creds(monkeypatch, KIS_CANO="0180")
    _install_client(monkeypatch, balance=Balance())

    result = probe_mod.probe("paper")

    assert result["cano_masked"] == "****"
    assert "0180" not in json.dumps(result)


def test_unknown_mode_falls_back_to_paper(monkeypatch):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=Balance())

    result = probe_mod.probe("SANDBOX")

    assert result["mode"] == "paper"
    assert result["base_url"] == BASE_URL_PAPER


# ============================================================
# 시크릿 비노출 (DoD: 앱키/시크릿/전체 CANO 가 출력에 절대 없음)
# ============================================================

def test_secrets_never_reach_the_serialized_json(monkeypatch):
    _creds(monkeypatch)
    # 예외 메시지에 시크릿과 전체 CANO가 그대로 섞여 들어온 최악의 경우.
    _install_client(
        monkeypatch,
        error=RuntimeError(f"auth dump appkey={APP_KEY} appsecret={APP_SECRET} CANO={CANO}"),
    )

    payload = json.dumps(probe_mod.probe("paper"))

    assert APP_SECRET not in payload
    assert APP_KEY not in payload
    assert CANO not in payload
    assert json.loads(payload)["ok"] is False


def test_stderr_traceback_is_redacted(monkeypatch, capsys):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=RuntimeError(f"secret leak {APP_SECRET} / {CANO}"))

    probe_mod.probe("paper")

    captured = capsys.readouterr()
    assert captured.out == ""            # 프로브 자체는 stdout에 아무것도 쓰지 않는다
    assert "Traceback" in captured.err   # 진단용 트레이스백은 stderr로만
    assert APP_SECRET not in captured.err
    assert CANO not in captured.err


# ============================================================
# main() — stdout JSON 계약 + 종료 코드
# ============================================================

def test_main_prints_json_and_exits_zero_on_success(monkeypatch, capsys):
    _creds(monkeypatch)
    _install_client(monkeypatch, balance=Balance())

    code = probe_mod.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert set(payload) == CONTRACT_KEYS


def test_main_exits_one_and_still_prints_json_on_failure(monkeypatch, capsys):
    _creds(monkeypatch)
    _install_client(monkeypatch, error=requests.ConnectTimeout("timed out"))

    code = probe_mod.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["reason"] == "network"
    assert "Traceback" not in captured.out


def test_main_reads_mode_from_kis_mode_env(monkeypatch, capsys):
    _creds(monkeypatch, KIS_MODE="real")
    calls = _install_client(monkeypatch, balance=Balance())

    probe_mod.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "real"
    assert payload["base_url"] == BASE_URL_PROD
    assert calls == ["real"]


def test_main_emits_valid_json_even_if_probe_raises(monkeypatch, capsys):
    _creds(monkeypatch)

    def _explode(mode="paper"):
        raise RuntimeError("probe 내부 폭발")

    monkeypatch.setattr(probe_mod, "probe", _explode)

    code = probe_mod.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)   # 트레이스백이 섞였다면 여기서 깨진다
    assert code == 1
    assert set(payload) == CONTRACT_KEYS
    assert payload["reason"] == "unknown"
    assert "Traceback" in captured.err


def test_main_output_is_a_single_line_of_pure_ascii(monkeypatch, capsys):
    # 한글 detail이 섞여도 비-UTF8 로케일에서 깨지지 않도록 ASCII로만 직렬화한다.
    _creds(monkeypatch)
    _install_client(monkeypatch, error=ValueError("한글 오류 메시지"))

    probe_mod.main()

    out = capsys.readouterr().out
    assert out.count("\n") == 1
    out.encode("ascii")                  # UnicodeEncodeError면 계약 위반
    assert json.loads(out)["detail"] == "한글 오류 메시지"
