# Task 05: 장중 현금 재배치 로직을 만든다

## Objective
`src/orchestrator/cash_deploy.py`의 `run_cash_deploy(...)`가 가동률을 계산해 미달분만큼
추가 진입한다. 미체결 주문을 유휴 현금으로 오인하지 않고, 후보가 없으면 경고 후
정상 종료한다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/jobs/idempotent-handlers.md` — 스케줄 잡이 재실행/중복 실행돼도
  같은 현금을 두 번 쓰지 않게 하는 규칙. 특히 §5(스케줄 fire 중복)와
  "DB state write → 절대값 set, 증분 금지".
- `wiki/backend/common/jobs/scheduled-job-overlap.md` — 왜 한 회 실행을 스케줄 간격
  안으로 묶어야 하는지 (`max_candidates_per_run`의 근거).
- `wiki/testing/quality/minimum-case-set.md` — 케이스 선정.

## Inputs
- 태스크 02·03의 산출물 (t1 세션이 이미 승인·머지한 코드):
  - `src.orchestrator.capital`: `CapitalPlan`, `compute_capital_plan(*, total_asset_won,
    position_eval_won, pending_notional_won, buying_power_won, rules)`, `position_eval_won(positions)`
  - `src.orchestrator.entry_decision`: `AccountSnapshot`(필드 `total_asset_won`,
    `invested_won`, `pending_notional_won`, 프로퍼티 `sizing_base_won`),
    `select_entries(..., *, deployable_won=None, quota_override=None)`
  - `src.guardrails.rules.TradingRules`: `cash_deploy_enabled`,
    `cash_deploy_max_daily_entries`, `cash_deploy_max_candidates_per_run`,
    `cash_deploy_min_deploy_won`, `cash_deploy_underrun_warn_pct`
- 따라 쓸 선례: `src/orchestrator/dip_buy.py` — 같은 저장소의 "순수 로직은 단위테스트,
  IO는 `run_*` 하나에" 구조. `_KIS_GAP_SEC = 1.1`(KIS 초당 제한)과
  `is_market_closed_reject(msg)`(정규장 아님 거부는 경고하지 않음)를 **재사용**한다.
- 바인딩되는 결정: **D2**, **D7**, **D8**, **D9**, **D10**, **D11**

## Steps
1. `src/orchestrator/cash_deploy.py`를 만든다. 모듈 docstring에 목적과 겹침 가드
   전제(한 회 최악 12분 < 락 15분 < 간격 30분)를 적는다.
2. **순수 함수** 2개를 먼저 둔다 (IO 없이 테스트되도록):
   ```python
   def compute_pending_notional(pending_positions) -> int:
       """PENDING 포지션의 미체결 명목금액 합. entry_price_target × entry_qty."""

   def pick_candidates(candidates: list[dict], repo, limit: int) -> list[dict]:
       """국내 후보만, 중복 종목 제외, score 내림차순, 최대 limit 개."""
   ```
   - `compute_pending_notional`: `entry_price_target`이나 `entry_qty`가 `None`이면
     그 포지션은 0으로 센다. 이 값이 **D7의 전부**다 — 빠지면 30분 뒤 다음 틱이 같은
     현금을 다시 쓴다.
   - `pick_candidates`: `asset_class`가 `domestic_stock`이 아닌 후보는 버린다
     (원화 예산으로 해외를 사지 않는다). `repo.is_duplicate(ticker)`가 True면 버린다.
     `limit`은 `rules.cash_deploy_max_candidates_per_run` (D8).
3. **하루 1회 경고 마커** — 이 저장소는 이미 `data/agent_mode`, `data/KILL_SWITCH` 같은
   파일 상태를 쓴다. 같은 방식으로:
   ```python
   _WARN_MARKER = Path("data/logs/.cash_deploy_underrun")

   def should_warn_underrun(today: str, marker: Path = _WARN_MARKER) -> bool:
       """오늘 아직 경고하지 않았으면 True. True 를 반환할 때 마커를 오늘 날짜로 갱신한다."""
   ```
   읽기/쓰기 실패(`OSError`)는 삼키고 `True`를 반환한다 — 경고를 못 보내는 것보다
   중복 경고가 낫다.
4. `run_cash_deploy`를 `dip_buy.run_dip_buy`와 같은 형태로 만든다:
   ```python
   def run_cash_deploy(
       client: Any, repo: Any, rules: Any, candidates: list[dict], signals: dict,
       send_info: Callable[[str], Any], send_warning: Callable[[str], Any],
   ) -> int:
       """가동률 미달분만큼 1회 재배치. 접수된 주문 수를 반환."""
   ```
   순서 그대로:
   1. `rules.cash_deploy_enabled`가 False면 `log.info` 후 `return 0`.
   2. `balance = client.get_balance()`. `None`이면 `send_warning("재배치: KIS 잔고 조회 실패")`
      후 `return 0` (**예외를 올리지 않는다** — launchd 잡이 비정상 종료하면 안 된다).
   3. `time.sleep(_KIS_GAP_SEC)` 후 `buying_power = client.get_deposit() or balance.cash`
      (**D2**). `get_deposit`이 예외를 던지면 `log.info`로 삼키고 `balance.cash`로 폴백.
   4. `plan = compute_capital_plan(total_asset_won=balance.total_eval,
      position_eval_won=position_eval_won(balance.positions),
      pending_notional_won=compute_pending_notional(repo.get_pending_positions()),
      buying_power_won=buying_power, rules=rules)`
   5. `log.info`로 `plan`의 값을 전부 남긴다. 이 로그가 운영 중 유일한 진단 창이다.
   6. `plan.deployable_won < rules.cash_deploy_min_deploy_won`이면 → underrun 경고 판단
      후 `return 0`.
   7. `picked = pick_candidates(candidates, repo, rules.cash_deploy_max_candidates_per_run)`.
      비었으면 → underrun 경고 판단 후 `return 0`.
   8. `account = AccountSnapshot(cash_won=balance.cash,
      total_asset_won=balance.total_eval, invested_won=plan.invested_won,
      pending_notional_won=<위에서 센 값>, open_positions=len(repo.get_active_positions()),
      daily_pnl_pct=0.0, daily_entries_today=repo.get_today_entries())`
   9. `plans, skips = select_entries(picked, signals, account, rules, repo=repo,
      deployable_won=plan.deployable_won,
      quota_override=rules.max_daily_entries + rules.cash_deploy_max_daily_entries)`
      — **D9**: 합산 상한이 곧 "아침 예산 위에 얹는 추가 예산"이다. 여기에
      `rules.cash_deploy_max_daily_entries`만 넣으면 아침 진입이 이미 소진해 0이 된다.
      **Kill Switch·시장 레짐·중복 필터는 `select_entries` 안에 이미 있다. 우회 경로를
      만들지 말 것 (D10).**
   10. 각 계획에 대해 `client.submit_buy(p.ticker, p.qty, p.entry_price_tick,
       order_type="market")` — 장중 잡이므로 시장가로 체결을 확보한다.
       거부되면 `is_market_closed_reject(msg)`일 때는 `log.info`로만 남기고,
       아니면 `send_warning`.
   11. 접수된 주문마다 `repo.insert_position(...)`를 `__main__.py`의 기존 호출과 **같은
       인자 구성**으로 부르고, `send_info`로 알린다. 메시지 첫 줄은
       `"🔁 재배치 매수 접수"`로 시작해 아침 진입과 구별되게 한다.
   12. 주문 사이에 `time.sleep(_KIS_GAP_SEC)`.
   13. 접수 수를 반환한다.
5. underrun 경고 문구 (**D11** — 미달을 허용하고 알리기만 한다):
   ```
   ⚠️ 가동률 {plan.utilization_pct:.1f}% (목표 {plan.target_pct:.0f}%) — 배치 가능 {plan.deployable_won:,}원인데 진입 후보가 없어. 신호가 부족한 날이야.
   ```
   `plan.utilization_pct < rules.cash_deploy_underrun_warn_pct`이고
   `should_warn_underrun(오늘)`이 True일 때만 보낸다.
6. `dip_buy.py`에서 `_KIS_GAP_SEC`와 `is_market_closed_reject`를 import해 쓴다.
   복사하지 말 것 (같은 문자열 목록이 두 벌이 되면 갈라진다).

## Deliverables
- `src/orchestrator/cash_deploy.py` (신규)
- `tests/test_cash_deploy.py` (신규)

## Verify
`client` / `repo`는 전부 가짜 객체로 주입한다 — **KIS도 실제 DB도 건드리지 않는다**.
`send_info` / `send_warning`은 리스트에 append하는 람다로 준다.
`select_entries`는 실제 함수를 쓰되 `vote_entry`를 monkeypatch한다
(`tests/test_entry_risk_sizing.py`의 `monkeypatch.setattr(mod, "vote_entry", fake_vote)`
선례를 따를 것).

1. 정상: 총자산 30,000,000 / 보유 0 / 매수여력 25,000,000 / 후보 2건 →
   주문이 접수되고 반환값이 접수 수와 같다. 발주 명목 합계가 `deployable_won` 이하.
2. **D7 증거 (가장 중요한 케이스)**: PENDING 포지션
   (`entry_price_target=10_000, entry_qty=1_000` → 명목 10,000,000)이 있을 때
   `deployable_won`이 PENDING이 없을 때보다 정확히 10,000,000 적다. 이 케이스가
   30분 뒤 같은 현금을 두 번 쓰는 사고를 막는 유일한 방어다.
3. 정상: `compute_pending_notional([])` == 0;
   `entry_price_target=None`인 포지션은 0으로 센다; 2건이면 합산된다.
4. 경계: `cash_deploy_enabled=False` → 반환 0, `client.get_balance`가 **호출되지 않는다**.
5. 경계: `deployable_won < min_deploy_won` → 반환 0, 주문 없음.
6. 경계: 후보 0건 → 반환 0, 경고 1건 발송(가동률이 임계 아래일 때).
7. **하루 1회 경고**: 같은 조건으로 `run_cash_deploy`를 연속 2회 부르면 경고는 **1건**.
   `tmp_path`의 마커 경로를 주입해 검증한다.
8. 경계: 가동률이 `underrun_warn_pct`보다 **높으면** 후보가 없어도 경고하지 않는다.
9. 에러: `client.get_balance()`가 `None` → 반환 0, 경고 1건, **예외가 밖으로 새지 않는다**.
10. 에러: `client.get_deposit()`이 예외를 던짐 → `balance.cash`로 폴백해 계속 진행한다.
11. 에러: `submit_buy`가 `accepted=False`, `msg1="장종료"` → 경고 **없음**(정상 거부),
    `msg1="잔고 부족"` → 경고 **있음**.
12. **해외 배제**: `asset_class=overseas_stock` 후보만 주면 `pick_candidates`가 빈
    리스트를 반환하고 주문이 나가지 않는다.
13. **중복 배제**: `repo.is_duplicate`가 True를 반환하는 종목은 `pick_candidates`가 버린다.
14. **상한**: 후보 10건을 주면 `pick_candidates`가 `max_candidates_per_run`(4)건만
    반환하고, score 내림차순 상위 4건이다 (D8 — 한 회 실행 시간을 락 만료 아래로 묶는다).
15. **D9 증거**: `daily_entries_today=12`(아침 예산 소진)여도 재배치가 진입한다.
    `quota_override`에 `cash_deploy_max_daily_entries`만 넣었다면 여기서 빨개진다.

명령: `.venv/bin/python -m pytest tests/test_cash_deploy.py -q` → 통과.
이어서 `.venv/bin/python -m pytest -q` → 전체 통과.

## Out of scope
- `__main__.py` 배선과 `--deploy-cash` 플래그 — 태스크 06.
- launchd 등록 — 다른 세션(t4)이 이미 하고 있다. `cli/` 를 열지 말 것.
- `repository.py`에 새 메서드 추가 — t3 세션이 그 파일을 소유한다. 기존 메서드만 쓴다.
- 해외 재배치, 지수 ETF 채움, 피라미딩 — 만들지 않는다.
