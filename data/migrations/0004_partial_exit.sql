-- 0004: 부분 익절(partial take-profit) 지원 (2026-07-06)
-- TP 1차 도달 시 일부만 매도해 실익을 확보하고, 잔여분은 연장된 목표가로 계속 운용.
-- qty_remaining: 부분 매도 후 잔여 수량 (NULL = 부분 매도 이력 없음 → entry_qty 전량)
-- partial_exit_count: 부분 익절 횟수 (현재 정책은 최대 1회)
-- partial_realized_pnl_won: 부분 매도로 확정한 누적 실현손익 (최종 청산 시 pnl_won에 합산)
ALTER TABLE positions ADD COLUMN qty_remaining INTEGER;
ALTER TABLE positions ADD COLUMN partial_exit_count INTEGER DEFAULT 0;
ALTER TABLE positions ADD COLUMN partial_realized_pnl_won INTEGER DEFAULT 0;
