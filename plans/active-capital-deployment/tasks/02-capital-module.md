# Task 02: 가동률과 배치가능액을 계산하는 capital 모듈을 만든다

## Objective
`src/orchestrator/capital.py`가 잔고 스냅샷에서 총자산·투자중·가동률·배치가능액을
계산한다. 배치가능액은 어떤 경우에도 매수여력을 넘지 않고 음수가 되지 않는다.

## Wiki pages (read these first, only these)
- `wiki/testing/quality/minimum-case-set.md` — 이 순수 계산 함수의 경계값 선정.
- `wiki/testing/quality/tests-that-cannot-fail.md` — 계산식을 테스트에 그대로 옮겨
  적어 항상 통과하는 테스트를 만들지 않기 위해.

## Inputs
- 태스크 01의 산출물: `TradingRules.target_utilization_pct`, `TradingRules.cash_buffer_pct`
- 기존 타입: `src.broker.kis_client.Balance` (`cash`, `total_eval`, `positions: list[dict]`)
- 바인딩되는 결정: **D1**(총자산=`total_eval`), **D2**(매수여력=`ord_psbl_cash`),
  **D3**(투자중=`eval_amt` 합), **D7**(PENDING 명목 합산)

## Steps
1. `src/orchestrator/capital.py`를 새로 만든다. **KIS 네트워크 호출을 하지 않는다** —
   순수 계산만 하고, 값은 호출자가 넣어준다 (테스트 가능성).
2. dataclass와 함수를 정확히 이 시그니처로 정의한다:
   ```python
   @dataclass(frozen=True)
   class CapitalPlan:
       total_asset_won: int      # D1
       invested_won: int         # D3 + D7 (보유 평가액 + PENDING 명목)
       buying_power_won: int     # D2
       utilization_pct: float    # invested / total_asset × 100
       target_pct: float
       deployable_won: int       # 이번에 새로 투입 가능한 금액
       buffer_won: int           # 남겨둘 현금

   def compute_capital_plan(
       *,
       total_asset_won: int,
       position_eval_won: int,
       pending_notional_won: int,
       buying_power_won: int,
       rules: TradingRules,
   ) -> CapitalPlan: ...
   ```
   모든 인자를 keyword-only로 둔다 (**D4**). 신규 모듈이지만 이후 인자가 늘어날 때
   위치 바인딩 사고를 원천 차단한다.
3. 계산 규칙 — 이 순서 그대로:
   - `invested_won = position_eval_won + pending_notional_won`
   - `utilization_pct = invested_won / total_asset_won * 100` (총자산 0이면 `0.0`)
   - `buffer_won = int(total_asset_won * rules.cash_buffer_pct / 100)`
   - `target_won = int(total_asset_won * rules.target_utilization_pct / 100)`
   - `gap_won = target_won - invested_won`
   - `spendable_won = buying_power_won - buffer_won`
   - `deployable_won = max(0, min(gap_won, spendable_won))`
4. `deployable_won`이 `min`으로 매수여력에 묶이는 것이 이 모듈의 존재 이유다.
   목표만 보고 계산하면 실제로 못 사는 금액을 배치하게 된다.
5. 잔고 dict에서 값을 뽑는 얇은 헬퍼도 같은 모듈에 둔다 (호출자 중복 방지):
   ```python
   def position_eval_won(positions: list[dict]) -> int:
       """보유 종목 평가금액 합. 'eval_amt' 키가 없으면 0으로 센다."""
   ```
6. 로깅은 `logging.getLogger(__name__)` 한 줄(`log.info`)로 계산 결과 요약만.

## Deliverables
- `src/orchestrator/capital.py` (신규)
- `tests/test_capital_plan.py` (신규)

## Verify
`tests/test_capital_plan.py`는 다음을 커버한다. **기대값은 손으로 계산한 숫자를 literal로
적는다** — 프로덕션 수식을 테스트에 재구현하면 어떤 버그도 못 잡는다:

1. 정상: 총자산 30,000,000 / 보유평가 5,000,000 / PENDING 0 / 매수여력 25,000,000,
   목표 90·버퍼 10 → `utilization_pct ≈ 16.67`, `target_won = 27,000,000`,
   `buffer_won = 3,000,000`, `deployable_won = 22,000,000`.
2. **매수여력이 상한이 되는 경우** (이 모듈의 핵심): 총자산 30,000,000 / 보유평가 0 /
   매수여력 **5,000,000** → gap 27,000,000 이지만 spendable 2,000,000 →
   `deployable_won == 2,000,000`.
3. **D7 증거**: PENDING 명목 10,000,000을 넣으면 `invested_won`이 그만큼 늘고
   `deployable_won`이 그만큼 줄어든다. PENDING을 0으로 준 같은 케이스와 비교해
   차이가 정확히 10,000,000임을 단언.
4. 경계: 이미 목표 초과 (보유평가 28,000,000, 목표 27,000,000) → `deployable_won == 0`
   (음수 아님).
5. 경계: 매수여력 < 버퍼 (매수여력 1,000,000, 버퍼 3,000,000) → `deployable_won == 0`.
6. 경계: `total_asset_won == 0` → `utilization_pct == 0.0`, ZeroDivisionError 없음,
   `deployable_won == 0`.
7. 경계: `position_eval_won([])` == 0, `position_eval_won([{}])` == 0 (키 없음),
   `position_eval_won([{"eval_amt": 100}, {"eval_amt": 200}])` == 300.
8. 에러: `compute_capital_plan(0, 0, 0, 0, rules)` 처럼 **위치 인자로 호출하면
   `TypeError`** — keyword-only 계약이 실제로 강제되는지 (D4).

명령: `.venv/bin/python -m pytest tests/test_capital_plan.py -q` → 통과.

## Out of scope
- KIS 조회 (`get_balance` / `get_deposit`) 호출 — 태스크 05·06이 배선한다.
- PENDING 명목금액을 DB에서 실제로 세는 일 — 태스크 05가 한다. 여기서는 **인자로 받는다**.
- `entry_decision.py` 수정 — 태스크 03.
