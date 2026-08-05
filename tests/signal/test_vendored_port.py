"""신호 봇 이식(태스크 01) 회귀 방지 — `src/signal/` 트리 자체를 검사한다.

이 테스트는 저장소 파일을 읽고 이식된 모듈을 import 할 뿐이다. 네트워크
(KRX·Telegram·Brave), 키체인, launchctl 을 일절 건드리지 않는다.

`import` 부작용 주의: `universe.py` 와 `data/{pykrx,naver}_source.py` 는
모듈 최상단에서 `CACHE_DIR.mkdir(parents=True, exist_ok=True)` 를 실행한다.
그래서 `parents[N]` 을 하나라도 덜 올리면 캐시 디렉터리가 `src/` 안쪽에
조용히 생긴다 — grep 한 번으로는 못 잡고, 나중 태스크(04·05·11)가 이 트리를
고칠 때 다시 깨질 수 있다. 그래서 shell grep 이 아니라 테스트로 고정한다.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIGNAL_SRC = REPO / "src" / "signal"
SIGNAL_TESTS = REPO / "tests" / "signal"

# 태스크 01 이 이식하기로 한 23 개 모듈. 원본은 `~/stock-signal-bot/src`.
PORTED_MODULES = (
    "__init__.py",
    "main.py",
    "universe.py",
    "analysis/__init__.py",
    "analysis/flow_analyzer.py",
    "analysis/llm_analyzer.py",
    "analysis/overseas_strategy_builder.py",
    "analysis/price_analyzer.py",
    "analysis/signal_engine.py",
    "analysis/strategy_builder.py",
    "analysis/ticker_context.py",
    "data/__init__.py",
    "data/dump_signals.py",
    "data/macro_context.py",
    "data/naver_finance.py",
    "data/naver_source.py",
    "data/news_brave.py",
    "data/overseas_yfinance_source.py",
    "data/pykrx_source.py",
    "data/short_balance.py",
    "data/sources.py",
    "notify/__init__.py",
    "notify/telegram_bot.py",
)

# 재작성돼야 하는 이식 전 import. 원본은 `src.analysis` / `src.data` /
# `src.notify` / `src.universe` 를 가리키는데, 이 저장소에는 트레이더의
# `src/notify/` 와 `src/universe/` 가 이미 있어서 그대로 두면 엉뚱한
# 모듈을 import 한다 (조용히 잘못 동작한다 — ImportError 조차 안 난다).
STALE_IMPORT_RX = r"^\s*(?:from|import)\s+src\.(?:analysis|data|notify|universe)\b"

# `Path(__file__).resolve().parents[N]` 의 N 을 뽑는다.
PARENTS_RX = re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]")


def ported_py_files() -> list[Path]:
    """이식된 트리의 `.py` 파일 전부 (`__pycache__` 제외)."""
    return sorted(
        p
        for p in SIGNAL_SRC.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def scan(paths: list[Path], pattern: str) -> list[tuple[Path, int, str]]:
    """`pattern` 에 걸리는 (파일, 줄번호, 줄내용) 전부."""
    rx = re.compile(pattern)
    hits: list[tuple[Path, int, str]] = []
    for path in paths:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if rx.search(line):
                hits.append((path, lineno, line))
    return hits


def report(hits: list[tuple[Path, int, str]]) -> str:
    return "\n".join(
        f"{p.relative_to(REPO)}:{n}: {line.strip()}" for p, n, line in hits
    )


# --- 스캐너 자체 검증 (아래 "0 건" 단언이 공허하지 않음을 보장) --------------


def test_scanner_catches_a_planted_stale_import(tmp_path: Path) -> None:
    """심어놓은 이식 전 import 를 실제로 잡아야 한다.

    이게 없으면 `STALE_IMPORT_RX` 오타 하나로 아래 위생 검사가 영원히
    "0 건 통과" 를 반환한다.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import os\n"
        "from src.analysis.signal_engine import combine\n"
        "from src.signal.data.sources import fetch_ticker_panel\n",
        encoding="utf-8",
    )

    hits = scan([planted], STALE_IMPORT_RX)

    assert len(hits) == 1, f"정확히 1건이어야 한다:\n{hits}"
    assert hits[0][1] == 2
    # 재작성된 `src.signal.*` 는 걸리면 안 된다 (오탐 방지).
    assert "src.signal" not in hits[0][2]


