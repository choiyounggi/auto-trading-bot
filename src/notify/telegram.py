"""Telegram 봇 알림 wrapper. Keychain에서 토큰/Chat ID 조회. 토큰 없으면 noop."""
from __future__ import annotations

import logging
import os

import requests

from src.util.keychain import keychain_get

log = logging.getLogger(__name__)

SEVERITY_PREFIX = {
    "critical": "🚨",
    "warning": "⚠️",
    "info": "📊",
    "daily": "📈",
    "debug": "🔧",
}


def _get_credentials() -> tuple[str | None, str | None]:
    # env override 우선
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or keychain_get("telegram-bot", "stock-trader")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or keychain_get("telegram-bot", "stock-trader-chatid")
    return token, chat_id


def send(message: str, severity: str = "info", parse_mode: str = "Markdown") -> bool:
    """
    Returns True if sent. Token/Chat ID 없으면 log only (noop).

    본문의 `_` `[` `*` 등이 Markdown entity로 오파싱되어 400(can't parse entities)이
    나면 parse_mode 없이(plain text) 1회 재전송한다 — 알림 유실 방지.
    """
    token, chat_id = _get_credentials()
    prefix = SEVERITY_PREFIX.get(severity, "")
    body = f"{prefix} {message}" if prefix else message

    if not token or not chat_id:
        log.info("[telegram noop] %s", body[:200])
        return False

    try:
        r = _post_message(token, chat_id, body, parse_mode)
        if r.status_code != 200:
            if parse_mode and r.status_code == 400 and "can't parse entities" in r.text:
                log.warning("telegram Markdown 파싱 실패 → plain text 재전송: %s", r.text[:200])
                r = _post_message(token, chat_id, body, parse_mode=None)
                if r.status_code == 200:
                    log.info("[telegram sent/plain] severity=%s len=%d", severity, len(body))
                    return True
            log.warning("telegram send 실패: %d %s", r.status_code, r.text[:300])
            return False
        log.info("[telegram sent] severity=%s len=%d", severity, len(body))
        return True
    except Exception as e:
        log.warning("telegram send 예외: %s", e)
        return False


def _post_message(token: str, chat_id: str, body: str, parse_mode: str | None):
    payload = {
        "chat_id": chat_id,
        "text": body,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=10,
    )


def send_critical(message: str) -> bool:
    return send(message, severity="critical")


def send_warning(message: str) -> bool:
    return send(message, severity="warning")


def send_info(message: str) -> bool:
    return send(message, severity="info")


def send_daily(message: str) -> bool:
    return send(message, severity="daily")
