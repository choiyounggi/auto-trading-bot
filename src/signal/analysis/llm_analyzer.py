"""LLM CLI subprocess 호출 — claude code 우선, 실패 시 pi fallback."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# 자택 맥북 기준 절대경로 (launchd 비대화형 셸에서도 동작)
CLAUDE_BIN = "/opt/homebrew/bin/claude"
PI_BIN = "/opt/homebrew/bin/pi"


def _version_tuple(name: str) -> tuple[int, ...]:
    """v22.13.1 → (22,13,1). 비교 가능한 튜플."""
    parts = name.lstrip("v").split(".")
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def _newest_nvm_node_bin() -> str | None:
    """가장 최신 nvm-managed node bin 디렉토리. 시맨틱 버전 정렬 (v22.13.1 > v22.9.0)."""
    base = Path.home() / ".nvm" / "versions" / "node"
    if not base.exists():
        return None
    candidates = sorted(base.glob("v*"), key=lambda p: _version_tuple(p.name))
    if not candidates:
        return None
    bin_dir = candidates[-1] / "bin"
    return str(bin_dir) if bin_dir.exists() else None


def _augmented_env() -> dict:
    """node + brew bin 추가 PATH 환경."""
    env = dict(os.environ)
    parts = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    node_bin = _newest_nvm_node_bin()
    if node_bin:
        parts.insert(0, node_bin)
    env["PATH"] = ":".join(parts) + ":" + env.get("PATH", "")
    return env


def _run(cmd: list[str], stdin_text: str, timeout: int) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_augmented_env(),
        )
        if r.returncode != 0:
            log.warning("LLM CLI exit=%d stderr=%s", r.returncode, r.stderr[:300])
            return False, r.stderr.strip() or r.stdout.strip()
        out = r.stdout.strip()
        if not out:
            return False, "empty stdout"
        return True, out
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError as e:
        return False, f"not found: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _try_claude(prompt: str, timeout: int) -> tuple[bool, str]:
    if not Path(CLAUDE_BIN).exists():
        return False, "claude not installed"
    # claude -p (--print) headless mode. 응답 1회 출력 후 종료.
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "text"]
    return _run(cmd, stdin_text="", timeout=timeout)


def _try_pi(prompt: str, timeout: int) -> tuple[bool, str]:
    if not Path(PI_BIN).exists():
        return False, "pi not installed"
    # pi 0.74+ : -p (--print) 단발 모드. --no-extensions 로 sqlite 의존 회피, --no-tools 로 read/bash/edit 비활성.
    cmd = [
        PI_BIN, "-p", prompt,
        "--mode", "text",
        "--no-extensions",
        "--no-tools",
        "--no-skills",
        "--no-session",
    ]
    return _run(cmd, stdin_text="", timeout=timeout)


def analyze(prompt: str, timeout: int = 90) -> tuple[str, str]:
    """
    Returns (analysis_text, source). source ∈ {"claude","pi","unavailable"}
    실패 시 (에러요약, "unavailable").
    """
    ok, out = _try_claude(prompt, timeout)
    if ok:
        return out, "claude"
    log.warning("claude 실패 → pi fallback (reason: %s)", out[:200])

    ok, out = _try_pi(prompt, timeout)
    if ok:
        return out, "pi"
    log.warning("pi 실패 (reason: %s)", out[:200])

    return f"LLM 분석 실패: {out[:200]}", "unavailable"
