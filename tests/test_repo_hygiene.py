"""저자 머신 커플링 회귀 방지 (태스크 15).

이 테스트는 저장소 파일을 **읽기만** 한다. 키체인 접근, launchctl 실행,
venv 생성, 파이썬/그 외 프로세스 spawn 을 일절 하지 않으므로 어떤 머신에서
돌려도 부작용이 없다.

스캐너 자신이 자기 패턴에 걸리지 않도록 아래 리터럴은 조각으로 조립한다.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 저자 머신 흔적. 이 파일이 자기 스캔에 걸리지 않게 문자열을 쪼개 조립한다.
AUTHOR_HOME = "/Users/" + "choeyeong" + "-gi"
AUTHOR_LABEL = "com." + "choeyeong" + "gi."

# 스캔 제외: VCS 메타데이터, 설치 산출물, 빌드 산출물, 런타임 데이터,
# 그리고 태스크 15 의 Verify 가 명시적으로 제외하는 plans/.
SKIP_DIRS = {
    ".git",
    ".venv",
    ".orchestration",
    # 오케스트레이션이 만드는 태스크별 git 워크트리. gitignore 대상이고 배포
    # 패키지에도 사용자 클론에도 없다. 각 워크트리는 자기 분기 시점의 스냅샷이라
    # 태스크 15 의 삭제 이전 파일을 그대로 들고 있어, 빼지 않으면 이 스캔이
    # 지나간 상태를 현재 위반으로 보고한다.
    ".worktrees",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "dist-test",
    "plans",
    "data",
}

# 텍스트로 열어볼 확장자. 그 외(이미지·sqlite 등)는 스캔하지 않는다.
TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".js",
    ".mjs",
    ".sh",
    ".toml",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".plist",
    ".sql",
    ".txt",
    ".cfg",
    ".ini",
}


def source_files(root: Path = REPO) -> list[Path]:
    """SKIP_DIRS 를 제외한 저장소의 텍스트 소스 파일 전부."""
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            found.append(path)
    return found


def matching_lines(paths: list[Path], pattern: str) -> list[tuple[Path, int, str]]:
    """`pattern` 에 걸리는 (파일, 줄번호, 줄내용) 전부.

    디코딩 불가·읽기 불가 파일은 조용히 건너뛴다 — 스캐너가 바이너리 하나에
    죽어서 검사를 통째로 못 도는 편이 더 위험하다.
    """
    rx = re.compile(pattern)
    hits: list[tuple[Path, int, str]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append((path, lineno, line))
    return hits


def rels(hits: list[tuple[Path, int, str]]) -> set[str]:
    return {str(p.relative_to(REPO)) for p, _, _ in hits}


def report(hits: list[tuple[Path, int, str]]) -> str:
    return "\n".join(f"{p.relative_to(REPO)}:{n}: {line.strip()}" for p, n, line in hits)


# --- 스캐너 자체 검증 (이 아래 저장소 검사가 공허하지 않음을 보장) ----------


def test_scanner_detects_planted_violation(tmp_path: Path) -> None:
    """심어놓은 위반을 실제로 잡아내야 한다 — 스캔이 헛돌면 나머지가 무의미하다."""
    planted = tmp_path / "planted.sh"
    planted.write_text(f"# deploy to {AUTHOR_HOME}/stock-trader\necho ok\n", encoding="utf-8")

    hits = matching_lines([planted], re.escape(AUTHOR_HOME))

    assert len(hits) == 1
    assert hits[0][1] == 1
    assert AUTHOR_HOME in hits[0][2]


def test_scanner_returns_nothing_for_clean_and_empty_inputs(tmp_path: Path) -> None:
    """경계값: 빈 파일 목록, 빈 파일, 매치 없는 파일 모두 0건."""
    empty_file = tmp_path / "empty.py"
    empty_file.write_text("", encoding="utf-8")
    clean_file = tmp_path / "clean.py"
    clean_file.write_text("x = 1\n", encoding="utf-8")

    assert matching_lines([], re.escape(AUTHOR_HOME)) == []
    assert matching_lines([empty_file], re.escape(AUTHOR_HOME)) == []
    assert matching_lines([clean_file], re.escape(AUTHOR_HOME)) == []


def test_scanner_survives_undecodable_file(tmp_path: Path) -> None:
    """에러 경로: UTF-8 로 못 읽는 파일이 섞여도 죽지 않고 나머지를 계속 본다."""
    binary = tmp_path / "blob.txt"
    binary.write_bytes(b"\xff\xfe\x00\x81\x82")
    good = tmp_path / "good.txt"
    good.write_text(f"{AUTHOR_HOME}\n", encoding="utf-8")

    hits = matching_lines([binary, good], re.escape(AUTHOR_HOME))

    assert [p for p, _, _ in hits] == [good]
    assert hits[0][1] == 1


def test_scanner_skips_missing_file(tmp_path: Path) -> None:
    """에러 경로: 존재하지 않는 경로가 들어와도 예외 없이 건너뛴다."""
    assert matching_lines([tmp_path / "does-not-exist.py"], ".") == []


def test_source_files_excludes_skip_dirs() -> None:
    """스캔 대상이 실제로 SKIP_DIRS 를 제외하는지 — 제외가 과하면 검사가 공허해진다."""
    files = source_files()

    assert files, "스캔 대상이 0개면 이하 모든 검사가 공허하다"
    assert not [p for p in files if SKIP_DIRS & set(p.relative_to(REPO).parts)]
    assert (REPO / "pyproject.toml") in files
    assert (REPO / "scripts" / "setup.sh") in files


# --- 저장소 위생 ------------------------------------------------------------


def test_no_author_home_path_in_repo() -> None:
    """저자 홈 절대경로가 저장소 어디에도 없어야 한다."""
    hits = matching_lines(source_files(), re.escape(AUTHOR_HOME))

    assert not hits, f"저자 홈 절대경로 잔존:\n{report(hits)}"


def test_no_author_launchd_labels_outside_readme() -> None:
    """저자 전용 launchd 레이블 제거.

    README.md 는 태스크 16 소유라 여기서 건드리지 않는다. 태스크 16 이
    정리하면 offenders 는 공집합이 되며, 그때도 이 단언은 그대로 통과한다.
    """
    hits = matching_lines(source_files(), re.escape(AUTHOR_LABEL))

    assert rels(hits) <= {"README.md"}, f"저자 launchd 레이블 잔존:\n{report(hits)}"


def test_author_only_artifacts_are_deleted() -> None:
    """커밋됐던 저자 전용 plist 묶음과 SSH 배포 스크립트가 사라져야 한다."""
    assert not (REPO / "plists").exists(), "plists/ 는 cli/launchd.ts 의 renderPlist 로 대체됐다"
    assert not (REPO / "scripts" / "install_macbook_home.sh").exists()


def test_scripts_have_no_author_machine_paths() -> None:
    """스크립트 주석/독스트링의 `~/stock-trader`·`~/Desktop/stock` 경로 가정 제거.

    키체인 계정명 `stock-trader`(태스크 04 상류 계약)는 경로가 아니므로
    패턴이 `~/` 나 `$HOME/` 접두사에만 걸리도록 좁혔다.

    `scripts/migrate_keychain_to_kis.sh` 의 `$HOME/stock-trader/...` 는 주석이
    아니라 실행 로직(로그 경로)이라 태스크 15 범위 밖 — 후속 과제로 남긴다.
    """
    pattern = r"(?:~|\$HOME)/(?:stock-trader|Desktop/stock)"
    hits = matching_lines(source_files(REPO / "scripts"), pattern)

    offenders = {str(p.relative_to(REPO)) for p, _, _ in hits}
    assert offenders <= {"scripts/migrate_keychain_to_kis.sh"}, (
        f"스크립트에 저자 머신 경로 잔존:\n{report(hits)}"
    )


def test_setup_script_points_at_the_cli_next_step() -> None:
    """setup.sh 마지막 안내가 저자 로컬 문서가 아니라 CLI 명령을 가리켜야 한다."""
    text = (REPO / "scripts" / "setup.sh").read_text(encoding="utf-8")

    assert "다음 단계: kis-trader init" in text
    assert "10-tasks.md" not in text


def test_setup_script_logic_unchanged() -> None:
    """setup.sh 의 실행 로직은 손대지 않았다 — 주석/안내 문구만 바뀐다."""
    text = (REPO / "scripts" / "setup.sh").read_text(encoding="utf-8")

    for line in (
        '$PYTHON -m venv .venv',
        '.venv/bin/pip install -e ".[dev]" -q',
        "sqlite3 data/trades.sqlite < data/migrations/0001_init.sql",
        ".venv/bin/pytest tests/",
    ):
        assert line in text, f"실행 로직이 변경됨: {line!r} 사라짐"


def test_healthcheck_uses_the_runtime_label_scheme() -> None:
    """저자 고정 레이블 대신 cli/launchd.ts 의 labelFor() 규칙을 따라야 한다.

    labelFor(job, username) === `com.${username}.kistrader.${job}` (태스크 07).
    사용자명을 런타임에 뽑으므로 어떤 머신에서도 자기 잡을 조회한다.
    """
    text = (REPO / "scripts" / "healthcheck.sh").read_text(encoding="utf-8")

    assert 'p="com.$(id -un).kistrader.$job"' in text
    assert "for job in orchestrator monitor reconciler; do" in text
    # 조회 자체(launchctl list | grep)는 그대로여야 한다 — 레이블만 바뀐다.
    assert 'launchctl list | grep "$p" || echo "$p (미등록)"' in text


def test_healthcheck_job_names_exist_in_launchd_spec() -> None:
    """healthcheck 가 조회하는 job 이름이 cli/launchd.ts 의 JOBS 키와 일치해야 한다.

    오타 난 레이블은 launchctl 에서 조용히 '(미등록)' 으로만 보이므로,
    상류 정의와 대조해 두지 않으면 점검이 늘 통과하는 것처럼 보인다.
    """
    launchd_ts = (REPO / "cli" / "launchd.ts").read_text(encoding="utf-8")

    for job in ("orchestrator", "monitor", "reconciler"):
        assert re.search(rf"^  {job}: \{{$", launchd_ts, re.MULTILINE), (
            f"JOBS 에 {job} 키가 없다 — healthcheck.sh 의 레이블이 상류와 어긋났다"
        )
    assert "return `com.${username}.kistrader.${job}`;" in launchd_ts


# --- pyproject 메타데이터 ---------------------------------------------------


def load_pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_identity_matches_the_shipped_package() -> None:
    project = load_pyproject()["project"]

    assert project["name"] == "kis-trader"
    assert project["description"] == "LLM 기반 한국투자증권(KIS) 자동매매 엔진"
    assert "키움" not in project["description"], "브로커는 KIS 다 — 키움 MCP 아님"


def test_pyproject_has_no_mcp_dependency() -> None:
    """소스 어디에도 mcp 를 import 하지 않으므로 의존성에서 빠져야 한다."""
    deps = load_pyproject()["project"]["dependencies"]

    assert not [d for d in deps if re.match(r"^\s*mcp\b", d)], deps


def test_no_mcp_imports_anywhere() -> None:
    """의존성 제거의 근거 — import 0건."""
    roots = [REPO / "src", REPO / "scripts", REPO / "tests"]
    files = [f for root in roots for f in source_files(root)]

    # `(?:import|from)\s+mcp` 처럼 키워드와 모듈명을 붙여 쓰지 않는다. 통짜 리터럴로 두면 태스크의
    # Verify grep 이 스캐너인 이 파일 자신을 위반으로 잡아버린다.
    hits = matching_lines(files, r"^\s*(?:import|from)\s+mcp\b")

    assert not hits, f"mcp import 잔존 — 의존성을 지우면 깨진다:\n{report(hits)}"


def test_pyproject_leaves_everything_else_untouched() -> None:
    """경계: 지우라고 한 것만 지웠는지. version/requires-python/나머지 의존성/[tool.*] 보존."""
    data = load_pyproject()
    project = data["project"]

    assert project["version"] == "0.0.1"
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == [
        "pydantic>=2.5",
        "sqlalchemy>=2.0",
        "alembic>=1.13",
        "pyyaml>=6.0",
        "requests>=2.32",
        "python-dotenv>=1.0",
        "jsonschema>=4.21",
        "holidays>=0.50",
    ]
    assert data["project"]["optional-dependencies"]["dev"] == [
        "pytest>=8.0",
        "pytest-asyncio>=0.23",
        "ruff>=0.4",
    ]
    assert data["build-system"]["requires"] == ["setuptools>=68"]
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["src*"]
    assert data["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert data["tool"]["ruff"]["line-length"] == 100
    assert data["tool"]["ruff"]["target-version"] == "py311"
