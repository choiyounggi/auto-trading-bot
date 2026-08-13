# Task 01: 가동률 config 키를 추가하고 진입 파라미터를 상향한다

## Objective
`config/trading_rules.yaml`에 `capital.target_utilization_pct` / `capital.cash_buffer_pct`
와 `cash_deploy` 블록이 생기고, `load_rules()`가 이를 `TradingRules` 필드로 읽는다.
기존 진입 파라미터가 D15 값으로 상향된다.

## Wiki pages (read these first, only these)
- `wiki/backend/common/api-design/unenforced-declarations.md` — 신규 config 키가
  "기록되지만 아무 코드도 쓰지 않는 노브"가 되지 않게 하는 규칙. 특히 "recorded but
  unenforced" 구분과 §5(구현 코드에서 파생된 열거표).

## Inputs
- 기존 파일: `config/trading_rules.yaml`, `src/guardrails/rules.py`
- 바인딩되는 결정: **D6**(신규 키마다 동작 변화 테스트), **D15**(상향값), **D16**(목표/버퍼),
  **D8**(틱당 후보 4), **D9**(별도 일일 예산), **D11**(경고 임계)

## Steps
1. `config/trading_rules.yaml`의 `capital:` 블록에 아래 2개 키를 추가한다. 기존
   `phase` / `paper_capital_won` / `real_capital_won`은 건드리지 않는다.
   ```yaml
   capital:
     phase: 3
     paper_capital_won: 30_000_000
     real_capital_won: 0
     # [적극 투입 2026-08-12] 총자산 대비 목표 투자 비중. cash_buffer_pct 는
     # 주문 거부·수수료·부분체결 여유로 남기는 현금 비중.
     target_utilization_pct: 90.0
     cash_buffer_pct: 10.0
   ```
2. 같은 파일 최상위에 `cash_deploy` 블록을 신설한다 (`dip_buy` 블록 바로 위):
   ```yaml
   # 장중 현금 재배치 — 청산으로 회수된 현금을 같은 날 다시 투입한다.
   # 아침 진입(guardrails.max_daily_entries)과 예산 카운터가 분리되어 있다.
   cash_deploy:
     enabled: true
     max_daily_entries: 6         # 재배치 전용 일일 진입 상한
     max_candidates_per_run: 4    # 틱당 LLM 평가 상한 (4×180s=12분 < 락 15분 < 간격 30분)
     min_deploy_won: 500_000      # 미달분이 이보다 작으면 발주하지 않음 (잔돈 주문 방지)
     underrun_warn_pct: 70.0      # 가동률이 이 아래면 하루 1회 경고
   ```
3. `guardrails:` 블록의 값을 D15대로 바꾼다. 각 줄 끝에 기존 스타일(`# [사이징 확대] 3.0 → 10.0`)
   과 같은 형식으로 `# [적극 투입] 10.0 → 15.0` 주석을 남긴다.
   - `max_size_pct: 10.0` → `15.0`
   - `min_size_pct: 3.0` → `5.0`
   - `max_position_count: 12` → `18`
   - `max_daily_entries: 8` → `12`
   - `risk_per_trade_pct`는 **0.5 그대로 둔다** (plan.md "상향값 근거" 참조).
4. `strategy_budgets:`의 각 전략 `max_daily_entries: 2` → `3`. `mode: filter_only`인
   `value_quality`는 건드리지 않는다.
5. `src/guardrails/rules.py`의 `TradingRules`에 필드를 추가한다. 기존 필드 순서를
   흐트러뜨리지 말고 `risk_per_trade_pct` 아래에 새 그룹으로 넣는다:
   ```python
   # 적극 투입 — 총자산 대비 목표 가동률
   target_utilization_pct: float = 90.0
   cash_buffer_pct: float = 10.0

   # 장중 현금 재배치
   cash_deploy_enabled: bool = True
   cash_deploy_max_daily_entries: int = 6
   cash_deploy_max_candidates_per_run: int = 4
   cash_deploy_min_deploy_won: int = 500_000
   cash_deploy_underrun_warn_pct: float = 70.0
   ```
6. `load_rules()`에서 `cap = raw.get("capital", {})`, `cd = raw.get("cash_deploy", {})`를
   추가하고 위 7개 필드를 매핑한다. 기존 매핑 줄들과 같은 스타일(`cap.get("...", 기본값)`).
