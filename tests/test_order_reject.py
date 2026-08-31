# -*- coding: utf-8 -*-
"""주문 거부 알림 차단기 (src/notify/order_reject.py) 테스트.

파일 IO 만 하는 순수 모듈이라 KIS/DB/텔레그램을 전혀 건드리지 않는다.
상태 파일은 전부 tmp_path 로 명시 주입한다.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from src.notify.order_reject import (
    STATE_PATH,
    record_reject,
    should_notify_reject,
    warn_order_reject,
)

MSG = "청산 주문 거부 삼성바이오로직스: 모의투자 주문이 불가한 계좌입니다."
TODAY = "2026-08-31"


@pytest.fixture
def state(tmp_path):
    return tmp_path / "logs" / "state.json"


class _Spy:
    """send_warning 대역 — 보낸 문구를 순서대로 모은다."""

    def __init__(self):
        self.sent: list[str] = []

    def __call__(self, message: str) -> None:
        self.sent.append(message)


# ---------------------------------------------------------------------------
# 정상 경로
# ---------------------------------------------------------------------------

def test_first_reject_notifies_and_repeats_are_suppressed(state):
    spy = _Spy()
    assert warn_order_reject(MSG, spy, TODAY, state_path=state) is True
    for _ in range(9):
        assert warn_order_reject(MSG, spy, TODAY, state_path=state) is False
    assert len(spy.sent) == 1
    assert spy.sent[0].startswith(MSG)
    assert "오늘 더 알리지 않아" in spy.sent[0]


def test_record_reject_counts_every_occurrence(state):
    counts = [record_reject(MSG, TODAY, state_path=state) for _ in range(3)]
    assert counts == [1, 2, 3]
    assert should_notify_reject(MSG, TODAY, state_path=state) is False


def test_different_messages_each_notify_once(state):
    spy = _Spy()
    other = "재배치 주문 거부 018260 삼성에스디에스: 모의투자 주문이 불가한 계좌입니다."
    assert warn_order_reject(MSG, spy, TODAY, state_path=state) is True
    assert warn_order_reject(other, spy, TODAY, state_path=state) is True
    assert warn_order_reject(MSG, spy, TODAY, state_path=state) is False
    assert warn_order_reject(other, spy, TODAY, state_path=state) is False
    assert len(spy.sent) == 2


def test_state_file_records_date_and_counts(state):
    record_reject(MSG, TODAY, state_path=state)
    record_reject(MSG, TODAY, state_path=state)
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["date"] == TODAY
    assert saved["counts"][MSG] == 2


# ---------------------------------------------------------------------------
# 경계값
# ---------------------------------------------------------------------------

def test_new_day_resets_suppression(state):
    spy = _Spy()
    assert warn_order_reject(MSG, spy, "2026-08-31", state_path=state) is True
    assert warn_order_reject(MSG, spy, "2026-08-31", state_path=state) is False
    # 날짜가 바뀌면 다시 한 번 알린다 — 장애가 이어지고 있다는 사실을 매일 알아야 한다.
    assert warn_order_reject(MSG, spy, "2026-09-01", state_path=state) is True
    assert len(spy.sent) == 2
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["date"] == "2026-09-01"
    assert saved["counts"] == {MSG: 1}


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_empty_message_is_still_deduped_under_one_key(state, empty):
    spy = _Spy()
    assert warn_order_reject(empty, spy, TODAY, state_path=state) is True
    assert warn_order_reject(empty, spy, TODAY, state_path=state) is False
    assert len(spy.sent) == 1


def test_long_message_is_truncated_to_a_bounded_key(state):
    long_msg = "청산 주문 거부 " + "가" * 5000
    record_reject(long_msg, TODAY, state_path=state)
    (key,) = json.loads(state.read_text(encoding="utf-8"))["counts"]
    assert len(key) == 200


def test_messages_sharing_a_200_char_prefix_collapse_into_one_key(state):
    """자른 키가 같으면 같은 사유로 본다 — 상태 파일이 무한히 불어나지 않게 하는 대가."""
    base = "가" * 250
    assert record_reject(base + "A", TODAY, state_path=state) == 1
    assert record_reject(base + "B", TODAY, state_path=state) == 2


def test_creates_missing_parent_directory(state):
    assert not state.parent.exists()
    assert record_reject(MSG, TODAY, state_path=state) == 1
    assert state.exists()


# ---------------------------------------------------------------------------
# 에러 경로 — 어떤 실패도 알림을 삼키지 않는다
# ---------------------------------------------------------------------------

def test_corrupt_state_file_is_treated_as_first_reject(state):
    state.parent.mkdir(parents=True)
    state.write_text("{이건 JSON 이 아니다", encoding="utf-8")
    spy = _Spy()
    assert warn_order_reject(MSG, spy, TODAY, state_path=state) is True
    assert len(spy.sent) == 1
    # 손상 파일은 오늘 상태로 덮여 이후 억제가 정상 동작한다.
    assert warn_order_reject(MSG, spy, TODAY, state_path=state) is False


@pytest.mark.parametrize("junk", ['["리스트다"]', '{"date": "2026-08-31", "counts": 7}', ""])
def test_unexpected_state_shapes_are_treated_as_first_reject(state, junk):
    state.parent.mkdir(parents=True)
    state.write_text(junk, encoding="utf-8")
    assert record_reject(MSG, TODAY, state_path=state) == 1


def test_unwritable_directory_notifies_rather_than_swallowing(tmp_path):
    """상태를 못 쓰면 중복 알림이 나더라도 알린다 — 침묵이 더 나쁘다."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)   # 읽기/탐색만, 쓰기 불가
    try:
        target = locked / "sub" / "state.json"
        spy = _Spy()
        assert warn_order_reject(MSG, spy, TODAY, state_path=target) is True
        assert warn_order_reject(MSG, spy, TODAY, state_path=target) is True
        assert len(spy.sent) == 2
    finally:
        locked.chmod(stat.S_IRWXU)


def test_state_path_default_points_at_the_launchd_log_dir():
    """기본 경로는 launchd WorkingDirectory 기준 상대경로여야 한다
    (cash_deploy._WARN_MARKER 와 같은 규약)."""
    assert not STATE_PATH.is_absolute()
    assert STATE_PATH.parent.as_posix() == "data/logs"
