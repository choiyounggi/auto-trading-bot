# Task 07: SQLite 커넥션에 동시 접근 pragma를 건다

## Objective
`get_engine()`이 여는 모든 커넥션에 `busy_timeout`이 걸리고, DB 파일에 WAL과
외래키가 실제로 활성화된다. 현재 이 함수의 docstring은 "SQLite 엔진 + WAL + 외래키
활성"이라고 주장하지만 **코드는 셋 중 아무것도 하지 않는다**.

## 왜 지금인가
텔레그램 에이전트는 상주 데몬으로 이 DB를 읽고, launchd 잡들(orchestrator/monitor/
reconciler)이 같은 파일에 쓴다. `busy_timeout`이 없으면 락을 만난 커넥션이 기다리지
않고 즉시 `database is locked`를 던진다. 태스크 09가 새 읽기 경로(`/history`)를
추가하고, 태스크 05가 장중 30분마다 쓰는 새 writer를 추가하므로 양쪽 모두 늘어난다.

## Wiki pages (read these first, only these)
- `wiki/databases/sqlite/concurrent-access-for-a-read-api.md` — §1 WAL은 파일에
  1회, §2 `busy_timeout`은 **모든 커넥션**, §3 writer의 `synchronous=NORMAL`,
  그리고 "pragma 비용" edge case(WAL/synchronous는 init 1회, busy_timeout만 커넥션마다).

## Inputs
- 기존 파일: `src/storage/models.py` (`get_engine`, line 160 근처)
- 바인딩되는 결정: **D14**

## Steps
1. `src/storage/models.py`에 `event` import를 추가한다 (`from sqlalchemy import event`).
2. `get_engine`을 아래 형태로 바꾼다. 위키 §2/edge case대로 **커넥션마다 거는 것은
   `busy_timeout`과 `foreign_keys`뿐**이고, `journal_mode`/`synchronous`는 엔진 생성
   직후 1회만 건다:
   ```python
   def get_engine(db_path: Path | str = "data/trades.sqlite"):
       """SQLite 엔진 — WAL(1회) + busy_timeout/외래키(커넥션마다)."""
       p = Path(db_path).expanduser()
       engine = create_engine(f"sqlite:///{p}", future=True)

       @event.listens_for(engine, "connect")
       def _pragmas(dbapi_conn, _record):
           cur = dbapi_conn.cursor()
           cur.execute("PRAGMA busy_timeout=5000")
           cur.execute("PRAGMA foreign_keys=ON")
           cur.close()

       with engine.connect() as c:
           c.exec_driver_sql("PRAGMA journal_mode=WAL")
           c.exec_driver_sql("PRAGMA synchronous=NORMAL")
       return engine
   ```
3. `busy_timeout` 값 5000ms는 위키 §2의 예시값을 그대로 쓴다. 상수로 빼거나 설정
   가능하게 만들지 않는다 — 요청되지 않은 유연성이다.
4. `get_session_factory` / `create_all`은 건드리지 않는다.

## Deliverables
- `src/storage/models.py` (수정 — `get_engine`만)
- `tests/test_sqlite_pragmas.py` (신규)

## Verify
pragma는 "걸었다"가 아니라 **DB에 물어봐서** 확인한다. `tmp_path`에 실제 sqlite 파일을
만들어 검사한다 (`:memory:`는 WAL을 지원하지 않으므로 쓰지 말 것):

1. 정상: `get_engine(tmp_path / "t.sqlite")` 후
   `SELECT * FROM pragma_journal_mode` → `"wal"`.
2. 정상: 같은 엔진의 커넥션에서 `SELECT * FROM pragma_busy_timeout` → `5000`.
3. 정상: `SELECT * FROM pragma_foreign_keys` → `1`.
4. **커넥션마다 걸리는지** (핵심): 엔진에서 커넥션을 2번 따로 열어 **두 번째** 커넥션도
   `busy_timeout == 5000`임을 확인한다. `connect` 이벤트가 아니라 1회성 코드로
   잘못 구현하면 여기서 빨개진다.
5. 경계: 존재하지 않는 디렉터리의 경로를 주면 `get_engine` 자체는 예외를 던지지 않고
   (SQLite는 lazy) 실제 연결 시점에 `OperationalError` — 어느 쪽이든 **테스트에 실제
   관측된 동작을 적는다**. 추측으로 단언하지 말고 먼저 돌려보고 적을 것.
6. 경계: 같은 경로로 `get_engine`을 두 번 호출해도 예외 없이 두 엔진 모두 동작한다
   (WAL 재설정이 idempotent).
7. 회귀: `create_all(engine)` 후 `Position` 1건 insert → select 왕복이 성공한다.
   pragma 추가가 기존 ORM 경로를 깨지 않았다는 증거.

명령: `.venv/bin/python -m pytest tests/test_sqlite_pragmas.py -q` → 통과.
이어서 `.venv/bin/python -m pytest -q` → 321건+ 전체 통과.

## Out of scope
- 조회 메서드 추가 — 태스크 08.
- 텔레그램 명령 — 태스크 09.
- 스키마 변경·마이그레이션 — 이번 목표에 새 컬럼은 필요 없다.
