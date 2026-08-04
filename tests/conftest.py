"""pytest 공통 fixture."""
from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (src를 패키지로 import 가능)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
