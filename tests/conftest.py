"""pytest 공통 fixture."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 프로젝트 루트를 sys.path에 추가 (src를 패키지로 import 가능)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate_kis_throttle_file(monkeypatch, tmp_path_factory):
    """throttle 공유 파일을 tmp 로 격리 — 실행 중인 실운영 봇의 호출 시각이
    테스트를 실제로 재우거나, 개발자 홈에 파일을 남기는 것을 막는다.

    테스트 자신의 tmp_path 를 재사용하면 tmp_path 의 내용물을 단언하는
    무관한 테스트(예: test_dump_signals_atomic.py)를 오염시키므로,
    독립된 tmp_path_factory 디렉터리를 쓴다."""
    from src.broker import kis_client as kc
    d = tmp_path_factory.mktemp("kis-throttle")
    monkeypatch.setattr(kc, "_throttle_path", lambda mode, cano: d / f"{mode}-{cano or 'nocano'}")


@pytest.fixture(autouse=True)
def _isolate_order_reject_state(monkeypatch, tmp_path_factory):
    """주문 거부 억제 상태 파일을 tmp 로 격리 — 테스트가 레포의 data/logs 에
    파일을 남기거나(test_repo_hygiene), 앞선 테스트가 남긴 카운트 때문에
    다른 테스트의 첫 경고가 억제되는 것을 막는다.

    _isolate_kis_throttle_file 과 같은 이유로 tmp_path 가 아니라 독립된
    tmp_path_factory 디렉터리를 쓴다."""
    from src.notify import order_reject as orj
    d = tmp_path_factory.mktemp("order-reject")
    monkeypatch.setattr(orj, "STATE_PATH", d / "state.json")
