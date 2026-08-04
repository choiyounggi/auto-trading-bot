"""Claude CLI + pi CLI fallback. stock-signal-bot llm_analyzer 패턴 재활용."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

CLAUDE_BIN = "/opt/homebrew/bin/claude"
PI_BIN = "/opt/homebrew/bin/pi"

# 진입 timeout 180s (stock-signal-bot 실측 1m30s~2m04s 흡수)
# 모니터 timeout 90s (짧은 prompt)
DEFAULT_ENTRY_TIMEOUT = 180
DEFAULT_MONITOR_TIMEOUT = 90


def _version_tuple(name: str) -> tuple[int, ...]:
    parts = name.lstrip("v").split(".")
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def _newest_nvm_node_bin() -> str | None:
    base = Path.home() / ".nvm" / "versions" / "node"
    if not base.exists():
        return None
    candidates = sorted(base.glob("v*"), key=lambda p: _version_tuple(p.name))
    if not candidates:
        return None
    bin_dir = candidates[-1] / "bin"
    return str(bin_dir) if bin_dir.exists() else None


def _augmented_env() -> dict:
    env = dict(os.environ)
    parts = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    node_bin = _newest_nvm_node_bin()
    if node_bin:
        parts.insert(0, node_bin)
    env["PATH"] = ":".join(parts) + ":" + env.get("PATH", "")
    return env


def _run(cmd: list[str], timeout: int) -> tuple[bool, str, int]:
    """Returns (ok, output_or_err, elapsed_ms)."""
    start = time.perf_counter()
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=_augmented_env()
        )
        elapsed = int((time.perf_counter() - start) * 1000)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "non-zero exit").strip(), elapsed
        out = r.stdout.strip()
        if not out:
            return False, "empty stdout", elapsed
        return True, out, elapsed
    except subprocess.TimeoutExpired:
        return False, "timeout", int((time.perf_counter() - start) * 1000)
    except FileNotFoundError as e:
        return False, f"not found: {e}", 0
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", 0


def _try_claude(prompt: str, timeout: int) -> tuple[bool, str, int]:
    if not Path(CLAUDE_BIN).exists():
        return False, "claude not installed", 0
    return _run([CLAUDE_BIN, "-p", prompt, "--output-format", "text"], timeout)


def _try_pi(prompt: str, timeout: int) -> tuple[bool, str, int]:
    if not Path(PI_BIN).exists():
        return False, "pi not installed", 0
    cmd = [
        PI_BIN, "-p", prompt,
        "--mode", "text",
        "--no-extensions", "--no-tools", "--no-skills", "--no-session",
    ]
    return _run(cmd, timeout)


def call_llm(prompt: str, timeout: int = DEFAULT_ENTRY_TIMEOUT) -> tuple[str, str, int]:
    """
    Returns (output, source, elapsed_ms).
    source ∈ {claude, pi, unavailable}.
    실패 시 output=에러요약, source=unavailable.
    """
    ok, out, ms = _try_claude(prompt, timeout)
    if ok:
        return out, "claude", ms
    log.warning("claude 실패 (%dms): %s → pi fallback", ms, out[:200])

    ok, out2, ms2 = _try_pi(prompt, timeout)
    if ok:
        return out2, "pi", ms2
    log.warning("pi 실패 (%dms): %s", ms2, out2[:200])

    return f"LLM 분석 실패: claude={out[:100]} / pi={out2[:100]}", "unavailable", ms + ms2
