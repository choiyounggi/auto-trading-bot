-- 0001_init.sql — SQLite 초기 스키마
-- 실행: sqlite3 data/trades.sqlite < data/migrations/0001_init.sql

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- positions — 종목별 진입~청산 상태 머신
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','OPEN','CLOSING','CLOSED','CANCELLED')),

    -- 진입
    entry_signal_score INTEGER,
    entry_strategy_id TEXT,
    entry_strategy_score INTEGER,
    entry_features_json TEXT,
    entry_llm_confidence INTEGER,
    entry_order_id TEXT,
    entry_strategy TEXT CHECK (entry_strategy IN ('MARKET_OPEN','LIMIT_TODAY_AFTER_HOURS')),
    entry_price_target INTEGER,
    entry_price_actual INTEGER,
    entry_qty INTEGER,
    entry_at TIMESTAMP,
    entry_thesis TEXT,
    watch_signals_json TEXT,

    -- 동적 관리
    current_stop_loss INTEGER,
    current_take_profit INTEGER,
    max_hold_until DATE,
    trailing_high INTEGER,
    tp_raised_count INTEGER DEFAULT 0,
    trailing_active INTEGER DEFAULT 0,

    -- 청산
    exit_reason TEXT CHECK (exit_reason IN ('STOP_LOSS','TAKE_PROFIT','TIME_STOP','TRAILING_STOP','LLM_CLOSE','DAILY_LIMIT','MANUAL','MCP_FAIL')),
    exit_order_id TEXT,
    exit_price INTEGER,
    exit_at TIMESTAMP,
    pnl_pct REAL,
    pnl_won INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(ticker);
CREATE INDEX IF NOT EXISTS idx_positions_entry_at ON positions(entry_at);

-- ============================================================
-- orders — KIS(한국투자증권) 주문 추적
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER REFERENCES positions(id),
    broker_order_id TEXT UNIQUE,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    order_type TEXT NOT NULL CHECK (order_type IN ('LIMIT','MARKET','AFTER_HOURS')),
    price INTEGER,
    qty INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','FILLED','PARTIAL','CANCELLED','REJECTED')),
    filled_qty INTEGER DEFAULT 0,
    filled_avg_price INTEGER,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    filled_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    raw_response TEXT,
    client_order_id TEXT UNIQUE -- 중복 주문 방지 UUID
);

CREATE INDEX IF NOT EXISTS idx_orders_position ON orders(position_id);
CREATE INDEX IF NOT EXISTS idx_orders_broker_id ON orders(broker_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- ============================================================
-- llm_decisions — 모든 LLM 호출 영구 기록 (사후 평가용)
-- ============================================================
CREATE TABLE IF NOT EXISTS llm_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER REFERENCES positions(id),
    decision_type TEXT NOT NULL CHECK (decision_type IN ('ENTRY','MONITOR')),
    model TEXT,
    source TEXT CHECK (source IN ('claude','pi','unavailable')),
    prompt_hash TEXT,
    prompt_token_estimate INTEGER,
    ticker TEXT,
    name TEXT,
    signal_score INTEGER,
    strategy_id TEXT,
    strategy_score INTEGER,
    features_json TEXT,
    decision_date DATE,
    eval_due_date DATE,
    price_at_decision INTEGER,
    response_text TEXT,
    response_json TEXT,
    confidence INTEGER,
    action TEXT,
    elapsed_ms INTEGER,
    timeout INTEGER DEFAULT 0,
    parse_error TEXT,
    -- 사후 라벨 (5거래일 후 평가)
    label TEXT CHECK (label IN ('TRUE_POSITIVE','FALSE_POSITIVE','TRUE_NEGATIVE','FALSE_NEGATIVE','NEUTRAL')),
    actual_return REAL,
    labeled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_llm_position ON llm_decisions(position_id);
CREATE INDEX IF NOT EXISTS idx_llm_decision_type ON llm_decisions(decision_type);
CREATE INDEX IF NOT EXISTS idx_llm_created_at ON llm_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_ticker ON llm_decisions(ticker);
CREATE INDEX IF NOT EXISTS idx_llm_eval_due ON llm_decisions(eval_due_date);

-- ============================================================
-- daily_pnl — 일일 PnL 집계 (reconciler가 작성)
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_pnl (
    trade_date DATE PRIMARY KEY,
    capital_start INTEGER,
    capital_end INTEGER,
    realized_pnl_won INTEGER DEFAULT 0,
    realized_pnl_pct REAL DEFAULT 0.0,
    unrealized_pnl_won INTEGER DEFAULT 0,
    trades_opened INTEGER DEFAULT 0,
    trades_closed INTEGER DEFAULT 0,
    stop_loss_hits INTEGER DEFAULT 0,
    take_profit_hits INTEGER DEFAULT 0,
    time_stops INTEGER DEFAULT 0,
    kill_switch_active INTEGER DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- system_events — KillSwitch, MCP 장애, 가드레일 활성 등 영속 로그
-- ============================================================
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT CHECK (severity IN ('CRITICAL','WARNING','INFO')),
    message TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_events_type ON system_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON system_events(created_at);
