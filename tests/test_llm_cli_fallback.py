"""call_llm 의 CLI 우선순위 계약.

2026-08-13 실측: cashDeploy 8회 실행에서 claude -p 가 9회 전부 180초 타임아웃 후
pi 로 폴백했고, 같은 프롬프트를 pi 는 15~19초에 처리했다. claude 는 호출마다
사용자의 플러그인·MCP 스택을 부팅하는데 그 구간이 머신 상태에 따라 멈춘다.
그래서 pi 를 먼저 시도하고 claude 를 폴백으로 남긴다. 이 파일은 그 순서가
되돌아가지 않도록 고정한다.
"""
from __future__ import annotations

import pytest

from src.llm import cli_client


def _stub(monkeypatch, *, pi, claude):
    """(_try_pi, _try_claude) 를 대체하고 호출 순서를 기록한다."""
    order: list[str] = []

    def fake_pi(prompt, timeout):
        order.append("pi")
        return pi

    def fake_claude(prompt, timeout):
        order.append("claude")
        return claude

    monkeypatch.setattr(cli_client, "_try_pi", fake_pi)
    monkeypatch.setattr(cli_client, "_try_claude", fake_claude)
    return order


def test_pi_is_tried_first_and_short_circuits(monkeypatch):
    order = _stub(monkeypatch, pi=(True, '{"action":"SKIP"}', 15_000),
                  claude=(True, "SHOULD NOT BE USED", 1))
    out, src, ms = cli_client.call_llm("prompt")
    assert order == ["pi"], "pi 성공이면 claude 를 부르지 않는다"
    assert src == "pi"
    assert out == '{"action":"SKIP"}'
    assert ms == 15_000


def test_claude_is_the_fallback_when_pi_fails(monkeypatch):
    order = _stub(monkeypatch, pi=(False, "timeout", 180_000),
                  claude=(True, '{"action":"BUY"}', 38_000))
    out, src, ms = cli_client.call_llm("prompt")
    assert order == ["pi", "claude"]
    assert src == "claude"
    assert out == '{"action":"BUY"}'
    assert ms == 38_000


def test_both_failing_reports_unavailable_with_both_errors(monkeypatch):
    _stub(monkeypatch, pi=(False, "pi boom", 100), claude=(False, "claude boom", 200))
    out, src, ms = cli_client.call_llm("prompt")
    assert src == "unavailable"
    assert "pi boom" in out and "claude boom" in out
    assert ms == 300, "실패 시 경과시간은 두 시도의 합"


def test_empty_output_counts_as_failure_and_falls_through(monkeypatch):
    """경계 — _run 은 빈 stdout 을 실패로 돌려준다. 그 계약이 유지되는지."""
    order = _stub(monkeypatch, pi=(False, "empty stdout", 500),
                  claude=(True, "ok", 900))
    out, src, _ = cli_client.call_llm("prompt")
    assert order == ["pi", "claude"]
    assert (out, src) == ("ok", "claude")


def test_timeout_argument_reaches_both_clis(monkeypatch):
    seen: list[int] = []

    def rec_pi(prompt, timeout):
        seen.append(timeout)
        return (False, "x", 1)

    def rec_claude(prompt, timeout):
        seen.append(timeout)
        return (False, "y", 1)

    monkeypatch.setattr(cli_client, "_try_pi", rec_pi)
    monkeypatch.setattr(cli_client, "_try_claude", rec_claude)
    cli_client.call_llm("prompt", timeout=42)
    assert seen == [42, 42]


def test_default_entry_timeout_is_used_when_unspecified(monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(cli_client, "_try_pi",
                        lambda p, t: (seen.append(t), (True, "ok", 1))[1])
    monkeypatch.setattr(cli_client, "_try_claude",
                        lambda p, t: pytest.fail("claude 가 불려선 안 된다"))
    cli_client.call_llm("prompt")
    assert seen == [cli_client.DEFAULT_ENTRY_TIMEOUT]


def test_pi_invocation_keeps_isolation_flags():
    """pi 를 먼저 쓰는 근거가 격리 플래그다 — 빠지면 claude 와 같은 문제가 생긴다."""
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, timeout):
        captured["cmd"] = cmd
        return (True, "ok", 1)

    orig = cli_client._run
    cli_client._run = fake_run
    try:
        cli_client._try_pi("prompt", 30)
    finally:
        cli_client._run = orig

    cmd = captured.get("cmd")
    if cmd is None:
        pytest.skip("pi 바이너리가 없는 환경 — _try_pi 가 _run 까지 가지 않는다")
    for flag in ("--no-extensions", "--no-tools", "--no-skills", "--no-session"):
        assert flag in cmd, f"{flag} 누락 — pi 가 플러그인 스택을 타게 된다"
