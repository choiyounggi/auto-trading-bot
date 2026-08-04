"""SQLAlchemy ORM — data/migrations/0001_init.sql 스키마 매핑."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)  # PENDING/OPEN/CLOSING/CLOSED/CANCELLED

    entry_signal_score = Column(Integer)
    entry_strategy_id = Column(String)
    entry_strategy_score = Column(Integer)
    entry_features_json = Column(Text)
    entry_llm_confidence = Column(Integer)
    entry_order_id = Column(String)
    entry_strategy = Column(String)
    entry_price_target = Column(Integer)
    entry_price_actual = Column(Integer)
    entry_qty = Column(Integer)
    entry_at = Column(DateTime)
    entry_thesis = Column(Text)
    watch_signals_json = Column(Text)

    current_stop_loss = Column(Integer)
    current_take_profit = Column(Integer)
    max_hold_until = Column(Date)
    trailing_high = Column(Integer)
    tp_raised_count = Column(Integer, default=0)
    trailing_active = Column(Integer, default=0)

    # 부분 익절 (0004_partial_exit) — NULL qty_remaining = 부분 매도 없음(전량 보유)
    qty_remaining = Column(Integer)
    partial_exit_count = Column(Integer, default=0)
    partial_realized_pnl_won = Column(Integer, default=0)

    exit_reason = Column(String)
    exit_order_id = Column(String)
    exit_price = Column(Integer)
    exit_at = Column(DateTime)
    pnl_pct = Column(Float)
    pnl_won = Column(Integer)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())

    orders = relationship("Order", back_populates="position")
    llm_decisions = relationship("LLMDecision", back_populates="position")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey("positions.id"))
    broker_order_id = Column(String, unique=True)
    side = Column(String, nullable=False)  # BUY/SELL
    order_type = Column(String, nullable=False)  # LIMIT/MARKET/AFTER_HOURS
    price = Column(Integer)
    qty = Column(Integer, nullable=False)
    status = Column(String, nullable=False)  # PENDING/FILLED/PARTIAL/CANCELLED/REJECTED
    filled_qty = Column(Integer, default=0)
    filled_avg_price = Column(Integer)
    submitted_at = Column(DateTime, server_default=func.now())
    filled_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    raw_response = Column(Text)
    client_order_id = Column(String, unique=True)

    position = relationship("Position", back_populates="orders")


class LLMDecision(Base):
    __tablename__ = "llm_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, ForeignKey("positions.id"))
    decision_type = Column(String, nullable=False)  # ENTRY/MONITOR
    model = Column(String)
    source = Column(String)  # claude/pi/unavailable
    prompt_hash = Column(String)
    prompt_token_estimate = Column(Integer)
    ticker = Column(String)
    name = Column(String)
    signal_score = Column(Integer)
    strategy_id = Column(String)
    strategy_score = Column(Integer)
    features_json = Column(Text)
    decision_date = Column(Date)
    eval_due_date = Column(Date)
    price_at_decision = Column(Integer)
    response_text = Column(Text)
    response_json = Column(Text)
    confidence = Column(Integer)
    action = Column(String)
    elapsed_ms = Column(Integer)
    timeout = Column(Integer, default=0)
    parse_error = Column(Text)
    label = Column(String)
    actual_return = Column(Float)
    labeled_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())

    position = relationship("Position", back_populates="llm_decisions")


class DailyPnl(Base):
    __tablename__ = "daily_pnl"

    trade_date = Column(Date, primary_key=True)
    capital_start = Column(Integer)
    capital_end = Column(Integer)
    realized_pnl_won = Column(Integer, default=0)
    realized_pnl_pct = Column(Float, default=0.0)
    unrealized_pnl_won = Column(Integer, default=0)
    trades_opened = Column(Integer, default=0)
    trades_closed = Column(Integer, default=0)
    stop_loss_hits = Column(Integer, default=0)
    take_profit_hits = Column(Integer, default=0)
    time_stops = Column(Integer, default=0)
    kill_switch_active = Column(Integer, default=0)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class SystemEvent(Base):
    __tablename__ = "system_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)
    severity = Column(String)
    message = Column(Text)
    event_metadata = Column("metadata", Text)  # 'metadata' 컬럼명, ORM 속성은 event_metadata
    created_at = Column(DateTime, server_default=func.now())


def get_engine(db_path: Path | str = "data/trades.sqlite"):
    """SQLite 엔진 + WAL + 외래키 활성."""
    p = Path(db_path).expanduser()
    return create_engine(f"sqlite:///{p}", future=True)


def get_session_factory(engine):
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)


def create_all(engine) -> None:
    """초기 스키마 생성 (테스트/dev용. 운영은 0001_init.sql 사용)."""
    Base.metadata.create_all(engine)
