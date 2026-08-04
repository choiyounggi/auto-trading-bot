-- 0003_strategy_fields.sql — 다중 전략 운용/분석용 컬럼

ALTER TABLE positions ADD COLUMN entry_strategy_id TEXT;
ALTER TABLE positions ADD COLUMN entry_strategy_score INTEGER;
ALTER TABLE positions ADD COLUMN entry_features_json TEXT;

ALTER TABLE llm_decisions ADD COLUMN strategy_id TEXT;
ALTER TABLE llm_decisions ADD COLUMN strategy_score INTEGER;
ALTER TABLE llm_decisions ADD COLUMN features_json TEXT;

CREATE INDEX IF NOT EXISTS idx_positions_strategy ON positions(entry_strategy_id);
CREATE INDEX IF NOT EXISTS idx_llm_strategy ON llm_decisions(strategy_id);
