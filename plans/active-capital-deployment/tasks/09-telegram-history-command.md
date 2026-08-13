# Task 09: 텔레그램 `/history` 명령을 추가한다

## Objective
텔레그램에서 `/history` 또는 `/history 20`, `/내역`을 보내면 최근 청산 내역과
승률·누적손익 요약이 온다. 명령 목록(`setMyCommands`)과 `/help`에도 노출된다.

## Wiki pages (read these first, only these)
- `wiki/testing/quality/behavior-not-implementation.md` — 순수 포맷 함수를 테스트
  대상으로 삼고 네트워크(Telegram/KIS)를 테스트에 끌어들이지 않기 위해.
- `wiki/testing/quality/minimum-case-set.md` — 케이스 선정.

## Inputs
- 태스크 08의 산출물: `Repo.get_closed_positions(limit) -> list[ClosedTrade]`,
  `Repo.get_history_summary(limit) -> HistorySummary`, `ClosedTrade`, `HistorySummary`
- 기존 파일: `src/agent/telegram_agent.py` — `_fmt_won`, `_balance_text`,
  `_positions_text` 같은 **모듈 레벨 순수 포맷 함수**가 이미 있고, `Agent.handle_text`가
  `low in (...)` 분기로 명령을 라우팅하며, `Telegram.set_commands`가 명령 목록을 올린다.
- 바인딩되는 결정: 없음 (표시 전용)

## Steps
1. `src/agent/telegram_agent.py`에 **모듈 레벨 순수 함수**를 추가한다. `Agent` 메서드
   안에서 문자열을 조립하면 테스트가 데몬을 세워야 한다. 기존 `_positions_text` 바로
   아래에 둔다:
   ```python
   def _history_text(trades, summary) -> str: ...
   ```
   포맷 (기존 Markdown 스타일과 일치시킬 것 — `*굵게*`, `` `코드` ``):
   ```
   *📜 거래 내역* (최근 3건)
   `005930` 삼성전자 6주
     243,000 → 253,750  +64,500원 (+4.42%)  _take_profit_  08/12
   `035720` 카카오 35주
     40,550 → 40,150  -14,000원 (-0.99%)  _stop_loss_  08/12
   ─────
   3건 · 승 2 / 패 1 (승률 66.7%)
   누적손익 *+50,500원*
   ```
   - 손익 부호는 항상 `+`/`-`를 붙인다 (`{v:+,}`).
   - `exit_at`이 `None`이면 날짜 자리에 `-`.
   - `exit_reason`이 비었으면 `_-_`.
   - 빈 목록이면 `"*📜 거래 내역*\n청산된 거래가 아직 없어."` 한 줄만 반환하고
     요약 블록은 붙이지 않는다.
2. `Agent`에 `_cmd_history(self, text: str)`를 추가한다. 기존 `_cmd_balance` 등과
   같은 `try/except` 형태를 지킨다:
   ```python
   def _cmd_history(self, text: str) -> None:
       parts = text.split()
       limit = 10
       if len(parts) > 1:
           try:
               limit = int(parts[1])
           except ValueError:
               self.tg.send("형식: `/history` 또는 `/history 20` (최대 50건)")
               return
       try:
           trades = self.repo.get_closed_positions(limit)
           summary = self.repo.get_history_summary(limit)
           self.tg.send(_history_text(trades, summary))
       except Exception as e:
           log.warning("history 오류: %s", e)
           self.tg.send(f"⚠️ 거래 내역 조회 실패: {e}")
   ```
   `limit` 범위 제한은 Repo가 clamp하므로 여기서 다시 하지 않는다 (한 곳에서만 검증).
3. `handle_text`의 라우팅에 한 줄을 추가한다. `/buy`/`/sell` 분기보다 **위**,
   `/buyable` 분기 아래에 둔다. `low.startswith`를 쓰는 이유는 인자를 받기 때문:
   ```python
   if low.startswith("/history") or low.startswith("/내역") or low == "내역":
       self._cmd_history(text); return
   ```
   **`/help` 분기보다 아래**여야 하고, `/buy`로 시작하는 분기와 겹치지 않는지 확인할 것
   (`/history`는 `/h`로 시작하므로 충돌 없음).
