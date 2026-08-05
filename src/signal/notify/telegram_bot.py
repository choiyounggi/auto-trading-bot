"""텔레그램 메시지 송신."""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


def _token_chat() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 미설정")
    if not chat:
        raise RuntimeError("TELEGRAM_CHAT_ID 미설정 — scripts/bootstrap_chat_id.py 실행")
    return token, chat


def send_message(text: str, parse_mode: Optional[str] = "Markdown") -> dict:
    token, chat = _token_chat()
    url = f"{API_BASE}/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"telegram error: {data}")
    return data


def get_updates(token: Optional[str] = None) -> list[dict]:
    token = token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 미설정")
    r = requests.get(f"{API_BASE}/bot{token}/getUpdates", timeout=15)
    r.raise_for_status()
    return r.json().get("result", [])
