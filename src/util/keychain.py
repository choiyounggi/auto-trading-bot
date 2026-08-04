"""macOS Keychain 조회 wrapper + KIS(한국투자증권)/Telegram 키 자동 inject."""
from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)


def keychain_get(service: str, account: str) -> str | None:
    """평문 값 조회. SSH non-interactive에선 ACL로 차단될 수 있음 (GUI/launchd OK)."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    except Exception as e:
        log.warning("keychain access 실패 (%s/%s): %s", service, account, e)
        return None


def load_kis_keys(mode: str | None = None) -> dict:
    """
    Keychain에서 KIS 키 → os.environ 자동 inject.

    Keychain service 이름 'kis-openapi' (2026-07-06 kiwoom-openapi에서 개명 —
    브로커는 처음부터 한투 KIS였고 kiwoom은 초기 kiwoom-mcp 시절 네이밍 잔재).
    account 10자리 = 앞 8자리(CANO) + 뒤 2자리(ACNT_PRDT_CD) 자동 분리.
    """
    mode = (mode or os.environ.get("KIS_MODE") or "paper").lower()
    loaded: dict = {}

    # app key / secret
    for env_var, ks_a in [
        ("KIS_APP_KEY",    f"{mode}-appkey"),
        ("KIS_APP_SECRET", f"{mode}-secret"),
    ]:
        if os.environ.get(env_var):
            loaded[env_var] = "already-set"
            continue
        v = keychain_get("kis-openapi", ks_a)
        if v:
            os.environ[env_var] = v
            loaded[env_var] = f"keychain ({len(v)} chars)"
        else:
            loaded[env_var] = "missing"

    # account 10자리 → 8 (CANO) + 2 (ACNT_PRDT_CD) 분리
    if not (os.environ.get("KIS_CANO") and os.environ.get("KIS_ACNT_PRDT_CD")):
        acct_full = keychain_get("kis-openapi", f"{mode}-account") or ""
        if len(acct_full) == 10:
            os.environ["KIS_CANO"] = acct_full[:8]
            os.environ["KIS_ACNT_PRDT_CD"] = acct_full[8:]
            loaded["KIS_CANO"] = f"keychain (8 chars, split from {len(acct_full)})"
            loaded["KIS_ACNT_PRDT_CD"] = f"keychain (2 chars, split)"
        elif acct_full:
            os.environ["KIS_CANO"] = acct_full
            loaded["KIS_CANO"] = f"keychain ({len(acct_full)} chars — not 10!)"
            loaded["KIS_ACNT_PRDT_CD"] = "default 01"
            os.environ.setdefault("KIS_ACNT_PRDT_CD", "01")
        else:
            loaded["KIS_CANO"] = "missing"
            loaded["KIS_ACNT_PRDT_CD"] = "missing"

    os.environ.setdefault("KIS_MODE", mode)
    return loaded


def load_telegram_keys() -> dict:
    """Keychain에서 Telegram 토큰 → os.environ 자동 inject."""
    loaded: dict = {}
    mapping = {
        "TELEGRAM_BOT_TOKEN": ("telegram-bot", "stock-trader"),
        "TELEGRAM_CHAT_ID":   ("telegram-bot", "stock-trader-chatid"),
    }
    for env_var, (svc, acct) in mapping.items():
        if os.environ.get(env_var):
            loaded[env_var] = "already-set"
            continue
        v = keychain_get(svc, acct)
        if v:
            os.environ[env_var] = v
            loaded[env_var] = f"keychain ({len(v)} chars)"
        else:
            loaded[env_var] = "missing"
    return loaded
