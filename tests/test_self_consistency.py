"""Self-consistency 진입 다수결 테스트."""
from __future__ import annotations

from src.llm.self_consistency import vote_entry


def _fake_llm(outputs: list[str]):
    calls = {"i": 0}

    def _call(prompt: str, timeout: int):
        idx = calls["i"]
        calls["i"] += 1
        return outputs[idx], "fake", 1

    return _call


def _buy_json(stop_loss: float, take_profit: float = 4.0, confidence: int = 9) -> str:
    return (
        '{"action":"BUY","entry_strategy":"MARKET_OPEN","entry_price":100000,'
        f'"size_pct":3.0,"stop_loss_pct":{stop_loss},"take_profit_pct":{take_profit},'
        f'"max_hold_days":5,"confidence":{confidence},"key_thesis":"test"}}'
    )


def test_buy_majority_uses_tightest_stop_loss():
    decision, trace = vote_entry(
        "prompt",
        n=3,
        llm_fn=_fake_llm([
            _buy_json(stop_loss=3.0, take_profit=6.0),
            _buy_json(stop_loss=1.5, take_profit=4.0),
            _buy_json(stop_loss=2.0, take_profit=5.0),
        ]),
    )

    assert decision.action == "BUY"
    assert decision.stop_loss_pct == 1.5
    assert decision.take_profit_pct == 4.0
    assert len(trace) == 3


def test_skip_majority_returns_skip():
    decision, trace = vote_entry(
        "prompt",
        n=3,
        llm_fn=_fake_llm([
            _buy_json(stop_loss=2.0),
            '{"action":"SKIP","confidence":4}',
            '{"action":"SKIP","confidence":5}',
        ]),
    )

    assert decision.action == "SKIP"
    assert len(trace) == 3


def test_all_parse_fail_returns_skip():
    decision, trace = vote_entry(
        "prompt",
        n=3,
        llm_fn=_fake_llm(["not json", "", "```oops```"]),
    )

    assert decision.action == "SKIP"
    assert all(t["parse_error"] for t in trace)
