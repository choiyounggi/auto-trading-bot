"""Kill Switch — 전역 잠금. 파일 기반으로 cross-process 동기."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_FILE = Path("data") / "KILL_SWITCH"


def is_active(file_path: Path | str = DEFAULT_FILE) -> bool:
    return Path(file_path).exists()


def activate(reason: str, file_path: Path | str = DEFAULT_FILE) -> None:
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"activated_at={datetime.now().isoformat()}\nreason={reason}\n",
        encoding="utf-8",
    )
    log.critical("KILL SWITCH 활성: %s", reason)


def deactivate(user_confirmed: bool, file_path: Path | str = DEFAULT_FILE) -> None:
    if not user_confirmed:
        raise RuntimeError("Kill Switch 해제는 사용자 명시 확인 필요")
    p = Path(file_path)
    p.unlink(missing_ok=True)
    log.warning("KILL SWITCH 해제 (사용자 명시 확인)")


def get_status(file_path: Path | str = DEFAULT_FILE) -> dict | None:
    p = Path(file_path)
    if not p.exists():
        return None
    out: dict = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out
