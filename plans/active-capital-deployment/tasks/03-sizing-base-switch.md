# Task 03: 국내 사이징 기준을 예수금에서 총자산으로 바꾸고 배치 예산을 강제한다

## Objective
국내 후보의 수량 계산이 **총자산** 기준으로 이뤄지고, 한 배치가 발주하는 명목 합계가
`deployable_won`을 넘지 않는다. 해외 경로의 동작은 한 글자도 바뀌지 않는다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/change-impact/call-site-enumeration.md` — 파라미터 추가 형태
  (keyword-only), 기본값 함정, 편집 후 재열거로 완료 확인.
- `wiki/testing/quality/minimum-case-set.md` — 케이스 선정.
- `wiki/testing/quality/behavior-not-implementation.md` — 무엇을 단언할지.

## Inputs
- 태스크 02의 산출물: `src/orchestrator/capital.py`의 `CapitalPlan`
- 열거된 호출부 (plan.md "열거된 계약 변경 지점" 표) — 이 태스크가 담당하는 것은
  `tests/test_entry_risk_sizing.py`의 3곳(`_account()` 헬퍼 포함)
- 바인딩되는 결정: **D4**(keyword-only), **D5**(생산자 전부 명시 갱신),
  **D12**(배치 예산), **D13**(3중 min), **D16**

## Steps
1. `AccountSnapshot`에 필드 3개를 **끝에** 추가한다 (중간 삽입 금지 — 위치 인자
   재바인딩 사고):
   ```python
   total_asset_won: int = 0        # 0 이면 cash_won 으로 폴백
   invested_won: int = 0
   pending_notional_won: int = 0
   ```
   그리고 `AccountSnapshot`에 프로퍼티를 둔다:
   ```python
   @property
   def sizing_base_won(self) -> int:
       """국내 사이징 기준. total_asset_won 미배선 호출자는 기존 동작(예수금)을 유지."""
       return self.total_asset_won or self.cash_won
   ```
   이 폴백이 있어야 태스크 06 이전에도 `__main__.py`가 깨지지 않는다.
2. `evaluate_candidate`에 keyword-only 파라미터를 추가한다 (bare `*` 뒤):
   ```python
   def evaluate_candidate(
       candidate, signals, account, rules, repo=None, *, budget_won: int | None = None,
   ) -> tuple[EntryPlan | None, str | None]:
   ```
   `repo=None`까지는 기존 위치 그대로 둔다 — 기존 호출부가 전부 그 형태다.
3. `evaluate_candidate` §5의 **국내 분기만** 바꾼다:
   ```python
   else:
       scale = 1
       entry_price = round_to_tick(clamped.entry_price, mode="floor")
       stop_loss = calc_stop_loss_price(entry_price, clamped.stop_loss_pct)
       take_profit = calc_take_profit_price(entry_price, clamped.take_profit_pct)
       capital = account.sizing_base_won      # ← account.cash_won 에서 변경
       size_pct = float(clamped.size_pct or 0.0)
       risk_pct = float(rules.risk_per_trade_pct)
   ```
   `if meta["asset_class"] == "overseas_stock":` 분기는 **손대지 않는다**.
4. 수량 계산에 예산 상한을 3번째 min으로 넣는다 (**D13**). 기존
   `qty = min(size_qty, risk_qty) if risk_qty > 0 else size_qty` 아래에:
   ```python
   if budget_won is not None:
       budget_qty = budget_won // entry_price if entry_price > 0 else 0
       qty = min(qty, budget_qty)
   ```
   `budget_won is None`이면 기존 동작 그대로 — 기존 테스트가 의미를 유지한다.
5. `qty <= 0` skip 사유 문자열에 `budget_won`을 포함시킨다. 진단 가능성이 이 경로의
   전부다 (지금도 `qty=0 (price=..., capital=...)` 형식).
6. `select_entries`에 keyword-only 파라미터를 추가한다:
   ```python
   def select_entries(
       candidates, signals, account, rules, kill_switch_file=None, repo=None,
       *, deployable_won: int | None = None, quota_override: int | None = None,
   ) -> tuple[list[EntryPlan], list[SkipReason]]:
   ```
7. `select_entries` 내부:
   - `quota = (quota_override if quota_override is not None else rules.max_daily_entries) - account.daily_entries_today`
     — 태스크 05가 재배치 전용 예산을 넣는 통로다 (**D9**).
   - 후보 루프 진입 전 `remaining = deployable_won` (None이면 예산 미적용).
   - 각 후보에 `evaluate_candidate(..., budget_won=remaining)`을 넘긴다.
   - 계획이 채택되면 `remaining -= plan.entry_price_tick * plan.qty`.
   - 루프 상단에서 `remaining is not None and remaining <= 0`이면 남은 후보를
     `SkipReason(..., "budget_exhausted")`로 처리하고 continue.
   - **해외 후보에는 예산을 적용하지 않는다** (원화 예산 ≠ USD 예산). 후보가
     `_is_overseas_candidate(c)`면 `budget_won=None`으로 넘기고 `remaining`도 차감하지
     않는다.
8. 열거된 `AccountSnapshot(` 생산자 중 이 태스크 담당 3곳을 **명시적으로** 갱신한다
   (**D5** — 기본값에 맡기지 않는다). `tests/test_entry_risk_sizing.py`의
   `_account()`(:10), :120, :201 각각에 `total_asset_won=1_000_000`을 추가한다.
   기존 `cash_won=1_000_000`과 같은 값이므로 **기존 단언은 전부 그대로 통과해야 한다** —
   통과하지 않으면 사이징 전환이 국내 경로 밖으로 샌 것이다.
9. 편집이 끝나면 `grep -rn "AccountSnapshot" src tests scripts`와
   `grep -rn "evaluate_candidate\|select_entries" src tests scripts`를 다시 돌려,
   이 태스크 담당 범위에 구계약 호출부가 0건임을 확인한다.

## Deliverables
- `src/orchestrator/entry_decision.py` (수정)
- `tests/test_entry_risk_sizing.py` (수정 + 케이스 추가)

## Verify
기존 7건이 **전부 그대로 통과**해야 한다 (회귀 없음). 추가 케이스:

1. 정상: `total_asset_won=30_000_000`, `cash_won=25_000_000`, `size_pct=10` →
   수량이 총자산 10%(3,000,000) 기준. 같은 입력에서 `total_asset_won=0`으로 두면
   예수금 25,000,000 기준이 되어 **수량이 달라진다** — 폴백이 실제로 다른 경로임을 증명.
2. **D12 증거**: `deployable_won=1_000_000`, 후보 3개, 각 후보 단가 400,000 →
   채택된 계획들의 `entry_price_tick × qty` 합이 1,000,000 이하이고, 마지막 후보의
   skip 사유가 `budget_exhausted`.
3. 경계: `budget_won=0` → `qty=0`, skip 사유에 `budget_won` 문자열 포함.
4. 경계: `budget_won`이 단가보다 작음 (`budget_won=999`, `entry_price=10_000`) →
   `budget_qty=0` → skip. 1주도 못 사는 잔돈으로 발주하지 않는다.
5. 경계: `deployable_won=None` → 기존 동작(예산 무제한)과 완전히 동일한 결과.
6. **해외 불변**: 기존 해외 테스트(:120 근처)가 `deployable_won=0`을 줘도 통과한다 —
   원화 예산이 해외 후보를 굶기지 않는다.
7. 에러 — keyword-only 강제 (D4), **두 함수 모두**:
   - `evaluate_candidate(c, {}, acc, rules, None, 5_000_000)` → `TypeError`
   - `select_entries([], {}, acc, rules, None, None, 5_000_000)` → `TypeError`
     (`deployable_won`을 7번째 위치 인자로)
   - `select_entries([], {}, acc, rules, None, None, None, 3)` → `TypeError`
     (`quota_override`를 8번째 위치 인자로)

   `pytest.raises(TypeError)`로 각각 단언한다. bare `*`를 빠뜨리면 이 셋이 조용히
   통과해 버리고, 나중에 위치 인자 재바인딩 사고가 난다.

8. 정상 — `quota_override` 동작: `account.daily_entries_today=0`, 후보 5건,
   `rules.max_daily_entries=12`일 때 `quota_override=2`를 주면 채택이 **2건**으로 묶이고
   나머지는 `quota_exhausted`로 skip된다. `quota_override=None`이면 12가 쓰인다.
   (태스크 05가 이 인자로 재배치 예산을 넣으므로, 실제로 동작해야 한다.)

명령: `.venv/bin/python -m pytest tests/test_entry_risk_sizing.py -q` → 통과.
이어서 `-m pytest -q` 전체 → 321건+ 통과.

## Out of scope
- `build_prompt` / `ENTRY_PROMPT_TEMPLATE` — 태스크 04.
- `__main__.py`의 `AccountSnapshot(` 생산자와 `select_entries` 호출부 — 태스크 06.
- `scripts/test_entry_decision.py` — 태스크 04.
- 해외 사이징 로직 — 영구 out of scope.
