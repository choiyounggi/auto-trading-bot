-- 0002_llm_entry_eval.sql — ENTRY LLM 사후평가용 컬럼
-- SQLite ALTER TABLE은 ADD COLUMN IF NOT EXISTS 호환성이 제한적이므로
-- 운영 DB에는 아래 컬럼 존재 여부를 확인한 뒤 한 번만 적용한다.

ALTER TABLE llm_decisions ADD COLUMN ticker TEXT;
ALTER TABLE llm_decisions ADD COLUMN name TEXT;
ALTER TABLE llm_decisions ADD COLUMN signal_score INTEGER;
ALTER TABLE llm_decisions ADD COLUMN decision_date DATE;
ALTER TABLE llm_decisions ADD COLUMN eval_due_date DATE;
ALTER TABLE llm_decisions ADD COLUMN price_at_decision INTEGER;

CREATE INDEX IF NOT EXISTS idx_llm_ticker ON llm_decisions(ticker);
CREATE INDEX IF NOT EXISTS idx_llm_eval_due ON llm_decisions(eval_due_date);
