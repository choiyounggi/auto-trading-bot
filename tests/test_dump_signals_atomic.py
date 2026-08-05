"""dump_signals_json 의 원자적 쓰기 + 트레이더와 동일한 디렉토리 해석 테스트.

트레이더(16:45)가 신호 잡(16:30)의 반쯤 쓰인 파일을 파싱하는 일이 없어야 한다.
네트워크(KRX/Telegram)·Keychain 을 건드리지 않는다 — 순수 파일 I/O 만 검증한다.
"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from src.orchestrator.signal_loader import SIGNAL_DIR_ENV
from src.signal.data.dump_signals import dump_signals_json

TODAY = date(2026, 8, 5)


def _dump(out_dir: Path | str | None = None, **kwargs) -> Path:
    """필수 인자를 빈 값으로 채운 dump_signals_json 호출 (빈 입력 경계값)."""
    params = {
        "today": TODAY,
        "buys": [],
        "cautions": [],
        "panels": {},
        "llm_results": {},
        "short_balances": {},
        "macro_snaps": [],
        "macro_news": [],
        "out_dir": out_dir,
    }
    params.update(kwargs)
    return dump_signals_json(**params)


# --- 정상 ---------------------------------------------------------------


def test_explicit_out_dir_writes_json_that_round_trips(tmp_path):
    """명시적 out_dir 은 그대로 쓰인다 (기존 호출자·테스트 호환)."""
    out_path = _dump(out_dir=tmp_path)

    assert out_path == tmp_path / "2026-08-05.json"
    assert out_path.exists()

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["date"] == "2026-08-05"
    assert data["buys"] == []
    assert data["cautions"] == []


def test_out_dir_none_resolves_through_the_traders_resolver(tmp_path, monkeypatch):
    """out_dir=None 이면 트레이더가 읽는 그 디렉토리(KIS_TRADER_SIGNAL_DIR)에 쓴다."""
    monkeypatch.setenv(SIGNAL_DIR_ENV, str(tmp_path))

    out_path = _dump(out_dir=None)

    assert out_path == tmp_path / "2026-08-05.json"
    assert out_path.exists()


def test_no_tmp_residue_after_a_successful_write(tmp_path):
    _dump(out_dir=tmp_path)

    entries = list(tmp_path.iterdir())
    assert len(entries) == 1, f"임시 파일 잔재: {[e.name for e in entries]}"
    assert entries[0].name == "2026-08-05.json"


def test_name_suffix_file_also_leaves_no_tmp_residue(tmp_path):
    """`.us` 처럼 점을 포함한 suffix 에서도 임시 파일 이름이 어긋나지 않는다."""
    out_path = _dump(out_dir=tmp_path, name_suffix=".us")

    assert out_path.name == "2026-08-05.us.json"
    assert [e.name for e in tmp_path.iterdir()] == ["2026-08-05.us.json"]


# --- 에러 ---------------------------------------------------------------


def test_replace_failure_propagates_and_leaves_no_tmp(tmp_path, monkeypatch):
    """os.replace 가 실패하면 예외는 그대로 올라오고 .tmp 는 남지 않는다."""
    def boom(src, dst):
        raise OSError("replace 실패")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="replace 실패"):
        _dump(out_dir=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_write_failure_propagates_and_cleanup_does_not_mask_it(tmp_path, monkeypatch):
    """임시 파일 생성 자체가 실패해도 정리 로직이 다른 예외로 덮어쓰지 않는다."""
    real_write_text = Path.write_text

    def boom(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise OSError("디스크 꽉 참")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", boom)

    with pytest.raises(OSError, match="디스크 꽉 참"):
        _dump(out_dir=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_relative_signal_dir_env_raises_value_error(tmp_path, monkeypatch):
    """상대 경로는 launchd 작업 디렉토리 기준으로 조용히 어긋난다 → 즉시 실패."""
    monkeypatch.setenv(SIGNAL_DIR_ENV, "relative/signals")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError) as exc:
        _dump(out_dir=None)

    assert "must be an absolute path" in str(exc.value)
    assert "relative/signals" in str(exc.value)
    assert list(tmp_path.iterdir()) == []


# --- 경계값 -------------------------------------------------------------


def test_missing_destination_directory_is_created(tmp_path):
    out_dir = tmp_path / "a" / "b" / "signals"
    assert not out_dir.exists()

    out_path = _dump(out_dir=out_dir)

    assert out_dir.is_dir()
    assert out_path.exists()


def test_missing_destination_directory_is_created_for_resolved_dir(tmp_path, monkeypatch):
    """resolver 로 해석된 경로도 없으면 만든다 (첫 실행 시나리오)."""
    out_dir = tmp_path / "not-yet"
    monkeypatch.setenv(SIGNAL_DIR_ENV, str(out_dir))

    out_path = _dump(out_dir=None)

    assert out_path == out_dir / "2026-08-05.json"
    assert out_path.exists()


def test_rewriting_the_same_date_overwrites_cleanly(tmp_path):
    """재실행 가능해야 한다 — 이어붙이거나 잘린 잔재가 남으면 안 된다."""
    _dump(out_dir=tmp_path, fundamentals={"005930": {"note": "x" * 5000}})
    second = _dump(out_dir=tmp_path, fundamentals={})

    assert [e.name for e in tmp_path.iterdir()] == ["2026-08-05.json"]

    text = second.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["fundamentals"] == {}
    assert "005930" not in text


def test_temp_file_is_invisible_to_a_json_globber(tmp_path, monkeypatch):
    """이 태스크의 존재 이유: *.json 을 훑는 리더가 부분 파일을 보면 안 된다."""
    seen: dict = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src"] = Path(src)
        seen["dst"] = Path(dst)
        seen["tmp_exists"] = Path(src).exists()
        seen["json_glob"] = sorted(p.name for p in tmp_path.glob("*.json"))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)

    out_path = _dump(out_dir=tmp_path)

    assert seen["tmp_exists"], "os.replace 이전에 임시 파일이 존재해야 한다"
    assert seen["src"] != seen["dst"]
    assert not seen["src"].name.endswith(".json"), seen["src"].name
    assert seen["src"].parent == seen["dst"].parent, "크로스 파일시스템 rename 은 원자적이지 않다"
    assert seen["json_glob"] == [], "쓰는 도중 *.json 에 부분 파일이 보인다"
    assert out_path.name.endswith(".json")
