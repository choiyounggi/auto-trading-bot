# Task 06: `--deploy-cash` 플래그를 orchestrator에 배선한다

## Objective
`python -m src.orchestrator --deploy-cash`가 최신 신호로 재배치를 1회 실행하고 종료한다.
같은 파일에서 아침 진입 경로의 `AccountSnapshot` 생산자도 새 계약으로 갱신된다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/change-impact/call-site-enumeration.md` — 열거된 마지막
  `AccountSnapshot(` 생산자와 유일한 `select_entries(` 호출부를 갱신하는 근거.
- `wiki/platforms/processes/background-services.md` — launchd 잡의 최소 환경/로그/
  종료코드. 특히 "Verify by observing, not by launch exit code".

## Inputs
- 태스크 05의 산출물: `src.orchestrator.cash_deploy.run_cash_deploy(client, repo, rules,
  candidates, signals, send_info, send_warning) -> int`
- 태스크 02·03의 산출물: `compute_capital_plan`, `position_eval_won`,
  `AccountSnapshot.total_asset_won/.invested_won/.pending_notional_won`
- 기존 파일: `src/orchestrator/__main__.py` — `parse_args`(`--dip-only`, `--carry-over`
  선례), `--dip-only` 조기 반환 블록, 계좌 스냅샷 블록(line 180 근처),
  `select_entries` 호출부(line 192)
- **고정 계약 (D19)**: 플래그 철자는 정확히 `--deploy-cash`. launchd 쪽(t4 세션)이
  `["-m", "src.orchestrator", "--deploy-cash"]`로 이미 배선했다.
- 바인딩되는 결정: **D5**, **D19**

## Steps
1. `parse_args`에 인자를 추가한다. `--dip-only`와 같은 형태·같은 톤의 help:
   ```python
   p.add_argument("--deploy-cash", action="store_true",
                  help="장중 현금 재배치만 실행하고 종료 (09:30~15:00 30분 간격 cashDeploy 잡 전용).")
   ```
   argparse는 `--deploy-cash`를 `args.deploy_cash`로 노출한다.
2. `--dip-only` 블록 **바로 아래**에 재배치 조기 반환 블록을 넣는다. dip-only와 같은
   구조(설정 확인 → 세션 → 실행 → 로그 → `return 0`)를 지킨다:
   ```python
   if args.deploy_cash:
       if not rules.cash_deploy_enabled:
           log.info("cash-deploy disabled (config)")
           return 0
       # 재배치는 최신(전일 포함) 신호를 쓴다 — 오늘 신호는 16:30에야 생성된다.
       signal_dir = resolve_signal_dir()
       target_date = latest_signal_date(signal_dir, "")
       if target_date is None:
           log.warning("cash-deploy: 사용 가능한 signal 파일 없음 (%s)", signal_dir)
           return 0
       signals = load_signal(
           signal_dir=signal_dir, target_date=target_date,
           schema_path=Path("schemas/signal-v1.json"),
           max_age_min=10080, name_suffix="",
       )
       if signals is None:
           log.warning("cash-deploy: signal 로드 실패 (date=%s)", target_date)
           return 0
       buys = filter_buy_candidates(signals, min_score=rules.entry_signal_score_min)
       try:
           with KisClient(mode="paper").session() as _cc:
               n = run_cash_deploy(_cc, Repo(), rules, buys, signals,
                                   send_info, send_warning)
               log.info("cash-deploy: %d건 재배치 매수", n)
       except Exception as e:
           log.warning("cash-deploy 실패: %s", e)
       return 0
   ```
   `max_age_min=10080`(7일)은 `--carry-over`가 쓰는 값과 같다 — 최신 파일만 고르므로
   신선도 검사는 사실상 무력하고, 주말·연휴 갭을 덮는다.
   **예외를 삼키고 `return 0`** 하는 이유: launchd 잡이 비정상 종료하면 `guardScript`의
   `trap`이 락을 풀더라도 운영자에게 남는 신호가 로그뿐이다. dip-only 블록이 이미
   같은 방식이다.
3. 상단 import에 `from src.orchestrator.cash_deploy import run_cash_deploy`를 추가한다.
   `run_dip_buy` import 바로 아래.
4. **열거된 마지막 생산자 갱신 (D5)** — line 180 근처의 아침 진입 `AccountSnapshot(...)`
   에 새 필드를 채운다:
   ```python
   from src.orchestrator.capital import position_eval_won   # 상단 import

   account = AccountSnapshot(
       cash_won=balance.cash,
       total_asset_won=balance.total_eval,
       invested_won=position_eval_won(balance.positions),
       open_positions=active_count,
       daily_pnl_pct=0.0,
       daily_entries_today=daily_entries,
       cash_usd=overseas_cash_usd,
   )
   ```
   `pending_notional_won`은 여기서는 넣지 않는다 — 아침 진입은 하루 첫 잡이라 미체결
   잔여가 없고, 넣으려면 조회가 하나 더 붙는다. 기본값 0이 맞는 값이다.
5. **아침 진입 경로에도 배치 예산을 건다.** line 192의 유일한 `select_entries` 호출부를
   갱신한다. 이걸 빼면 아침 진입만 상한 없이 발주해 재배치가 쓸 현금을 먹는다:
   ```python
   cap = compute_capital_plan(
       total_asset_won=balance.total_eval,
       position_eval_won=position_eval_won(balance.positions),
       pending_notional_won=0,
       buying_power_won=(c.get_deposit() or balance.cash),
       rules=rules,
   )
   log.info("자본: 총자산=%d, 투자중=%d, 가동률=%.1f%%, 배치가능=%d",
            cap.total_asset_won, cap.invested_won, cap.utilization_pct, cap.deployable_won)
   plans, skips = select_entries(candidates, signals, account, rules, repo=repo,
                                 deployable_won=cap.deployable_won)
   ```
   `c.get_deposit()`은 이미 열린 세션 `c` 안에서 부른다. 예외가 나면 `balance.cash`로
   폴백하도록 `try/except`로 감싼다 (태스크 05와 같은 처리).
6. 기존 `log.info("계좌: cash=%d원, ...")` 줄은 남겨둔다 — 위 자본 로그와 함께 있어야
   예수금과 총자산을 대조할 수 있다.
7. 편집 후 `grep -rn "AccountSnapshot\|select_entries" src tests scripts`를 재실행해
   구계약 호출부가 **0건**임을 확인한다 (계획 전체의 완료 조건).

## Deliverables
- `src/orchestrator/__main__.py` (수정)
- `tests/test_cash_deploy.py` (태스크 05가 만든 파일에 CLI 케이스 추가)

## Verify
`__main__.run()`은 KIS와 파일시스템을 타므로, **argparse와 조기 반환 분기만** 검증한다.
`monkeypatch`로 `KisClient` / `Repo` / `run_cash_deploy` / `load_signal` /
`latest_signal_date` / `resolve_signal_dir`를 대체한다.

1. 정상: `parse_args(["--deploy-cash"]).deploy_cash is True`이고, 다른 플래그는 기본값
   (`dip_only is False`, `carry_over is False`, `asset_class == "all"`).
2. 정상: `run(["--deploy-cash"])`가 `0`을 반환하고 `run_cash_deploy`가 **정확히 1회**
   호출된다. 넘어간 `rules`가 로드된 `TradingRules`이고 `candidates`가
   `filter_buy_candidates` 결과다.
3. **경로 분리**: `run(["--deploy-cash"])`는 아침 진입 경로(`select_entries`)를
   **호출하지 않는다**. 반대로 `run([])`은 `run_cash_deploy`를 호출하지 않는다.
   두 경로가 섞이면 하루치 예산이 두 번 소모된다.
4. 경계: `cash_deploy_enabled=False` → 반환 0, `run_cash_deploy` 미호출,
   `KisClient` 미생성.
5. 경계: `latest_signal_date`가 `None` → 반환 0, `run_cash_deploy` 미호출, 경고 로그.
6. 경계: `load_signal`이 `None` → 반환 0, `run_cash_deploy` 미호출.
7. 에러: `run_cash_deploy`가 예외를 던져도 `run(["--deploy-cash"])`가 **0을 반환**한다
   (launchd 잡이 비정상 종료하지 않는다).
8. **D19 계약**: `--deploy-cash`가 argparse에 존재한다.
   `parse_args(["--deploy-cash"])`가 `SystemExit`을 던지지 않는 것으로 확인.
   철자가 다르면 launchd 잡이 매 30분마다 usage 에러로 죽는다.
9. 회귀: 기존 `--dip-only` / `--carry-over` / `--asset-class overseas_stock` 동작이
   그대로다 (기존 테스트가 있으면 통과, 없으면 `parse_args` 단언 1건씩 추가).

명령: `.venv/bin/python -m pytest tests/test_cash_deploy.py -q` → 통과.
이어서 `.venv/bin/python -m pytest -q` → 전체 통과.

## Out of scope
- `run_cash_deploy` 내부 로직 — 태스크 05가 이미 끝냈다.
- launchd / `cli/**` — 다른 세션(t4) 소유. 열지 말 것.
- 해외 잡(`--asset-class overseas_stock`) 경로 변경.
- 실제 KIS 호출이나 실제 signal 파일을 읽는 테스트.
