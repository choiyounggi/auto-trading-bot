# Task 04: LLM 프롬프트의 자본 기준을 총자산으로 맞추고 진단 스크립트를 갱신한다

## Objective
ENTRY 프롬프트가 총자산·가동률·이번 배치 가능액을 보여주고 `size_pct`가 **총자산 대비**
비율임을 명시한다. 프롬프트에 하드코딩된 사이징 숫자가 D15 값과 일치한다.
`scripts/test_entry_decision.py`가 새 계약으로 계좌 스냅샷을 만든다.

## 왜 이 태스크가 필요한가
`config/trading_rules.yaml`의 기존 주석이 이미 경고하고 있다 — *"config + prompt 동시
완화해야 실효"*. `src/llm/prompts.py`는 `size_pct 권장: 5~10%`, `clamp 범위 3~10%`,
`10% 초과 금지`를 **문자열로 하드코딩**한다. 태스크 01이 `max_size_pct`를 15로 올려도
프롬프트가 10% 상한을 지시하면 LLM은 10을 넘지 않고, 상향은 무효가 된다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/api-design/unenforced-declarations.md` — 설정에 선언했는데
  실제로 아무것도 바뀌지 않는 "recorded but unenforced" 형태를 만들지 않기 위해.
  이 태스크가 막는 것이 정확히 그 실패다.
- `wiki/backend/common/change-impact/call-site-enumeration.md` — 열거된 마지막
  `AccountSnapshot(` 생산자를 기본값에 맡기지 않고 갱신하는 근거.

## Inputs
- 태스크 03의 산출물: `AccountSnapshot.total_asset_won` / `.invested_won` /
  `.sizing_base_won`, `evaluate_candidate(..., *, budget_won=None)`
- 태스크 01의 산출물: `TradingRules.max_size_pct = 15.0`, `min_size_pct = 5.0`
- 열거된 마지막 생산자: `scripts/test_entry_decision.py:70`
- 바인딩되는 결정: **D5**, **D15**, **D17**

## Steps
1. `src/llm/prompts.py`의 `[계좌 상태]` 블록을 교체한다. 플레이스홀더 이름을
   여기 적힌 그대로 쓴다 — `build_prompt`의 `.format(...)` 인자와 1:1이어야 한다:
   ```
   [계좌 상태]
   총자산(예수금+평가): {total_asset_won:,} 원
   현재 투자중: {invested_won:,} 원 (가동률 {utilization_pct:.1f}% / 목표 {target_utilization_pct:.0f}%)
   이번 배치 가능액: {deployable_won:,} 원
   보유 종목 수: {open_positions}/{max_positions}
   오늘 누적 PnL: {daily_pnl_pct:+.2f}%
   ```
   기존 `국내 가용 자본: {cash_won:,} 원` 줄은 삭제한다. `cash_won` 플레이스홀더가
   템플릿에 남아 있으면 `.format()`이 `KeyError`를 던진다.
2. 같은 파일 `[운영 룰 — 절대 준수]`의 사이징 문구를 D15에 맞춰 고치고, **비율의
   분모가 총자산임을 명시**한다 (D17):
   - `size_pct 권장: 5~10%` → `size_pct 권장: 8~15% (총자산 대비 비율이다. 확신 보통=8~11%, high-conviction=12~15%)`
   - `clamp 범위 3~10%` → `clamp 범위 5~15%`
   - `high-conviction이어도 10% 초과 금지` → `... 15% 초과 금지`
   - 아래 JSON 스펙의 `"size_pct": <3.0~10.0>` → `<5.0~15.0>`
   - **`- 실제 주문 수량은 코드가 ... 다시 줄인다` 줄 바로 아래에 한 줄 추가**:
     `- 계좌에 남은 배치 가능액을 코드가 상한으로 적용한다. 가동률을 채우려고 size_pct를 부풀리지 말 것.`
   - `min_confidence 5.0`, 손절 `1.5~3.0`, 익절 `2.0~10.0`, `max_hold_days` 문구는
     **손대지 않는다** (태스크 01이 그 값들을 바꾸지 않았다).
3. `src/orchestrator/entry_decision.py`의 `build_prompt` 시그니처에 keyword-only
   인자를 추가한다 (**D4**):
   ```python
   def build_prompt(
       candidate: dict, signals: dict, account: AccountSnapshot, rules: TradingRules,
       *, deployable_won: int = 0,
   ) -> str:
   ```
   `.format(...)`의 `cash_won=account.cash_won`을 아래로 교체한다:
   ```python
   total_asset_won=account.sizing_base_won,
   invested_won=account.invested_won,
   utilization_pct=(account.invested_won / account.sizing_base_won * 100) if account.sizing_base_won else 0.0,
   target_utilization_pct=rules.target_utilization_pct,
   deployable_won=deployable_won,
   ```
4. `evaluate_candidate` 안의 `prompt = build_prompt(candidate, signals, account, rules)`를
   `build_prompt(candidate, signals, account, rules, deployable_won=budget_won or 0)`로
   바꾼다. `budget_won`은 태스크 03이 이미 추가한 인자다.
5. `scripts/test_entry_decision.py:70`의 `AccountSnapshot(...)`에 새 필드를 채운다
   (**D5** — 기본값에 맡기지 않는다). 이 스크립트는 실계좌를 조회하는 진단 도구이므로
   프로덕션과 같은 값을 넣어야 진단이 의미가 있다:
   ```python
   from src.orchestrator.capital import position_eval_won

   account = AccountSnapshot(
       cash_won=balance.cash,
       total_asset_won=balance.total_eval,
       invested_won=position_eval_won(balance.positions),
       open_positions=0,
       daily_pnl_pct=0.0,
       daily_entries_today=0,
   )
   ```
   같은 파일의 `=== rules ===` 출력 블록에 `print(f"  가동률 목표: {rules.target_utilization_pct}%")`
   한 줄을 추가한다.
6. 편집 후 `grep -rn "cash_won" src tests scripts`를 돌려, `prompts.py`에 `cash_won`
   플레이스홀더가 남아 있지 않은지 확인한다.

## Deliverables
- `src/llm/prompts.py` (수정)
- `src/orchestrator/entry_decision.py` (수정 — `build_prompt`만)
- `scripts/test_entry_decision.py` (수정)

## Verify
`tests/test_entry_risk_sizing.py`에 프롬프트 케이스를 추가한다 (새 파일 만들지 말 것 —
`build_prompt`는 이미 그 모듈의 테스트 대상 범위다):

1. 정상: `total_asset_won=30_000_000`, `invested_won=5_000_000`로 `build_prompt`를 부르면
   반환 문자열에 `"30,000,000"`, `"5,000,000"`, `"16.7%"`, `"목표 90%"`가 들어 있다.
2. 정상: `deployable_won=22_000_000`을 넘기면 `"이번 배치 가능액: 22,000,000 원"`이
   문자열에 있다.
3. **D15 동기화 가드**: 프롬프트 문자열에 `f"clamp 범위 {rules.min_size_pct:.0f}~{rules.max_size_pct:.0f}%"`
   가 들어 있음을 단언한다. 하드코딩 문구와 config가 갈라지면 빨개진다 —
   이 태스크의 존재 이유를 회귀 테스트로 고정하는 것.
4. 에러: `total_asset_won=0`, `cash_won=0` → 가동률 계산에서 ZeroDivisionError가 나지
   않고 `"0.0%"`가 렌더된다.
5. 경계: `deployable_won` 미지정(기본 0) → `"이번 배치 가능액: 0 원"`, 예외 없음.
6. 경계: `build_prompt(c, s, acc, rules, 100)` 처럼 위치 인자로 넘기면 `TypeError`.

명령:
- `.venv/bin/python -m pytest tests/test_entry_risk_sizing.py -q` → 통과
- `.venv/bin/python -m pytest -q` → 321건+ 전체 통과
- `.venv/bin/python -c "import ast,sys; ast.parse(open('scripts/test_entry_decision.py').read())"`
  → 스크립트 문법 확인 (실행은 실계좌를 건드리므로 하지 않는다)

## Out of scope
- 스크립트를 실제로 실행해 KIS를 호출하는 것 — 실계좌 접속이라 하지 않는다.
- `src/monitor/` 쪽 프롬프트 — 진입 사이징과 무관.
- `entry_decision.py`의 사이징 로직 — 태스크 03이 이미 끝냈다. `build_prompt`만 만진다.
