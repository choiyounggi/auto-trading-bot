"""get_engine() 동시 접근 pragma 검증 — 코드가 아니라 DB에 물어봐서 확인한다."""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.storage.models import Position, create_all, get_engine


def test_journal_mode_is_wal(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite")
    with engine.connect() as c:
        mode = c.execute(text("SELECT * FROM pragma_journal_mode")).scalar()
    assert mode == "wal"


def test_busy_timeout_is_5000(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite")
    with engine.connect() as c:
        timeout = c.execute(text("SELECT * FROM pragma_busy_timeout")).scalar()
    assert timeout == 5000


def test_foreign_keys_on(tmp_path):
    engine = get_engine(tmp_path / "t.sqlite")
    with engine.connect() as c:
        fk = c.execute(text("SELECT * FROM pragma_foreign_keys")).scalar()
    assert fk == 1


def test_busy_timeout_applies_to_second_connection(tmp_path):
    """connect 이벤트가 아니라 1회성 코드로 잘못 구현하면 여기서 빨개진다."""
    engine = get_engine(tmp_path / "t.sqlite")
    with engine.connect() as c1:
        c1.execute(text("SELECT 1"))
    with engine.connect() as c2:
        timeout = c2.execute(text("SELECT * FROM pragma_busy_timeout")).scalar()
    assert timeout == 5000


def test_nonexistent_directory_behavior(tmp_path):
    """관측된 실제 동작: get_engine이 WAL pragma를 걸려고 즉시 커넥션을 여므로,
    존재하지 않는 디렉터리에서는 get_engine 호출 시점에 OperationalError가 난다
    (lazy가 아니다 — 실행해서 확인함)."""
    bad_path = tmp_path / "no_such_dir" / "t.sqlite"
    with pytest.raises(OperationalError):
        get_engine(bad_path)


def test_get_engine_idempotent_same_path(tmp_path):
    db_path = tmp_path / "t.sqlite"
    engine1 = get_engine(db_path)
    with engine1.connect():
        pass
    engine2 = get_engine(db_path)
    with engine2.connect() as c:
        mode = c.execute(text("SELECT * FROM pragma_journal_mode")).scalar()
    assert mode == "wal"


def test_orm_roundtrip_after_pragmas(tmp_path):
    """pragma 추가가 기존 ORM insert/select 경로를 깨지 않는다는 증거."""
    engine = get_engine(tmp_path / "t.sqlite")
    create_all(engine)
    with Session(engine) as s:
        s.add(Position(ticker="005930", name="삼성전자", status="OPEN"))
        s.commit()

    with Session(engine) as s:
        rows = list(s.execute(select(Position)).scalars())
    assert len(rows) == 1
    assert rows[0].ticker == "005930"
