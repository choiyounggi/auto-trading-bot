# -*- coding: utf-8 -*-
"""KIS 호출 간격 공유 throttle 회귀 테스트 (issue #6).

배경: `_throttle()`이 인스턴스 단위 `_last_request_at`(monotonic)만 봐서,
프로세스 경계를 넘는 계좌 단위 '초당 거래건수' 제한을 지키지 못했다. 특히
cold start(새 인스턴스)의 첫 호출은 `_last_request_at = 0.0`이라 절대
대기하지 않았다 — 09:05 잡 시작 직후 balance 계열 실패가 전부 이 경로였다.

이제 상태는 계좌+모드 단위 공유 파일(`_throttle_path`)에 벽시계로 저장된다.
conftest의 autouse 픽스처가 이 경로를 tmp_path로 돌려놓는다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from src.broker import kis_client as kc
from src.broker.kis_client import KisClient

# 모듈 import 시점(= conftest의 autouse 픽스처가 적용되기 전)에 실제 함수를
# 붙잡아 둔다. 픽스처가 kc._throttle_path 를 매 테스트마다 tmp 람다로 바꿔치기
# 하므로, 그 뒤에 kc._throttle_path 를 참조하면 실제 함수가 아니라 그 람다를
# 검증하게 된다 — mode/cano 스코프 분리는 여기 붙잡은 원본으로 단언한다.
_REAL_THROTTLE_PATH = kc._throttle_path

REPO = Path(__file__).resolve().parents[1]

_CHILD = (
    "import time, sys;"
    "from src.broker.kis_client import KisClient;"
    "c = KisClient(mode='paper'); c.cano = '12345678';"
    "b = time.time(); c._throttle(); a = time.time();"
    "print(f'{b:.6f} {a:.6f}')"
)


def _client(mode: str = "paper", cano: str = "12345678") -> KisClient:
    """네트워크를 타지 않는다 — `_throttle()`은 HTTP를 하지 않는다."""
    c = KisClient(mode=mode)
    c.cano = cano
    return c


def _slept(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(kc.time, "sleep", lambda s: slept.append(s))
    return slept


def _write_stamp(mode: str, cano: str, value: float) -> None:
    kc._throttle_path(mode, cano).write_text(f"{value:.6f}", encoding="utf-8")


# ============================================================
# 정상
# ============================================================

def test_first_call_of_a_fresh_instance_waits_for_another_processs_stamp(monkeypatch):
    """이슈의 09:05 실패 재현: 다른 프로세스가 방금 호출했다는 파일이 있으면
    새 인스턴스의 첫 호출도 그 간격을 지켜야 한다."""
    _write_stamp("paper", "12345678", time.time())
    slept = _slept(monkeypatch)
    c = _client()

    c._throttle()

    assert len(slept) == 1
    assert 0 < slept[0] <= 1.05


def test_throttle_writes_the_call_time_to_the_shared_file(monkeypatch):
    slept = _slept(monkeypatch)
    c = _client()

    c._throttle()

    stamp = float(kc._throttle_path("paper", "12345678").read_text(encoding="utf-8"))
    assert abs(time.time() - stamp) < 1.0
    assert slept == []  # 파일이 없었으니 첫 호출은 대기 없음


def test_second_instance_in_the_same_process_reads_the_first_instances_stamp(monkeypatch):
    """인스턴스 단위 상태가 아니라 공유 파일 단위임을 증명한다."""
    slept = _slept(monkeypatch)
    a = _client()
    a._throttle()

    b = _client()
    b._throttle()

    assert len(slept) == 1
    assert 0 < slept[0] <= 1.05


# ============================================================
# 경계
# ============================================================

def test_no_wait_when_the_stamp_is_old(monkeypatch):
    _write_stamp("paper", "12345678", time.time() - 10.0)
    slept = _slept(monkeypatch)
    c = _client()

    c._throttle()

    assert slept == []


def test_missing_file_does_not_wait(monkeypatch):
    slept = _slept(monkeypatch)
    c = _client()

    c._throttle()

    assert slept == []
    assert kc._throttle_path("paper", "12345678").exists()


def test_empty_file_is_treated_as_no_previous_call(monkeypatch):
    kc._throttle_path("paper", "12345678").write_text("", encoding="utf-8")
    slept = _slept(monkeypatch)
    c = _client()

    c._throttle()

    assert slept == []


def test_future_stamp_never_sleeps_longer_than_the_interval(monkeypatch):
    """NTP 역행 / 미래 타임스탬프 — 절대 3600초를 자지 않는다."""
    _write_stamp("paper", "12345678", time.time() + 3600)
    slept = _slept(monkeypatch)
    c = _client()

    c._throttle()

    assert slept == [] or slept[0] <= 1.05


def test_cold_start_last_request_at_is_wall_clock_not_zero():
    c = _client()

    assert c._last_request_at != 0.0
    assert abs(time.time() - c._last_request_at) < 1.0


def test_path_is_scoped_by_mode_and_account():
    """conftest의 autouse 픽스처가 kc._throttle_path 를 tmp 람다로 가리므로,
    여기서는 모듈 import 시점에 붙잡아 둔 실제 함수(_REAL_THROTTLE_PATH)를
    검증한다 — 그래야 mode/cano 를 실제로 무시하는 회귀를 잡는다."""
    p_paper_111 = _REAL_THROTTLE_PATH("paper", "111")
    p_real_111 = _REAL_THROTTLE_PATH("real", "111")
    p_paper_222 = _REAL_THROTTLE_PATH("paper", "222")

    assert p_paper_111 != p_real_111
    assert p_paper_111 != p_paper_222
    assert not str(_REAL_THROTTLE_PATH("paper", "")).endswith("-")


# ============================================================
# 에러
# ============================================================

def test_corrupt_file_is_treated_as_no_previous_call(monkeypatch):
    kc._throttle_path("paper", "12345678").write_text("not-a-float", encoding="utf-8")
    slept = _slept(monkeypatch)
    c = _client()

    c._throttle()

    assert slept == []


def test_unwritable_lock_path_falls_back_without_raising(monkeypatch):
    def raiser(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(kc.os, "open", raiser)
    slept = _slept(monkeypatch)  # 폴백도 interval 만큼 재우므로 실제 대기를 없앤다
    c = _client()
    before = c._last_request_at

    c._throttle()  # 예외를 던지면 안 된다

    assert len(slept) == 1
    assert 0 < slept[0] <= 1.05
    assert c._last_request_at != before


# ============================================================
# 통합 — 서로 다른 프로세스 2개가 계좌 단위 간격을 지킨다 (이슈 완료 기준 1)
# ============================================================

def _run_child(tmp_home: Path) -> tuple[float, float]:
    env = {**os.environ, "HOME": str(tmp_home)}
    p = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=30,
    )
    assert p.returncode == 0, p.stderr
    before, after = p.stdout.strip().split()
    return float(before), float(after)


def test_two_processes_observe_the_account_level_interval(tmp_path):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()

    b_a, a_a = _run_child(tmp_home)   # 첫 프로세스 — 공유 파일 없음, 대기 없음
    assert a_a - b_a < 1.05

    b_b, a_b = _run_child(tmp_home)   # 둘째 프로세스, 즉시 이어서 기동

    # 창 도착 확인 — 자식 기동이 interval 보다 느리면 경합이 없어 테스트가 무의미하다.
    assert b_b < a_a + 1.05, "자식 기동이 interval 보다 느려 경합이 없었다 — 테스트 무의미"
    # 핵심 단언 — 둘째 프로세스는 첫 프로세스의 완료로부터 최소 interval 만큼 기다린다.
    assert a_b >= a_a + 1.05 - 1e-6
