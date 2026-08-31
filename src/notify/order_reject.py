# -*- coding: utf-8 -*-
"""주문 거부 알림 차단기 — 같은 경고 문구는 하루 한 번만 텔레그램으로 보낸다.

2026-08-28 모의투자 계좌가 주문 권한을 잃자(`모의투자 주문이 불가한 계좌입니다`)
monitor 는 5분마다, cash-deploy 는 30분마다 같은 주문을 재시도했고 거부 한 건마다
경고가 나갔다. 3일 만에 글자까지 똑같은 알림이 151건 쌓여 정작 봐야 할 알림을 덮었다.

억제 기준을 KIS 거부 메시지 목록(화이트리스트)이 아니라 **문구의 반복**으로 잡는다.
어떤 사유가 재시도로 풀리고 어떤 사유가 안 풀리는지 우리가 다 알 수 없지만, 하루
종일 글자 그대로 반복되는 경고는 사유가 무엇이든 첫 건만 보면 충분하기 때문이다.
문구에 종목명이 들어가 있으므로 종목별로는 각각 한 번씩 알림이 간다.

주문 재시도 자체는 막지 않는다 — 장중에 계좌를 복구하면 다음 틱부터 바로 주문이
나가야 한다. 억제되는 것은 텔레그램 알림뿐이고, 거부는 매 건 로그에 남는다.

상태 파일은 monitor / orchestrator / cash-deploy / dip-buy 가 서로 다른 프로세스에서
갱신하므로 flock 으로 직렬화한다 (kis_client._throttle_shared 와 같은 이유).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# cash_deploy._WARN_MARKER 와 같은 규약 — launchd 잡의 WorkingDirectory 기준 상대경로.
STATE_PATH = Path("data/logs/.order_reject_state.json")

_KEY_MAX_CHARS = 200        # 사유 문구가 길어도 상태 파일이 불어나지 않게 자른다
_STATE_MAX_BYTES = 262_144  # 손상된 거대 파일을 통째로 읽지 않기 위한 상한
_EMPTY_KEY = "(문구 없음)"
_SUPPRESS_NOTE = "\n(같은 문구는 오늘 더 알리지 않아 — 반복 여부는 로그에서 확인)"


def _reject_key(message: str | None) -> str:
    key = (message or "").strip()
    return key[:_KEY_MAX_CHARS] if key else _EMPTY_KEY


def _read_state(fd: int, today: str) -> dict:
    """빈 파일 / 손상 / 날짜 변경은 모두 '오늘 첫 거부'로 취급한다."""
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, _STATE_MAX_BYTES).decode("utf-8", "replace").strip()
    try:
        state = json.loads(raw) if raw else {}
    except ValueError:
        state = {}
    if not isinstance(state, dict) or state.get("date") != today:
        return {"date": today, "counts": {}}
    counts = state.get("counts")
    return {"date": today, "counts": counts if isinstance(counts, dict) else {}}


def _write_state(fd: int, state: dict) -> None:
    body = json.dumps(state, ensure_ascii=False).encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, body)
    os.ftruncate(fd, len(body))


def record_reject(message: str | None, today: str, state_path: Path | None = None) -> int:
    """오늘 이 문구가 몇 번째 거부인지(1부터) 돌려준다. 날짜가 바뀌면 1부터 다시 센다.

    상태를 읽거나 쓸 수 없으면 1 을 돌려준다 — 알림을 놓치는 것보다 중복이 낫다
    (cash_deploy.should_warn_underrun 과 같은 규약).

    state_path 기본값을 기본 인자에 두지 않는 이유: 기본 인자는 def 시점에 바인딩돼
    테스트의 monkeypatch(STATE_PATH)가 반영되지 않는다 (cash_deploy._WARN_MARKER 선례).
    """
    state_path = state_path or STATE_PATH
    key = _reject_key(message)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(state_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        log.warning("주문 거부 상태 파일 열기 실패 (%s) — 알림 억제 없이 진행", e)
        return 1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        state = _read_state(fd, today)
        count = int(state["counts"].get(key, 0)) + 1
        state["counts"][key] = count
        _write_state(fd, state)
        return count
    except (OSError, TypeError, ValueError) as e:
        log.warning("주문 거부 상태 갱신 실패 (%s) — 알림 억제 없이 진행", e)
        return 1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def should_notify_reject(message: str | None, today: str,
                         state_path: Path | None = None) -> bool:
    """이 거부를 텔레그램으로 올릴지. 오늘 같은 문구의 첫 건만 True."""
    return record_reject(message, today, state_path) == 1


def warn_order_reject(message: str, send_warning: Callable[[str], Any], today: str,
                      state_path: Path | None = None) -> bool:
    """거부 경고를 보낸다. 오늘 이미 같은 문구를 보냈으면 로그만 남기고 False.

    첫 알림에는 억제 안내를 덧붙인다 — 안 붙이면 '한 번만 났다'로 읽힌다.
    """
    if should_notify_reject(message, today, state_path):
        send_warning(f"{message}{_SUPPRESS_NOTE}")
        return True
    log.warning("%s — 오늘 같은 문구 반복, 텔레그램 알림 억제", message or _EMPTY_KEY)
    return False
