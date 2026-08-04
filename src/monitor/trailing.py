"""Trailing stop 활성 + 갱신."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def maybe_activate_trailing(position, rules) -> bool:
    """TP raise 횟수가 임계 도달 시 trailing 자동 활성."""
    if position.trailing_active:
        return False
    if position.tp_raised_count >= rules.max_tp_raises:
        position.trailing_active = 1
        log.info("trailing stop 자동 활성: %s (TP raise %d회)",
                 position.ticker, position.tp_raised_count)
        return True
    return False


def update_trailing_high(position, current_price: int) -> None:
    """현재가가 trailing_high 갱신 시 update."""
    if not position.trailing_active:
        return
    new_high = max(position.trailing_high or 0, current_price)
    if new_high > (position.trailing_high or 0):
        position.trailing_high = new_high
