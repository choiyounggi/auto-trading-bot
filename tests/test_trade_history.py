"""Repo.get_closed_positions / get_history_summary — 청산 내역 조회와 승률·누적손익 요약.
+ 텔레그램 `/history` 순수 포맷 함수 `_history_text` 와 `Agent._cmd_history` 라우팅."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from src.agent.telegram_agent import Agent, Telegram, _history_text
from src.storage.models import Position, create_all
from src.storage.repository import ClosedTrade, HistorySummary, Repo


def _repo(tmp_path) -> Repo:
    repo = Repo(tmp_path / "trades.sqlite")
    create_all(repo.engine)
    return repo


def _insert(repo: Repo, **kwargs) -> int:
    """CLOSED/OPEN/PENDING 포지션을 필드 정밀 제어를 위해 직접 넣는다
    (close_position은 pnl_won/exit_at을 자동 계산해 값을 마음대로 못 정한다)."""
    defaults = dict(
        ticker="005930",
        name="삼성전자",
        status="CLOSED",
        entry_price_target=100_000,
        entry_price_actual=100_000,
        entry_qty=10,
        entry_at=datetime(2026, 8, 1, 9, 0, 0),
        exit_reason="TAKE_PROFIT",
        exit_price=110_000,
        exit_at=datetime(2026, 8, 1, 15, 0, 0),
        pnl_won=1000,
        pnl_pct=1.0,
    )
    defaults.update(kwargs)
    with Session(repo.engine) as s:
        pos = Position(**defaults)
        s.add(pos)
        s.commit()
        return pos.id


def test_get_closed_positions_returns_recent_first_with_matching_fields(tmp_path):
    repo = _repo(tmp_path)
    # 일부러 시간 역순으로 삽입해 정렬이 exit_at 기준임을 확인한다.
    _insert(repo, ticker="A", pnl_won=200, exit_at=datetime(2026, 8, 1, 10, 0))
    _insert(repo, ticker="B", pnl_won=1000, exit_at=datetime(2026, 8, 3, 10, 0))
    _insert(repo, ticker="C", pnl_won=-500, exit_at=datetime(2026, 8, 2, 10, 0))

    trades = repo.get_closed_positions(10)
    assert [t.ticker for t in trades] == ["B", "C", "A"]
    assert all(isinstance(t, ClosedTrade) for t in trades)
    assert trades[0].pnl_won == 1000


def test_get_history_summary_win_rate_and_total(tmp_path):
    repo = _repo(tmp_path)
    _insert(repo, pnl_won=1000)
    _insert(repo, pnl_won=-500)
    _insert(repo, pnl_won=200)

    summary = repo.get_history_summary(10)
    assert isinstance(summary, HistorySummary)
    assert summary.trades == 3
    assert summary.wins == 2
    assert summary.losses == 1
    assert abs(summary.win_rate_pct - 66.67) < 0.01
    assert summary.total_pnl_won == 700


def test_open_and_pending_positions_excluded(tmp_path):
    repo = _repo(tmp_path)
    _insert(repo, pnl_won=1000)
    _insert(repo, pnl_won=-500)
    _insert(repo, pnl_won=200)
    _insert(repo, status="OPEN", exit_at=None, exit_price=None, pnl_won=None, pnl_pct=None)
    _insert(repo, status="PENDING", exit_at=None, exit_price=None, pnl_won=None, pnl_pct=None)

    trades = repo.get_closed_positions(10)
    summary = repo.get_history_summary(10)
    assert len(trades) == 3
    assert summary.trades == 3


def test_zero_pnl_is_neither_win_nor_loss(tmp_path):
    repo = _repo(tmp_path)
    _insert(repo, pnl_won=1000)
    _insert(repo, pnl_won=-500)
    _insert(repo, pnl_won=200)
    _insert(repo, pnl_won=0)

    summary = repo.get_history_summary(10)
    assert summary.trades == 4
    assert summary.wins == 2
    assert summary.losses == 1
    assert summary.wins + summary.losses < summary.trades
    assert summary.win_rate_pct == 50.0


def test_empty_db_summary_has_no_zero_division(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get_closed_positions() == []
    summary = repo.get_history_summary()
    assert summary.trades == 0
    assert summary.win_rate_pct == 0.0
    assert summary.total_pnl_won == 0


def test_limit_clamps_to_lower_bound_of_one(tmp_path):
    repo = _repo(tmp_path)
    _insert(repo)
    assert len(repo.get_closed_positions(0)) == 1
    assert len(repo.get_closed_positions(-5)) == 1


def test_limit_clamps_to_upper_bound_of_fifty(tmp_path):
    repo = _repo(tmp_path)
    for i in range(60):
        _insert(repo, ticker=f"T{i:02d}", exit_at=datetime(2026, 8, 1, 0, i))
    assert len(repo.get_closed_positions(999)) == 50


def test_null_fields_do_not_raise_and_fall_back(tmp_path):
    repo = _repo(tmp_path)
    _insert(
        repo,
        pnl_won=None,
        pnl_pct=None,
        exit_at=None,
        entry_price_actual=None,
        entry_price_target=95_000,
        entry_qty=None,
    )
    trades = repo.get_closed_positions(10)
    assert len(trades) == 1
    t = trades[0]
    assert t.pnl_won == 0
    assert t.pnl_pct == 0.0
    assert t.entry_price == 95_000
    assert t.exit_at is None
    assert t.qty == 0


def test_summary_and_list_agree_on_total_pnl(tmp_path):
    repo = _repo(tmp_path)
    for i in range(60):
        _insert(repo, ticker=f"T{i:02d}", pnl_won=(i - 30) * 10, exit_at=datetime(2026, 8, 1, 0, i))

    summary = repo.get_history_summary(5)
    trades = repo.get_closed_positions(5)
    assert summary.total_pnl_won == sum(t.pnl_won for t in trades)


# ============================================================
# _history_text — 순수 포맷 함수 (네트워크/DB 없이 테스트)
# ============================================================

def _trade(**kwargs) -> ClosedTrade:
    defaults = dict(
        id=1,
        ticker="005930",
        name="삼성전자",
        qty=6,
        entry_price=243_000,
        exit_price=253_750,
        pnl_won=64_500,
        pnl_pct=4.42,
        exit_reason="TAKE_PROFIT",
        entry_at=datetime(2026, 8, 10, 9, 0),
        exit_at=datetime(2026, 8, 12, 15, 0),
        strategy_id="MOMENTUM",
    )
    defaults.update(kwargs)
    return ClosedTrade(**defaults)


def test_history_text_contains_required_fields_for_two_trades():
    trades = [
        _trade(pnl_won=64_500, pnl_pct=4.42),
        _trade(ticker="035720", name="카카오", qty=35, pnl_won=-14_000, pnl_pct=-0.99,
               exit_reason="STOP_LOSS"),
    ]
    summary = HistorySummary(trades=2, wins=1, losses=1, win_rate_pct=66.7, total_pnl_won=50_500)
    text = _history_text(trades, summary)
    assert "005930" in text
    assert "삼성전자" in text
    assert "+64,500원" in text
    assert "-14,000원" in text
    assert "승 1 / 패 1" in text
    # win_rate_pct는 요약 그대로 렌더(반올림 표기 확인용 별도 케이스는 아래)
    assert "66.7%" in text


def test_history_text_shows_name_qty_entry_exit_price():
    trades = [_trade()]
    summary = HistorySummary(trades=1, wins=1, losses=0, win_rate_pct=100.0, total_pnl_won=64_500)
    text = _history_text(trades, summary)
    assert "삼성전자" in text
    assert "6주" in text
    assert "243,000" in text
    assert "253,750" in text


def test_history_text_empty_list_has_no_summary_block():
    text = _history_text([], HistorySummary(0, 0, 0, 0.0, 0))
    assert "청산된 거래가 아직 없어" in text
    assert "승률" not in text


def test_history_text_null_exit_at_and_reason_render_dash():
    trades = [_trade(exit_at=None, exit_reason="")]
    summary = HistorySummary(trades=1, wins=1, losses=0, win_rate_pct=100.0, total_pnl_won=64_500)
    text = _history_text(trades, summary)
    assert "-" in text  # 날짜 자리


def test_history_text_zero_pnl_renders_without_exception():
    trades = [_trade(pnl_won=0, pnl_pct=0.0)]
    summary = HistorySummary(trades=1, wins=0, losses=0, win_rate_pct=0.0, total_pnl_won=0)
    text = _history_text(trades, summary)
    assert "+0원" in text


def test_history_text_fifty_trades_under_telegram_limit():
    trades = [_trade(id=i, ticker=f"{i:06d}") for i in range(50)]
    summary = HistorySummary(trades=50, wins=25, losses=25, win_rate_pct=50.0, total_pnl_won=0)
    text = _history_text(trades, summary)
    assert len(text) <= 4096


def test_history_text_fifty_trades_with_real_kosdaq_style_name_under_limit():
    """운영 DB 실제 최장 이름 사례: 'DIP KOSDAQ 229200'(17자) — 리뷰 r1에서
    이 이름으로 50건을 렌더하면 4,714자로 4096을 넘었다."""
    trades = [_trade(id=i, ticker=f"{i:06d}", name="DIP KOSDAQ 229200") for i in range(50)]
    summary = HistorySummary(trades=50, wins=25, losses=25, win_rate_pct=50.0, total_pnl_won=0)
    text = _history_text(trades, summary)
    assert len(text) <= 4096


def test_history_text_fifty_trades_with_real_long_korean_name_under_limit():
    """운영 DB 실재 이름 '삼성바이오로직스'(8자) — 리뷰 r1에서 4,164자로 초과했다."""
    trades = [_trade(id=i, ticker=f"{i:06d}", name="삼성바이오로직스") for i in range(50)]
    summary = HistorySummary(trades=50, wins=25, losses=25, win_rate_pct=50.0, total_pnl_won=0)
    text = _history_text(trades, summary)
    assert len(text) <= 4096


def test_history_text_truncation_keeps_summary_block_intact():
    trades = [_trade(id=i, ticker=f"{i:06d}", name="DIP KOSDAQ 229200") for i in range(50)]
    summary = HistorySummary(trades=50, wins=25, losses=25, win_rate_pct=50.0, total_pnl_won=123_456)
    text = _history_text(trades, summary)
    assert "누적손익" in text
    assert "+123,456원" in text
    assert "승 25 / 패 25" in text


def test_history_text_truncation_notice_present_when_cut():
    trades = [_trade(id=i, ticker=f"{i:06d}", name="DIP KOSDAQ 229200") for i in range(50)]
    summary = HistorySummary(trades=50, wins=25, losses=25, win_rate_pct=50.0, total_pnl_won=0)
    text = _history_text(trades, summary)
    assert "길이 제한" in text


def test_history_text_no_truncation_notice_when_it_fits():
    trades = [_trade(id=i, ticker=f"{i:06d}") for i in range(3)]
    summary = HistorySummary(trades=3, wins=2, losses=1, win_rate_pct=66.7, total_pnl_won=700)
    text = _history_text(trades, summary)
    assert "길이 제한" not in text


def test_history_text_exit_reason_has_no_italic_markdown_underscore():
    """운영 DB의 exit_reason(STOP_LOSS/TAKE_PROFIT/LLM_CLOSE)은 전부 언더스코어를
    포함한다. `_..._`로 감싸면 텔레그램 legacy Markdown 파싱이 깨진다."""
    trades = [_trade(exit_reason="STOP_LOSS")]
    summary = HistorySummary(trades=1, wins=0, losses=1, win_rate_pct=0.0, total_pnl_won=-1000)
    text = _history_text(trades, summary)
    assert "_" not in text


def test_history_text_empty_exit_reason_placeholder_has_no_underscore():
    trades = [_trade(exit_reason="")]
    summary = HistorySummary(trades=1, wins=1, losses=0, win_rate_pct=100.0, total_pnl_won=64_500)
    text = _history_text(trades, summary)
    assert "_" not in text


# ============================================================
# Agent._cmd_history — 라우팅/인자 파싱/예외 처리 (가짜 tg/repo 주입)
# ============================================================

class _StubTg:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str, reply_markup: dict | None = None) -> None:
        self.sent.append(text)


class _StubRepoOk:
    def __init__(self):
        self.calls: list[int] = []

    def get_closed_positions(self, limit: int = 10):
        self.calls.append(limit)
        return [_trade()]

    def get_history_summary(self, limit: int = 10):
        self.calls.append(limit)
        return HistorySummary(1, 1, 0, 100.0, 64_500)


class _StubRepoRaises:
    def get_closed_positions(self, limit: int = 10):
        raise RuntimeError("boom")

    def get_history_summary(self, limit: int = 10):
        raise RuntimeError("boom")


def _agent_with_stubs(tg, repo) -> Agent:
    agent = Agent.__new__(Agent)
    agent.tg = tg
    agent.repo = repo
    return agent


def test_cmd_history_invalid_arg_sends_guidance_and_does_not_call_repo():
    tg = _StubTg()
    repo = _StubRepoOk()
    agent = _agent_with_stubs(tg, repo)

    agent._cmd_history("/history abc")

    assert any("형식:" in m for m in tg.sent)
    assert repo.calls == []


def test_cmd_history_repo_exception_sends_warning_and_does_not_raise():
    tg = _StubTg()
    repo = _StubRepoRaises()
    agent = _agent_with_stubs(tg, repo)

    agent._cmd_history("/history")

    assert any("⚠️ 거래 내역 조회 실패" in m for m in tg.sent)


def test_set_commands_payload_includes_history(monkeypatch):
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["json"] = json

        class _Resp:
            pass

        return _Resp()

    monkeypatch.setattr("src.agent.telegram_agent.requests.post", _fake_post)

    Telegram("t", "c").set_commands()

    cmds = captured["json"]["commands"]
    assert any(c["command"] == "history" for c in cmds)


def test_help_text_mentions_history_command():
    tg = _StubTg()
    repo = _StubRepoOk()
    agent = _agent_with_stubs(tg, repo)
    agent.mode = "paper"
    agent.pending = {}

    agent.handle_text("/help")

    assert any("/history" in m for m in tg.sent)
