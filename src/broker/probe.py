# -*- coding: utf-8 -*-
"""KIS 연결성 프로브 — TS doctor가 파싱하는 고정 JSON 계약 (D18).

    python -m src.broker.probe

Keychain에 이미 들어있는 자격증명으로 KIS 왕복 1회(잔고 조회)를 수행하고
stdout에 JSON 객체 한 줄만 출력한다. 종료 코드는 ok면 0, 아니면 1.

자격증명은 이 프로세스 밖으로 나가지 않는다: 앱키/앱시크리트/전체 CANO는
JSON의 어떤 필드에도, stderr 트레이스백에도 들어가지 않는다
(WIKI security-secrets-secrets-in-code — "Printed to logs / CI output" 행).

호출자가 JSON.parse 하므로 stdout은 어떤 실패에서도 유효한 JSON이어야 한다.
트레이스백은 전부 stderr로만 나간다.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

import requests

from src.broker.kis_client import BASE_URL_PAPER, BASE_URL_PROD, KisClient
from src.util.keychain import load_kis_keys

DETAIL_MAX_CHARS = 200
REDACTED = "***"
_MIN_REDACT_LEN = 4          # 이보다 짧은 값을 지우면 무관한 텍스트까지 뭉갠다

_RATE_LIMIT_MARKER = "초당 거래건수"
_AUTH_FAILURE_MARKER = "token 발급 실패"   # KisClient._get_token 이 HTTP != 200 에 raise


def _normalize_mode(mode: str | None) -> str:
    """계약상 mode는 paper|real 둘뿐 — KisClient._base_url과 동일하게 판정."""
    return "real" if (mode or "").strip().lower() == "real" else "paper"


def _base_url(mode: str) -> str:
    return BASE_URL_PROD if mode == "real" else BASE_URL_PAPER


def _mask_cano(cano: str) -> str:
    """뒤 4자리만 노출. 4자리 이하면 전체가 노출되므로 전부 가린다."""
    if not cano:
        return ""
    if len(cano) <= 4:
        return "****"
    return "****" + cano[-4:]


def _redact(text: str, secrets: list[str]) -> str:
    """앱키/시크리트/CANO 원문이 섞여 들어왔으면 제거한다 (긴 값부터)."""
    for secret in sorted({s for s in secrets if s and len(s) >= _MIN_REDACT_LEN}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text


def _detail(text: str, secrets: list[str]) -> str:
    """사람이 읽는 텍스트 — 시크릿 제거 후 200자로 자른다 (자른 뒤 재유입 불가)."""
    return _redact(text, secrets)[:DETAIL_MAX_CHARS]


def _result(ok: bool, mode: str, cano_masked: str, reason: str, detail: str) -> dict:
    """D18 계약 — 키는 정확히 이 6개."""
    return {
        "ok": ok,
        "mode": mode,
        "base_url": _base_url(mode),
        "cano_masked": cano_masked,
        "reason": reason,
        "detail": detail,
    }


def _classify(text: str) -> str:
    if _RATE_LIMIT_MARKER in text:
        return "rate_limited"
    if _AUTH_FAILURE_MARKER in text:
        return "auth_failed"
    return "unknown"


def _log_traceback(secrets: list[str]) -> None:
    """진단용 트레이스백은 stderr로만 — stdout은 JSON 전용이다."""
    sys.stderr.write(_redact(traceback.format_exc(), secrets))


def probe(mode: str = "paper") -> dict:
    """KIS 왕복 1회를 시도하고 D18 계약 dict를 돌려준다. 절대 raise하지 않는다."""
    resolved = _normalize_mode(mode)
    secrets: list[str] = []
    cano_masked = ""
    try:
        load_kis_keys(resolved)
        app_key = os.environ.get("KIS_APP_KEY", "")
        app_secret = os.environ.get("KIS_APP_SECRET", "")
        cano = os.environ.get("KIS_CANO", "")
        secrets = [app_key, app_secret, cano]
        cano_masked = _mask_cano(cano)

        missing = [
            name for name, value in (
                ("KIS_APP_KEY", app_key),
                ("KIS_APP_SECRET", app_secret),
                ("KIS_CANO", cano),
            ) if not value
        ]
        if missing:
            return _result(False, resolved, cano_masked, "missing_credentials",
                           "자격증명 누락: " + ", ".join(missing))

        # 아웃바운드 호출의 타임아웃은 KisClient가 매 requests 호출에 timeout=10으로
        # 명시한다 (WIKI backend-common-reliability-timeouts-and-retries 규칙 1).
        # 프로브는 왕복 1회만 하므로 자체 재시도는 두지 않는다.
        balance = KisClient(mode=resolved).get_balance()
        if balance is None:
            return _result(False, resolved, cano_masked, "rejected",
                           "잔고 조회가 응답을 반환하지 않았습니다 (rt_cd != 0 또는 네트워크 오류)")
        return _result(True, resolved, cano_masked, "", "잔고 조회 성공")

    except requests.RequestException as exc:
        _log_traceback(secrets)
        return _result(False, resolved, cano_masked, "network",
                       _detail(str(exc) or type(exc).__name__, secrets))
    except Exception as exc:  # noqa: BLE001 — stdout JSON 계약이 예외보다 우선한다
        _log_traceback(secrets)
        text = str(exc) or type(exc).__name__
        return _result(False, resolved, cano_masked, _classify(text), _detail(text, secrets))


def main() -> int:
    """JSON 한 줄을 stdout에 출력하고 종료 코드를 돌려준다 (ok=0, 그 외=1)."""
    mode = os.environ.get("KIS_MODE") or "paper"
    try:
        result = probe(mode)
    except BaseException as exc:  # noqa: BLE001 — probe는 raise하지 않지만 계약을 최후 방어
        traceback.print_exc(file=sys.stderr)
        result = _result(False, _normalize_mode(mode), _mask_cano(os.environ.get("KIS_CANO", "")),
                         "unknown", f"probe internal error: {type(exc).__name__}")
    # ensure_ascii=True — 비-UTF8 로케일에서도 stdout 인코딩이 실패하지 않게 한다.
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
