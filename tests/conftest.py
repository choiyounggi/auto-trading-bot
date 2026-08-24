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
