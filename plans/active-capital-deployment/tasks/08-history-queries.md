# Task 08: 청산 내역 조회와 손익 요약을 Repo에 추가한다

## Objective
`Repo.get_closed_positions(limit)`이 최근 청산 포지션을 최신순으로 반환하고,
`Repo.get_history_summary(limit)`이 같은 구간의 승률·누적손익을 반환한다.

## Wiki pages (read these first, only these)
- `wiki/databases/query-optimization/existence-and-count-checks.md` — 건수·집계를
  전체 로우를 파이썬으로 끌어와 세지 않는 규칙. 기존 `get_today_entries`가 바로 그
  안티패턴(`.scalars().all()` 후 `len()`)이니 **새 코드에서 따라하지 말 것**.
- `wiki/testing/data/test-data-and-isolation.md` — 시간 의존 로직과 픽스처 격리.

## Inputs
- 태스크 07의 산출물: pragma가 걸린 `get_engine`
- 기존 스키마: `Position` (`status`, `exit_at`, `exit_price`, `exit_reason`,
  `pnl_won`, `pnl_pct`, `entry_price_actual`, `entry_price_target`, `entry_qty`,
  `entry_at`, `entry_strategy_id`, `ticker`, `name`)
- 바인딩되는 결정: **D12 없음** — 이 태스크는 읽기 전용

## Steps
1. `src/storage/repository.py`에 dataclass를 하나 둔다. ORM `Position` 객체를 세션
   밖으로 그대로 넘기면 detached 인스턴스 접근이 되므로, **필요한 값만 담은 평범한
   dataclass로 변환해서 반환한다**:
   ```python
   @dataclass(frozen=True)
   class ClosedTrade:
       id: int
       ticker: str
       name: str
       qty: int
       entry_price: int
       exit_price: int
       pnl_won: int
       pnl_pct: float
       exit_reason: str
       entry_at: datetime | None
       exit_at: datetime | None
       strategy_id: str | None
   ```
2. `Repo.get_closed_positions`를 추가한다:
   ```python
   def get_closed_positions(self, limit: int = 10) -> list[ClosedTrade]:
       """최근 청산 포지션 (exit_at 최신순). limit 은 1~50 으로 clamp."""
   ```
   - `limit`을 `max(1, min(int(limit), 50))`으로 clamp한다. 상한 50은 텔레그램
     메시지 4096자 제한 때문이다 (한 줄 ~70자 × 50 = 3,500자).
   - `select(Position).where(Position.status == "CLOSED").order_by(Position.exit_at.desc(), Position.id.desc()).limit(n)`
     — **`exit_at`이 NULL인 CLOSED 로우가 섞여도 순서가 흔들리지 않게 `id`를 tie-breaker로 둔다.**
   - `entry_price`는 `entry_price_actual or entry_price_target or 0`
     (`close_position`이 손익을 계산할 때 쓰는 것과 같은 폴백).
   - `qty`는 `entry_qty or 0`.
   - `pnl_won`/`pnl_pct`가 `None`이면 0 / 0.0으로 채운다.
3. `Repo.get_history_summary`를 추가한다:
   ```python
   @dataclass(frozen=True)
   class HistorySummary:
       trades: int
       wins: int
       losses: int
       win_rate_pct: float
       total_pnl_won: int

   def get_history_summary(self, limit: int = 10) -> HistorySummary: ...
   ```
   - 같은 구간(최근 `limit`건, 같은 clamp·같은 정렬)을 대상으로 한다. 요약과 목록이
     다른 구간을 보면 사용자가 합을 검산할 때 어긋난다.
   - `wins = pnl_won > 0`, `losses = pnl_won < 0`. **`pnl_won == 0`은 어느 쪽도
     아니다** — `wins + losses`가 `trades`보다 작을 수 있다.
   - `win_rate_pct = wins / trades * 100`, `trades == 0`이면 `0.0`.
4. 구현은 `get_closed_positions`를 재사용해도 된다 (같은 clamp/정렬을 두 번 쓰지
   않는 것이 요약-목록 불일치를 막는 가장 단순한 방법).

## Deliverables
- `src/storage/repository.py` (수정 — 추가만, 기존 메서드 변경 금지)
- `tests/test_trade_history.py` (신규)

## Verify
`tmp_path`의 실제 sqlite 파일 + `create_all`로 픽스처를 만든다. 기존
`tests/conftest.py`에 재사용할 픽스처가 있으면 쓰고, 없으면 이 파일 안에 만든다.

1. 정상: CLOSED 3건(pnl +1000, -500, +200)을 넣고 `get_closed_positions(10)` →
   3건, `exit_at` 최신순, 각 필드값이 넣은 값과 일치.
2. 정상: `get_history_summary(10)` → `trades=3, wins=2, losses=1,
   win_rate_pct≈66.67, total_pnl_won=700`.
3. **OPEN/PENDING 제외**: OPEN 1건 + PENDING 1건을 추가로 넣어도 위 두 결과가
   변하지 않는다.
4. 경계: `pnl_won == 0`인 청산 1건 추가 → `trades=4, wins=2, losses=1`
   (`wins+losses != trades`), `win_rate_pct = 50.0`.
5. 경계: 빈 DB → `get_closed_positions()` == `[]`,
   `get_history_summary()` == `trades=0, win_rate_pct=0.0, total_pnl_won=0`
   (ZeroDivisionError 없음).
6. 경계 — clamp 하한: `get_closed_positions(0)`과 `get_closed_positions(-5)`가
   각각 1건을 반환한다 (예외 아님).
7. 경계 — clamp 상한: CLOSED 60건을 넣고 `get_closed_positions(999)` → **50건**.
8. 경계 — NULL 내성: `pnl_won=None`, `exit_at=None`, `entry_price_actual=None`인
   CLOSED 로우를 넣어도 예외 없이 반환되고 `pnl_won == 0`,
   `entry_price == entry_price_target`.
9. **요약-목록 정합**: CLOSED 60건에서 `get_history_summary(5).total_pnl_won`이
   `sum(t.pnl_won for t in get_closed_positions(5))`와 같다.

명령: `.venv/bin/python -m pytest tests/test_trade_history.py -q` → 통과.
이어서 `.venv/bin/python -m pytest -q` → 전체 통과.

## Out of scope
- 텔레그램 명령/포맷팅 — 태스크 09.
- 기간 필터(`/history 7d`) — 영기가 "최근 N건 + 손익 요약"을 택했다. 만들지 않는다.
- 기존 메서드(`get_today_entries` 등)의 안티패턴 수정 — 이번 변경과 무관.