def test_scanner_returns_nothing_for_clean_and_empty_inputs(tmp_path: Path) -> None:
    """경계값: 빈 목록·빈 파일·매치 없는 파일 모두 0 건."""
    empty = tmp_path / "empty.py"
    empty.write_text("", encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text("from src.signal.universe import load_universe\n", encoding="utf-8")

    assert scan([], STALE_IMPORT_RX) == []
    assert scan([empty], STALE_IMPORT_RX) == []
    assert scan([clean], STALE_IMPORT_RX) == []


# --- 트리 구조 --------------------------------------------------------------


def test_all_23_modules_were_ported() -> None:
    """23 개 모듈이 서브디렉터리 구조를 유지한 채 존재해야 한다."""
    missing = [rel for rel in PORTED_MODULES if not (SIGNAL_SRC / rel).is_file()]

    assert not missing, f"이식 누락:\n" + "\n".join(missing)
    assert len(PORTED_MODULES) == 23


def test_no_extra_python_modules_were_copied() -> None:
    """경계: 23 개 **만** 들어와야 한다. 백업본·잔재가 딸려오면 실패."""
    actual = {str(p.relative_to(SIGNAL_SRC)) for p in ported_py_files()}

    assert actual == set(PORTED_MODULES), (
        f"예상 밖 파일: {sorted(actual - set(PORTED_MODULES))}\n"
        f"누락: {sorted(set(PORTED_MODULES) - actual)}"
    )


def test_no_backup_or_secret_artifacts_were_copied() -> None:
    """`.bak*` 백업본과 `.env` 는 하나도 넘어오면 안 된다.

    원본에는 `main.py.bak-*` 6 개를 비롯한 백업본이 널려 있고, `.env` 에는
    실제 KRX 로그인과 Brave API 키가 들어있다. 둘 다 무엇도 만들어내지 않는
    파일이라 파일시스템에 존재하면 곧 "복사됐다" 는 뜻이다.
    """
    everything = [p for p in SIGNAL_SRC.rglob("*") if p.is_file()]

    assert everything, "이식 트리가 비어 있다 — 이하 검사가 공허하다"

    baks = [p for p in everything if ".bak" in p.name]
    envs = [p for p in everything if p.name == ".env" or p.name.startswith(".env.")]

    assert not baks, f"백업 파일 잔존: {[str(p.relative_to(REPO)) for p in baks]}"
    assert not envs, f".env 가 복사됐다 — 실 자격증명 유출: {[str(p) for p in envs]}"


def test_no_build_artifacts_are_committed() -> None:
    """`__pycache__`/`.pyc` 가 저장소에 커밋되면 안 된다.

    파일시스템 존재 여부로 검사하면 안 된다 — 이 테스트가 이식 모듈을
    import 하는 순간 파이썬이 직접 `__pycache__` 를 만들기 때문에 늘 실패한다.
    실제 불변식은 "추적되지 않는다" 이고, `.gitignore` 가 이를 보장한다.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "src/signal", "tests/signal"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    junk = [f for f in tracked if "__pycache__" in f or f.endswith(".pyc")]

    assert not junk, f"빌드 산출물이 커밋됐다: {junk}"

    # 위 단언은 아직 커밋 전이라 공허할 수 있다. `.gitignore` 가 실제로
    # 이 경로들을 막는지 직접 확인해 커밋 시점의 보장을 지금 검증한다.
    for candidate in (
        "src/signal/__pycache__/main.cpython-311.pyc",
        "src/signal/analysis/__pycache__/signal_engine.cpython-311.pyc",
        "tests/signal/__pycache__/test_signal_engine.cpython-311.pyc",
    ):
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", candidate], cwd=REPO
        ).returncode

        assert ignored == 0, f".gitignore 가 {candidate} 를 막지 않는다"


def test_ported_tests_live_in_an_importable_package() -> None:
    """3 개 테스트 + `__init__.py` 가 `tests/signal/` 에 있어야 한다."""
    assert (SIGNAL_TESTS / "__init__.py").is_file()

    for name in (
        "test_signal_engine.py",
        "test_strategy_builder.py",
        "test_overseas_strategy_builder.py",
    ):
        assert (SIGNAL_TESTS / name).is_file(), f"이식 누락: tests/signal/{name}"


# --- import 재작성 ----------------------------------------------------------


def test_no_stale_imports_survive_in_ported_source() -> None:
    """이식 전 `src.{analysis,data,notify,universe}` import 0 건."""
    files = ported_py_files()

    assert len(files) >= 23, f"스캔 대상이 {len(files)}개 — 검사가 공허하다"

    hits = scan(files, STALE_IMPORT_RX)

    assert not hits, f"재작성 안 된 import:\n{report(hits)}"


def test_no_stale_imports_survive_in_ported_tests() -> None:
    """이식된 테스트 3 개도 같은 재작성을 받아야 한다."""
    files = sorted(
        p for p in SIGNAL_TESTS.rglob("*.py") if "__pycache__" not in p.parts
    )

    assert len(files) >= 4, f"스캔 대상이 {len(files)}개 — 검사가 공허하다"

    hits = scan(files, STALE_IMPORT_RX)

    assert not hits, f"재작성 안 된 import:\n{report(hits)}"


def test_documented_python_m_invocations_name_a_real_module() -> None:
    """독스트링의 `python -m X` 가 실제 존재하는 모듈이어야 한다.

    import 문만 재작성하면 `main.py` 의 Usage 블록이 이식 전 `src.main` 을
    가리킨 채 남는다. import 는 전부 통과하는데 문서가 시키는 명령은
    `No module named src.main` 으로 죽는다 — grep 도 pytest 도 못 잡는다.
    """
    invocations: list[tuple[Path, str]] = []
    for path in ported_py_files():
        for m in re.finditer(
            r"python -m ([A-Za-z_][\w.]*)", path.read_text(encoding="utf-8")
        ):
            invocations.append((path, m.group(1)))

    assert invocations, "`python -m` 사용례를 못 찾았다 — 검사가 공허하다"

    def resolves(name: str) -> bool:
        # 상위 패키지가 없으면 find_spec 은 None 이 아니라 예외를 던진다.
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    broken = [(p, mod) for p, mod in invocations if not resolves(mod)]

    assert not broken, "존재하지 않는 모듈을 안내한다:\n" + "\n".join(
        f"{p.relative_to(REPO)}: python -m {mod}" for p, mod in broken
    )


def test_every_ported_module_actually_imports() -> None:
    """23 개 모듈 전부가 실제로 import 돼야 한다.

    grep 은 문자열만 본다. 재작성이 문법적으로만 맞고 대상 모듈이 없으면
    (오타난 경로 등) grep 은 통과하고 런타임에 죽는다.
    """
    for rel in PORTED_MODULES:
        dotted = "src.signal." + rel.removesuffix(".py").replace("/", ".")
        dotted = dotted.removesuffix(".__init__")

        importlib.import_module(dotted)


# --- ROOT 경로 (`parents[N]` +1) --------------------------------------------


def test_main_root_points_at_the_repo_root() -> None:
    """`src/signal/main.py` 가 한 단계 깊어졌으므로 `ROOT` 가 저장소 루트여야 한다."""
    main = importlib.import_module("src.signal.main")

    assert main.ROOT == REPO
    assert (main.ROOT / "pyproject.toml").is_file()


def test_every_parents_index_resolves_to_the_repo_root() -> None:
    """이식 트리의 모든 `parents[N]` 이 저장소 루트를 가리켜야 한다.

    4 곳 전부 루트 기준 경로(.env·config·data/cache)를 만든다.
    한 곳이라도 +1 을 빠뜨리면 `src/` 안쪽을 가리키는데, 그중 셋은 import
    시점에 `mkdir` 까지 해버려서 조용히 잘못된 디렉터리를 만든다.

    `data/signals` 는 더 이상 여기에 없다 — `dump_signals.py` 가
    `resolve_signal_dir()`(KIS_TRADER_SIGNAL_DIR)로 해석한다.
    """
    sites: list[tuple[Path, int, int]] = []
    for path in ported_py_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            for m in PARENTS_RX.finditer(line):
                sites.append((path, lineno, int(m.group(1))))

    assert len(sites) >= 4, f"`parents[N]` 자리를 {len(sites)}개만 찾았다 — 검사가 공허하다"

    wrong = [
        (p, n, idx)
        for p, n, idx in sites
        if p.resolve().parents[idx] != REPO
    ]

    assert not wrong, "저장소 루트를 가리키지 않는 parents[N]:\n" + "\n".join(
        f"{p.relative_to(REPO)}:{n}: parents[{idx}] -> {p.resolve().parents[idx]}"
        for p, n, idx in wrong
    )


def test_cache_dirs_resolve_under_the_repo_data_dir() -> None:
    """import 시점에 `mkdir` 하는 세 모듈의 캐시 경로가 저장소 `data/` 아래여야 한다."""
    expected = REPO / "data" / "cache"

    for dotted in (
        "src.signal.universe",
        "src.signal.data.pykrx_source",
        "src.signal.data.naver_source",
    ):
        module = importlib.import_module(dotted)

        assert module.CACHE_DIR == expected, f"{dotted}.CACHE_DIR={module.CACHE_DIR}"


def test_no_cache_dir_leaked_into_the_source_tree() -> None:
    """경계: import 부작용이 `src/` 안에 `data/` 를 만들지 않았어야 한다.

    `parents[N]` 을 덜 올렸을 때 실제로 나타나는 증상을 직접 잡는다.
    """
    leaked = [p for p in (REPO / "src").rglob("data/cache") if p.is_dir()]
    leaked += [p for p in (REPO / "src").rglob("data/signals") if p.is_dir()]

    assert not leaked, f"잘못된 위치에 캐시 생성: {[str(p.relative_to(REPO)) for p in leaked]}"


# --- 저장소 위생 ------------------------------------------------------------


def test_ported_files_carry_no_author_machine_path() -> None:
    """저자 홈 절대경로가 이식본에 남으면 `test_repo_hygiene` 이 깨진다.

    원본 `dump_signals.py`·`naver_finance.py` 헤더에 "설치 위치 (자택 맥북)"
    주석으로 들어있다. 이식되면 그 경로는 사실도 아니다.
    """
    author_home = "/Users/" + "choeyeong" + "-gi"
    files = ported_py_files()

    assert len(files) >= 23, f"스캔 대상이 {len(files)}개 — 검사가 공허하다"

    hits = scan(files, re.escape(author_home))

    assert not hits, f"저자 홈 절대경로 잔존:\n{report(hits)}"
