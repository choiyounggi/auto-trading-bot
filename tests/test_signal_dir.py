"""KIS_TRADER_SIGNAL_DIR 해석 테스트 — resolve_signal_dir 단일 코드 경로."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.orchestrator.signal_loader import DEFAULT_SIGNAL_DIR, resolve_signal_dir

ENV_KEY = "KIS_TRADER_SIGNAL_DIR"

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- 정상 ---------------------------------------------------------------


def test_env_value_is_used():
    assert resolve_signal_dir({ENV_KEY: "/tmp/sig"}) == Path("/tmp/sig")


def test_unset_falls_back_to_default():
    assert resolve_signal_dir({}) == DEFAULT_SIGNAL_DIR


def test_default_is_the_legacy_hardcoded_path():
    """저자의 가동 중인 설치가 깨지지 않아야 한다."""
    assert DEFAULT_SIGNAL_DIR == Path.home() / "stock-signal-bot" / "data" / "signals"


def test_env_defaults_to_os_environ_when_set(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "/var/signals")

    assert resolve_signal_dir() == Path("/var/signals")


def test_env_defaults_to_os_environ_when_unset(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)

    assert resolve_signal_dir() == DEFAULT_SIGNAL_DIR


# --- 에러 ---------------------------------------------------------------


def test_relative_path_raises_value_error():
    with pytest.raises(ValueError) as exc:
        resolve_signal_dir({ENV_KEY: "relative/path"})

    assert "must be an absolute path" in str(exc.value)
    assert "relative/path" in str(exc.value)


def test_bare_relative_name_raises_value_error():
    with pytest.raises(ValueError) as exc:
        resolve_signal_dir({ENV_KEY: "signals"})

    assert "must be an absolute path" in str(exc.value)


def test_dot_relative_path_raises_value_error():
    """launchd 작업 디렉토리 기준으로 조용히 어긋나는 대표 케이스."""
    with pytest.raises(ValueError) as exc:
        resolve_signal_dir({ENV_KEY: "./data/signals"})

    assert "must be an absolute path" in str(exc.value)


# --- 경계값 -------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "\t", "\n", " \t\n "])
def test_blank_values_fall_back_to_default(value):
    assert resolve_signal_dir({ENV_KEY: value}) == DEFAULT_SIGNAL_DIR


def test_tilde_is_expanded():
    assert resolve_signal_dir({ENV_KEY: "~/sigs"}) == Path.home() / "sigs"


def test_bare_tilde_is_expanded():
    assert resolve_signal_dir({ENV_KEY: "~"}) == Path.home()


def test_root_path_is_accepted():
    assert resolve_signal_dir({ENV_KEY: "/"}) == Path("/")


def test_trailing_slash_is_normalized_by_path():
    assert resolve_signal_dir({ENV_KEY: "/tmp/sig/"}) == Path("/tmp/sig")


def test_none_env_uses_os_environ(monkeypatch):
    """env=None 은 미지정과 동일하게 os.environ 을 읽는다."""
    monkeypatch.setenv(ENV_KEY, "/opt/sig")

    assert resolve_signal_dir(None) == Path("/opt/sig")


# --- 단일 코드 경로 (DoD: 두 호출자가 모두 resolver 를 쓴다) ------------


@pytest.mark.parametrize(
    "rel_path",
    ["src/orchestrator/__main__.py", "scripts/test_entry_decision.py"],
)
def test_callers_use_the_resolver_and_not_the_literal(rel_path):
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")

    assert "resolve_signal_dir" in source, f"{rel_path} 가 resolver 를 쓰지 않는다"
    assert not re.search(
        r'Path\.home\(\)\s*/\s*"stock-signal-bot"', source
    ), f"{rel_path} 에 하드코딩된 경로가 남아 있다"
