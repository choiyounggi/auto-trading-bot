"""Positions / Orders / LLMDecisions CRUD — SQLAlchemy ORM 기반."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path


def add_business_days(start: date, n: int) -> date:
    """주말 제외 N영업일 후 date. (공휴일 미고려 — KRX 공휴일 wrapper 필요 시 추후 추가)"""
    cur = start
    added = 0
    while added < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # 0=월 ~ 4=금
            added += 1
    return cur

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.storage.models import (
    DailyPnl,
    LLMDecision,
    Order,
    Position,
    SystemEvent,
    get_engine,
    get_session_factory,
)

log = logging.getLogger(__name__)


class Repo:
    """단일 세션 컨텍스트 wrapper."""

    def __init__(self, db_path: Path | str = "data/trades.sqlite"):
        self.engine = get_engine(db_path)
        self.SessionLocal = get_session_factory(self.engine)

    # ===================================================
    # Position CRUD
    # ===================================================

    def insert_position(
        self,
        ticker: str,
        name: str,
        signal_score: int,
        confidence: int,
        broker_order_id: str,
        strategy: str,
        price_target: int,
        qty: int,
        thesis: str,
        watch_signals: list[str],
        stop_loss: int,
        take_profit: int,
        max_hold_days: int,
        strategy_id: str | None = None,
        strategy_score: int | None = None,
        features: dict | None = None,
    ) -> int:
        """매수 주문 접수 직후 호출. status=PENDING. 체결 확인되면 OPEN으로 변경."""
        with self.SessionLocal() as s:
            pos = Position(
                ticker=ticker,
                name=name,
                status="PENDING",
                entry_signal_score=signal_score,
                entry_strategy_id=strategy_id,
                entry_strategy_score=strategy_score,
                entry_features_json=json.dumps(features or {}, ensure_ascii=False),
                entry_llm_confidence=confidence,
                entry_order_id=broker_order_id,
                entry_strategy=strategy,
                entry_price_target=price_target,
                entry_qty=qty,
                entry_at=datetime.now(),
                entry_thesis=thesis,
                watch_signals_json=json.dumps(watch_signals, ensure_ascii=False),
                current_stop_loss=stop_loss,
                current_take_profit=take_profit,
                max_hold_until=add_business_days(date.today(), max_hold_days),
                tp_raised_count=0,
                trailing_active=0,
            )
            s.add(pos)
            s.commit()
            s.refresh(pos)
            return pos.id

    def mark_filled(self, position_id: int, actual_price: int) -> None:
        """체결 확정 시 PENDING → OPEN. entry_price_actual 기록."""
        with self.SessionLocal() as s:
            s.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(status="OPEN", entry_price_actual=actual_price, updated_at=datetime.now())
            )
            s.commit()

    def cancel_position(self, position_id: int) -> None:
        """미체결/만료 진입 주문을 CANCELLED 처리."""
        with self.SessionLocal() as s:
            s.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(status="CANCELLED", updated_at=datetime.now())
            )
            s.commit()

    def reconcile_pending_from_balance(
        self, broker_positions: list[dict], today: date | None = None
    ) -> tuple[int, int]:
        """KIS 잔고 기준으로 PENDING → OPEN/CANCELLED 동기화.

        Returns (filled_count, cancelled_count).
        당일 PENDING은 체결 지연 가능성이 있으므로 유지하고, 이전 거래일 PENDING이
        잔고에 없으면 일중/장후 주문 만료로 간주해 CANCELLED 처리한다.
        """
        today = today or date.today()
        by_ticker = {p.get("ticker"): p for p in broker_positions if p.get("ticker")}
        filled = 0
        cancelled = 0
        for pos in self.get_pending_positions():
            broker_pos = by_ticker.get(pos.ticker)
            if broker_pos and int(broker_pos.get("qty", 0) or 0) > 0:
                actual = int(broker_pos.get("avg_price", 0) or pos.entry_price_target or 0)
                self.mark_filled(pos.id, actual)
                filled += 1
                continue
            if pos.entry_at and pos.entry_at.date() < today:
                self.cancel_position(pos.id)
                cancelled += 1
        return filled, cancelled

    def get_open_positions(self) -> list[Position]:
        """실제 체결 완료된 OPEN 포지션만 반환."""
        with self.SessionLocal() as s:
            return list(s.execute(
                select(Position).where(Position.status == "OPEN")
            ).scalars())

    def get_active_positions(self) -> list[Position]:
        """진입 한도/중복 체크용: OPEN + 체결 대기 PENDING."""
        with self.SessionLocal() as s:
            return list(s.execute(
                select(Position).where(Position.status.in_(["OPEN", "PENDING"]))
            ).scalars())

    def get_pending_positions(self) -> list[Position]:
        with self.SessionLocal() as s:
            return list(s.execute(
                select(Position).where(Position.status == "PENDING")
            ).scalars())

    def get_today_entries(self) -> int:
        with self.SessionLocal() as s:
            today = date.today()
            cnt = s.execute(
                select(Position).where(Position.entry_at >= datetime(today.year, today.month, today.day))
            ).scalars().all()
            return len(cnt)

    def update_stop_loss(self, position_id: int, new_stop: int) -> None:
        with self.SessionLocal() as s:
            s.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(current_stop_loss=new_stop, updated_at=datetime.now())
            )
            s.commit()

    def update_take_profit(self, position_id: int, new_tp: int) -> None:
        with self.SessionLocal() as s:
            s.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(
                    current_take_profit=new_tp,
                    tp_raised_count=Position.tp_raised_count + 1,
                    updated_at=datetime.now(),
                )
            )
            s.commit()

    def activate_trailing(self, position_id: int) -> None:
        with self.SessionLocal() as s:
            s.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(trailing_active=1, updated_at=datetime.now())
            )
            s.commit()

    def update_trailing_high(self, position_id: int, new_high: int) -> None:
        with self.SessionLocal() as s:
            s.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(trailing_high=new_high, updated_at=datetime.now())
            )
            s.commit()

    def apply_partial_exit(
        self,
        position_id: int,
        sold_qty: int,
        sell_price: int,
        new_take_profit: int,
        new_stop_loss: int | None = None,
    ) -> None:
        """부분 익절 반영 — 포지션은 OPEN 유지, 잔여수량/실현손익/TP(/SL) 갱신."""
        with self.SessionLocal() as s:
            pos = s.execute(
                select(Position).where(Position.id == position_id)
            ).scalar_one()
            entry = pos.entry_price_actual or pos.entry_price_target or 0
            held = pos.qty_remaining if pos.qty_remaining is not None else (pos.entry_qty or 0)
            realized = (sell_price - entry) * sold_qty
            values = {
                "qty_remaining": max(held - sold_qty, 0),
                "partial_exit_count": (pos.partial_exit_count or 0) + 1,
                "partial_realized_pnl_won": (pos.partial_realized_pnl_won or 0) + realized,
                "current_take_profit": new_take_profit,
                "updated_at": datetime.now(),
            }
            if new_stop_loss is not None and new_stop_loss > (pos.current_stop_loss or 0):
                values["current_stop_loss"] = new_stop_loss  # 상향만 허용 (하향 금지 원칙 유지)
            s.execute(
                update(Position).where(Position.id == position_id).values(**values)
            )
            s.commit()

    def close_position(
        self, position_id: int, exit_reason: str, exit_price: int, exit_order_id: str = ""
    ) -> None:
        with self.SessionLocal() as s:
            pos = s.execute(
                select(Position).where(Position.id == position_id)
            ).scalar_one()
            entry = pos.entry_price_actual or pos.entry_price_target or 0
            # 부분 익절 이력이 있으면 잔여수량 기준 + 확정분 합산
            qty = pos.qty_remaining if pos.qty_remaining is not None else (pos.entry_qty or 0)
            pnl_won = (exit_price - entry) * qty + (pos.partial_realized_pnl_won or 0)
            total_cost = entry * (pos.entry_qty or 0)
            pnl_pct = (pnl_won / total_cost * 100) if total_cost else 0.0
            s.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(
                    status="CLOSED",
                    exit_reason=exit_reason,
                    exit_order_id=exit_order_id,
                    exit_price=exit_price,
                    exit_at=datetime.now(),
                    pnl_pct=pnl_pct,
                    pnl_won=pnl_won,
                    updated_at=datetime.now(),
                )
            )
            s.commit()

    def is_duplicate(self, ticker: str) -> bool:
        with self.SessionLocal() as s:
            cnt = s.execute(
                select(Position).where(
                    Position.ticker == ticker,
                    Position.status.in_(["OPEN", "PENDING"]),
                )
            ).scalars().all()
            return len(cnt) > 0

    def count_open_dip_positions(self, ticker: str) -> int:
        """해당 ETF의 OPEN/PENDING dip_buy 포지션 수(= 채워진 줄줗 단계 수)."""
        with self.SessionLocal() as s:
            rows = s.execute(
                select(Position).where(
                    Position.ticker == ticker,
                    Position.entry_strategy_id == "dip_buy",
                    Position.status.in_(["OPEN", "PENDING"]),
                )
            ).scalars().all()
            return len(rows)

    # ===================================================
    # LLMDecision 영구 기록
    # ===================================================

    def log_llm_decision(
        self,
        position_id: int | None,
        decision_type: str,
        model: str,
        source: str,
        response_text: str,
        response_json: str | None,
        confidence: int | None,
        action: str | None,
        elapsed_ms: int,
        parse_error: str | None = None,
        prompt_hash: str | None = None,
        ticker: str | None = None,
        name: str | None = None,
        signal_score: int | None = None,
        strategy_id: str | None = None,
        strategy_score: int | None = None,
        features_json: str | None = None,
        decision_date: date | None = None,
        eval_due_date: date | None = None,
        price_at_decision: int | None = None,
    ) -> int:
        with self.SessionLocal() as s:
            d = LLMDecision(
                position_id=position_id,
                decision_type=decision_type,
                model=model,
                source=source,
                prompt_hash=prompt_hash,
                ticker=ticker,
                name=name,
                signal_score=signal_score,
                strategy_id=strategy_id,
                strategy_score=strategy_score,
                features_json=features_json,
                decision_date=decision_date,
                eval_due_date=eval_due_date,
                price_at_decision=price_at_decision,
                response_text=response_text[:5000] if response_text else None,
                response_json=response_json,
                confidence=confidence,
                action=action,
                elapsed_ms=elapsed_ms,
                parse_error=parse_error,
            )
            s.add(d)
            s.commit()
            s.refresh(d)
            return d.id

    def get_due_entry_decisions(self, today: date | None = None) -> list[LLMDecision]:
        """5거래일 사후평가 기한이 지난 ENTRY 결정 중 미라벨 건."""
        today = today or date.today()
        with self.SessionLocal() as s:
            return list(s.execute(
                select(LLMDecision).where(
                    LLMDecision.decision_type == "ENTRY",
                    LLMDecision.label.is_(None),
                    LLMDecision.ticker.is_not(None),
                    LLMDecision.eval_due_date.is_not(None),
                    LLMDecision.eval_due_date <= today,
                    LLMDecision.price_at_decision.is_not(None),
                    LLMDecision.price_at_decision > 0,
                )
            ).scalars())

    def label_llm_decision(self, decision_id: int, label: str, actual_return: float) -> None:
        with self.SessionLocal() as s:
            s.execute(
                update(LLMDecision)
                .where(LLMDecision.id == decision_id)
                .values(
                    label=label,
                    actual_return=actual_return,
                    labeled_at=datetime.now(),
                )
            )
            s.commit()

    # ===================================================
    # 일일 PnL
    # ===================================================

    def upsert_daily_pnl(
        self,
        trade_date: date,
        capital_start: int,
        capital_end: int,
        realized_pnl_won: int,
        realized_pnl_pct: float,
        unrealized_pnl_won: int,
        trades_opened: int,
        trades_closed: int,
        stop_loss_hits: int,
        take_profit_hits: int,
        time_stops: int,
        kill_switch_active: int,
        notes: str = "",
    ) -> None:
        with self.SessionLocal() as s:
            existing = s.execute(
                select(DailyPnl).where(DailyPnl.trade_date == trade_date)
            ).scalar_one_or_none()
            if existing:
                s.execute(
                    update(DailyPnl)
                    .where(DailyPnl.trade_date == trade_date)
                    .values(
                        capital_start=capital_start,
                        capital_end=capital_end,
                        realized_pnl_won=realized_pnl_won,
                        realized_pnl_pct=realized_pnl_pct,
                        unrealized_pnl_won=unrealized_pnl_won,
                        trades_opened=trades_opened,
                        trades_closed=trades_closed,
                        stop_loss_hits=stop_loss_hits,
                        take_profit_hits=take_profit_hits,
                        time_stops=time_stops,
                        kill_switch_active=kill_switch_active,
                        notes=notes,
                    )
                )
            else:
                s.add(DailyPnl(
                    trade_date=trade_date,
                    capital_start=capital_start,
                    capital_end=capital_end,
                    realized_pnl_won=realized_pnl_won,
                    realized_pnl_pct=realized_pnl_pct,
                    unrealized_pnl_won=unrealized_pnl_won,
                    trades_opened=trades_opened,
                    trades_closed=trades_closed,
                    stop_loss_hits=stop_loss_hits,
                    take_profit_hits=take_profit_hits,
                    time_stops=time_stops,
                    kill_switch_active=kill_switch_active,
                    notes=notes,
                ))
            s.commit()

    def get_today_pnl(self) -> tuple[int, int, int]:
        """today 청산건 (count, realized_won, sl_hits)."""
        with self.SessionLocal() as s:
            today = date.today()
            closed = s.execute(
                select(Position).where(
                    Position.status == "CLOSED",
                    Position.exit_at >= datetime(today.year, today.month, today.day),
                )
            ).scalars().all()
            total_pnl = sum(p.pnl_won or 0 for p in closed)
            sl_hits = sum(1 for p in closed if p.exit_reason == "STOP_LOSS")
            return len(closed), total_pnl, sl_hits

    # ===================================================
    # System events
    # ===================================================

    def log_event(self, event_type: str, severity: str, message: str, metadata: dict | None = None) -> None:
        with self.SessionLocal() as s:
            s.add(SystemEvent(
                event_type=event_type,
                severity=severity,
                message=message,
                event_metadata=json.dumps(metadata or {}, ensure_ascii=False),
            ))
            s.commit()