7. 테스트를 `tests/test_signal_config_files.py`가 아니라 **새 파일
   `tests/test_capital_rules.py`** 에 쓴다. D6에 따라 각 키마다 "값을 바꾸면 로드 결과가
   바뀐다"를 검증한다 — 아래 Verify 참조.

## Deliverables
- `config/trading_rules.yaml` (수정)
- `src/guardrails/rules.py` (수정)
- `tests/test_capital_rules.py` (신규)

## Verify
`tests/test_capital_rules.py`가 다음을 **전부** 커버해야 한다 (정상 / 에러 / 경계값):

1. 정상: 실제 `config/trading_rules.yaml`을 `load_rules()`로 읽어
   `target_utilization_pct == 90.0`, `cash_buffer_pct == 10.0`,
   `cash_deploy_max_candidates_per_run == 4`, `max_size_pct == 15.0`,
   `min_size_pct == 5.0`, `max_position_count == 18`, `max_daily_entries == 12`.
2. 정상: `strategy_budgets`의 모든 활성 전략이 `strategy_max_daily_entries`에서 3.
3. **D6 강제 — 신규 7개 키 전부**. `tmp_path`에 최소 YAML을 써서 아래 7개를 **모두**
   dataclass 기본값과 다른 값으로 주고, 로드 결과가 파일 값과 일치함을 확인한다.
   `pytest.mark.parametrize`로 7행을 돌리는 것이 가장 짧다:

   | YAML 경로 | 시험값 | `TradingRules` 필드 |
   |---|---|---|
   | `capital.target_utilization_pct` | `55.0` | `target_utilization_pct` |
   | `capital.cash_buffer_pct` | `25.0` | `cash_buffer_pct` |
   | `cash_deploy.enabled` | `false` | `cash_deploy_enabled` |
   | `cash_deploy.max_daily_entries` | `2` | `cash_deploy_max_daily_entries` |
   | `cash_deploy.max_candidates_per_run` | `9` | `cash_deploy_max_candidates_per_run` |
   | `cash_deploy.min_deploy_won` | `123_456` | `cash_deploy_min_deploy_won` |
   | `cash_deploy.underrun_warn_pct` | `33.0` | `cash_deploy_underrun_warn_pct` |

   기본값이 아니라 **파일 값이 이긴다**는 증거다. 7개 중 하나라도 로더에서 빠지면
   그 키는 "기록되지만 아무 코드도 읽지 않는 노브"가 된다 — 위키가 말하는 정확히 그 실패.

   각 키가 **실제 동작을 바꾼다**는 증명은 이 태스크가 아니라 소비처에서 한다:
   `target_utilization_pct` / `cash_buffer_pct` → 태스크 02,
   `max_size_pct` / `min_size_pct` → 태스크 03·04,
   `cash_deploy_*` 5개 → 태스크 05. 여기서는 **로더까지**가 검증 범위다.
4. 에러: 존재하지 않는 경로를 주면 `TradingRules()` 기본값이 나온다 (기존 폴백 유지).
5. 경계: `capital:` 블록도 `cash_deploy:` 블록도 없는 YAML → 7개 신규 필드가 전부
   dataclass 기본값. (구버전 config 하위 호환)
6. 경계: `cash_deploy: {}` (빈 dict) → 역시 전부 기본값. `None`으로 새지 않는다.
7. **정합성 회귀 가드** — `risk_per_trade_pct / max_stop_loss_pct * 100 > max_size_pct`
   를 단언한다. plan.md "상향값 근거"의 부등식이 깨지면 `size_pct` 상향이 무의미해지므로
   이 테스트가 빨개져야 한다.

명령: `/Users/choeyeong-gi/Desktop/workspace/auto-trading-bot/.venv/bin/python -m pytest tests/test_capital_rules.py -q`
→ 전부 통과. 이어서 `-m pytest -q` 전체 321건+ 통과.

## Out of scope
- 이 값들을 **사용하는** 코드 (태스크 02·03이 한다). 이 태스크는 로드까지만.
- `overseas:` / `paper_probe:` / `dip_buy:` 블록 — 손대지 않는다.