4. `Telegram.set_commands`의 `cmds` 리스트에 추가한다 — `status` 다음, `buyable` 앞:
   ```python
   {"command": "history", "description": "거래 내역 (최근 N건 + 손익 요약)"},
   ```
5. `_cmd_history`가 걸리는 `/help` 텍스트에 한 줄 추가한다. 기존
   `"/balance 잔고 · /positions 포지션 · /status 현황 · /buyable 해외매수가능\n"` 줄 뒤에:
   ```python
   "/history [N] 거래 내역 (기본 10건, 최대 50)\n"
   ```
6. `_cmd_nl`(자연어 LLM 경로)은 손대지 않는다.

## Deliverables
- `src/agent/telegram_agent.py` (수정)
- `tests/test_trade_history.py` (태스크 08이 만든 파일에 케이스 추가)

## Verify
`_history_text`는 순수 함수이므로 직접 호출해 검증한다. `Agent`/`Telegram`을 세우지 말 것.

1. 정상: `ClosedTrade` 3건(+64500 / -14000 / +200)과 그에 맞는 `HistorySummary`로
   호출 → 반환 문자열에 `"005930"`, `"삼성전자"`, `"+64,500원"`, `"-14,000원"`,
   `"승 2 / 패 1"`, `"66.7%"`가 모두 들어 있다.
2. 정상: 종목명·수량·진입가·청산가가 전부 문자열에 나타난다 (한 종목만으로 확인).
3. 경계 — 빈 목록: `_history_text([], HistorySummary(0, 0, 0, 0.0, 0))` →
   `"청산된 거래가 아직 없어"`가 있고 `"승률"`은 **없다**.
4. 경계 — NULL: `exit_at=None`, `exit_reason=""`인 거래 1건 → 예외 없이 렌더되고
   날짜 자리에 `"-"`가 있다.
5. 경계 — 손익 0: `pnl_won=0` 거래 → `"+0원"` 형태로 렌더되고 예외 없음.
6. 경계 — 메시지 길이: 50건을 렌더한 결과가 **4096자 이하**임을 단언한다.
   텔레그램 `sendMessage` 하드 리밋이라 넘으면 전송 자체가 실패한다.
7. 에러 — 인자 파싱: `Agent._cmd_history`를 **가짜 tg/repo를 주입해** 호출한다.
   `Telegram`과 `Repo`를 각각 최소 스텁 객체로 대체하고 (`Agent.__init__`을 거치지 않고
   `Agent.__new__(Agent)` 후 속성 주입), `"/history abc"` → 스텁 tg가 받은 메시지에
   `"형식:"`이 들어 있고 **repo 메서드는 호출되지 않았다**.
8. 에러 — repo 예외: repo 스텁이 `RuntimeError("boom")`을 던지면 tg가 받은 메시지에
   `"⚠️ 거래 내역 조회 실패"`가 들어 있고 예외가 밖으로 새지 않는다.
9. 라우팅: `set_commands`가 올리는 목록에 `"history"`가 있는지 —
   `Telegram.set_commands`를 부르지 말고, 소스에서 확인하기 어려우면 이 케이스는
   생략하고 대신 `/help` 문자열에 `"/history"`가 있는지를 `_cmd_history`가 아닌
   `handle_text` 스텁 호출로 확인한다.

명령: `.venv/bin/python -m pytest tests/test_trade_history.py -q` → 통과.
이어서 `.venv/bin/python -m pytest -q` → 전체 통과.

## Out of scope
- 기간 필터 / 페이지 넘김 / 인라인 버튼 — 만들지 않는다.
- 실제 텔레그램 API 호출 테스트 — 네트워크를 타지 않는다.
- 자연어(LLM) 경로에서 히스토리를 이해하게 만드는 것 — 슬래시 명령만.
