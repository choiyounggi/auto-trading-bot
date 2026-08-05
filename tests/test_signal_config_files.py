"""신호 엔진 튜닝 파일 3종의 이식 계약 (태스크 10).

`config/` 는 12factor 의 "하나의 아티팩트, 환경별로 설정만 다르다" 규칙에 따라
패키지 안에 실린다. 이 테스트는 세 파일이 **원본 그대로** 그 자리에 있고
tarball 에 실린다는 것만 검증한다 — 임계값이 무엇을 뜻하는지(매매 전략)는
검증 대상이 아니다.

이 파일은 저장소 파일을 **읽기만** 한다. 네트워크(KRX/Telegram), 키체인,
launchctl, 프로세스 spawn 을 일절 하지 않는다.

원본 저장소(`~/stock-signal-bot`)를 참조하지 않는다 — 저자 머신에만 있는
경로라 다른 머신에서는 검증이 조용히 무의미해지기 때문이다. 대신 원본의
지문(줄 수·구조·개수)을 여기 박아 두어 어느 머신에서도 같은 것을 본다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "config"

THRESHOLDS = CONFIG / "thresholds.yaml"
WATCHLIST = CONFIG / "watchlist.txt"
OVERSEAS = CONFIG / "overseas_watchlist.yaml"

# 원본(`~/stock-signal-bot/config/`)의 줄 수. 바이트 동일 복사의 지문 —
# 잘림·부분 복사·줄바꿈 변환은 전부 여기서 걸린다.
EXPECTED_LINES = {
    THRESHOLDS: 44,
    WATCHLIST: 38,
    OVERSEAS: 277,
}

KRX_CODE = re.compile(r"^\d{6}$")


def parse_watchlist(text: str) -> list[str]:
    """`watchlist.txt` 본문 → 종목코드 리스트.

    파일 포맷은 원본 주석이 규정한다: 한 줄에 코드 1개, `#` 로 시작하는 줄은
    주석, 코드 뒤 인라인 `#` 주석 허용, 빈 줄 무시.

    Raises:
        ValueError: 주석·공백을 걷어낸 뒤 6자리 숫자가 아닌 토큰이 남은 경우.
    """
    codes: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not KRX_CODE.match(line):
            raise ValueError(f"watchlist line {lineno}: not a 6-digit KRX code: {line!r}")
        codes.append(line)
    return codes


# --- 파서 자체 검증 (아래 실제 파일 검사가 공허하지 않음을 보장) ------------


def test_parser_reads_code_with_inline_comment() -> None:
    """정상: 코드 + 인라인 주석이 원본 포맷이다."""
    assert parse_watchlist("005930  # 삼성전자\n000660  # SK하이닉스\n") == ["005930", "000660"]


def test_parser_rejects_non_six_digit_token() -> None:
    """에러: 5자리·티커 문자열 등은 조용히 통과시키지 않는다."""
    with pytest.raises(ValueError) as exc:
        parse_watchlist("005930\nAAPL\n")

    assert "not a 6-digit KRX code" in str(exc.value)
    assert "line 2" in str(exc.value)
    assert "AAPL" in str(exc.value)


def test_parser_rejects_short_numeric_code() -> None:
    """에러: 자릿수가 모자란 숫자도 거부하고 줄 번호를 알린다."""
    with pytest.raises(ValueError) as exc:
        parse_watchlist("# 주석\n5930\n")

    assert "line 2" in str(exc.value)
    assert "5930" in str(exc.value)


@pytest.mark.parametrize("text", ["", "\n", "   \n\t\n", "# 주석만\n# 또 주석\n"])
def test_parser_returns_empty_for_blank_and_comment_only_input(text: str) -> None:
    """경계값: 빈 입력·공백만·주석만 → 빈 리스트. 원본 주석이 규정한 '빈 파일' 경로."""
    assert parse_watchlist(text) == []


# --- 파일 존재와 지문 -------------------------------------------------------


@pytest.mark.parametrize("path", list(EXPECTED_LINES), ids=lambda p: p.name)
def test_config_file_exists(path: Path) -> None:
    """태스크 01 의 코드가 `<repo>/config/` 에서 이 이름 그대로 찾는다."""
    assert path.is_file(), f"{path.relative_to(REPO)} 이 없다"


@pytest.mark.parametrize(
    ("path", "expected"), list(EXPECTED_LINES.items()), ids=lambda v: getattr(v, "name", v)
)
def test_config_file_line_count_matches_source(path: Path, expected: int) -> None:
    """줄 수가 원본과 같아야 한다 — 충실한 복사의 지문."""
    actual = len(path.read_text(encoding="utf-8").splitlines())

    assert actual == expected, f"{path.name}: {expected}줄이어야 하는데 {actual}줄"


@pytest.mark.parametrize("path", list(EXPECTED_LINES), ids=lambda p: p.name)
def test_config_file_ends_with_newline(path: Path) -> None:
    """경계값: 마지막 줄이 잘리지 않았는지 — 복사 중 절단의 대표 증상."""
    assert path.read_text(encoding="utf-8").endswith("\n")


# --- thresholds.yaml --------------------------------------------------------


def load_thresholds() -> dict:
    return yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))


def test_thresholds_top_level_keys() -> None:
    """최상위 키 집합이 원본과 정확히 일치 — 누락도 추가도 없어야 한다."""
    assert set(load_thresholds()) == {
        "signal",
        "buy_weights",
        "caution_weights",
        "params",
        "markets",
        "indices",
        "overseas_market_risk",
    }


def test_thresholds_signal_section() -> None:
    """BUY/CAUTION 임계는 숫자이고 부호가 갈린다 — 값 자체는 저자 튜닝 영역."""
    signal = load_thresholds()["signal"]

    assert set(signal) == {"buy_threshold", "caution_threshold"}
    assert isinstance(signal["buy_threshold"], int)
    assert isinstance(signal["caution_threshold"], int)
    assert signal["caution_threshold"] < 0 < signal["buy_threshold"]


def test_thresholds_weight_keys() -> None:
    """가중치 키 이름은 신호 코드가 참조하는 seam 이다."""
    data = load_thresholds()

    assert set(data["buy_weights"]) == {
        "A1_inst_foreign_both",
        "A2_consecutive_buying",
        "A3_decoupling",
        "A4_volume_spike",
    }
    assert set(data["caution_weights"]) == {
        "B1_one_day_only",
        "B2_already_pumped",
        "B3_only_inst_buying",
        "B4_only_금융투자",
    }


def test_thresholds_weight_signs() -> None:
    """BUY 가중치는 양수, CAUTION 가중치는 음수 — 부호가 뒤집히면 신호가 반전된다."""
    data = load_thresholds()

    assert all(v > 0 for v in data["buy_weights"].values()), data["buy_weights"]
    assert all(v < 0 for v in data["caution_weights"].values()), data["caution_weights"]


def test_thresholds_params_keys() -> None:
    assert set(load_thresholds()["params"]) == {
        "consecutive_days",
        "accumulate_window",
        "volume_spike_ratio",
        "pumped_return_pct",
        "individual_strong_sell_ratio",
        "finance_only_ratio",
        "decoupling_pct_rank",
    }


def test_thresholds_markets_and_indices() -> None:
    """지수 코드는 **문자열**이어야 한다 — 따옴표가 빠지면 "1028" 이 1028 이 되어 조회가 깨진다."""
    data = load_thresholds()

    assert data["markets"] == ["KOSPI", "KOSDAQ"]
    assert set(data["indices"]) == {"KOSPI200", "KOSDAQ150"}
    assert all(isinstance(v, str) for v in data["indices"].values()), data["indices"]


def test_thresholds_overseas_market_risk_gate() -> None:
    """risk-off 게이트 3종. 상한(vix)은 양수, 수익률 하한 2종은 음수다."""
    gate = load_thresholds()["overseas_market_risk"]

    assert set(gate) == {"vix_max", "spy_ret5_min", "qqq_ret5_min"}
    assert gate["vix_max"] > 0
    assert gate["spy_ret5_min"] < 0
    assert gate["qqq_ret5_min"] < 0


# --- watchlist.txt ----------------------------------------------------------


def test_watchlist_parses_to_krx_codes() -> None:
    """원본 그대로면 30종목(KOSPI 20 + KOSDAQ 10)이 나온다."""
    codes = parse_watchlist(WATCHLIST.read_text(encoding="utf-8"))

    assert len(codes) == 30
    assert all(KRX_CODE.match(c) for c in codes)
    assert "005930" in codes  # 삼성전자 — 원본 첫 종목


def test_watchlist_has_no_duplicate_codes() -> None:
    """경계값: 중복 종목은 신호를 두 번 세게 만든다."""
    codes = parse_watchlist(WATCHLIST.read_text(encoding="utf-8"))

    assert len(set(codes)) == len(codes), f"중복: {[c for c in codes if codes.count(c) > 1]}"


def test_watchlist_keeps_its_format_comments() -> None:
    """포맷을 규정하는 헤더 주석이 남아 있어야 한다 — 이 파일의 유일한 스펙이다."""
    text = WATCHLIST.read_text(encoding="utf-8")

    assert "한 줄에 종목코드 1개" in text
    assert text.startswith("#")


# --- overseas_watchlist.yaml ------------------------------------------------

TICKER_KEYS = {
    "symbol",
    "name",
    "exchange",
    "quote_exchange",
    "currency",
    "yf_symbol",
    "benchmark_symbol",
    "benchmark_exchange",
    "benchmark_quote_exchange",
    "sector_etf",
}

ORDER_EXCHANGES = {"NASD", "NYSE", "AMEX"}
QUOTE_EXCHANGES = {"NAS", "NYS", "AMS"}


def load_overseas() -> dict:
    return yaml.safe_load(OVERSEAS.read_text(encoding="utf-8"))


def test_overseas_top_level_shape() -> None:
    data = load_overseas()

    assert set(data) == {"enabled", "tickers"}
    assert data["enabled"] is True
    assert len(data["tickers"]) == 27


def test_overseas_every_ticker_has_the_full_key_set() -> None:
    """키가 하나라도 빠지면 주문 거래소나 벤치마크 조회가 런타임에 터진다."""
    for ticker in load_overseas()["tickers"]:
        assert set(ticker) == TICKER_KEYS, f"{ticker.get('symbol')}: {set(ticker) ^ TICKER_KEYS}"


def test_overseas_exchange_codes_are_valid_kis_codes() -> None:
    """주문 코드와 시세 코드는 서로 다른 KIS 코드 체계다 — 뒤섞이면 주문이 거절된다."""
    for ticker in load_overseas()["tickers"]:
        assert ticker["exchange"] in ORDER_EXCHANGES, ticker
        assert ticker["quote_exchange"] in QUOTE_EXCHANGES, ticker
        assert ticker["benchmark_exchange"] in ORDER_EXCHANGES, ticker
        assert ticker["benchmark_quote_exchange"] in QUOTE_EXCHANGES, ticker


def test_overseas_symbols_are_unique_and_usd() -> None:
    symbols = [t["symbol"] for t in load_overseas()["tickers"]]

    assert len(set(symbols)) == len(symbols), "심볼 중복"
    assert all(t["currency"] == "USD" for t in load_overseas()["tickers"])


def test_overseas_yf_symbol_matches_symbol() -> None:
    """yfinance 심볼이 주문 심볼과 어긋나면 다른 종목의 시세로 신호를 낸다."""
    for ticker in load_overseas()["tickers"]:
        assert ticker["yf_symbol"] == ticker["symbol"], ticker


# --- 위생: 백업·비밀·이웃 파일 ----------------------------------------------


def test_no_backup_files_in_config() -> None:
    """`.bak*` 는 가져오지 않는다 (결정 D13) — 낡은 임계값이 tarball 에 섞이면 안 된다."""
    leftovers = [p.name for p in CONFIG.iterdir() if ".bak" in p.name]

    assert leftovers == [], f"백업 파일 잔존: {leftovers}"


def test_no_credentials_copied_into_config() -> None:
    """원본 저장소의 `.env`(실제 KRX 로그인·Brave API 키)가 따라오면 안 된다."""
    secrets = [p.name for p in CONFIG.iterdir() if p.name == ".env" or p.name.startswith(".env.")]

    assert secrets == [], f"자격증명 파일 유입: {secrets}"


def test_config_dir_holds_exactly_the_expected_files() -> None:
    """`config/` 의 인벤토리. 트레이더의 trading_rules.yaml 은 그대로, 그 외 유입 없음.

    12factor 의 "설정 인벤토리를 하나로 유지한다" 규칙을 파일 단위로 건다.
    설정 파일을 **의도적으로** 추가하는 태스크는 이 목록도 함께 갱신해야 한다 —
    목록에 없는 설정 파일은 아무도 관리하지 않고 썩는다.
    """
    names = {p.name for p in CONFIG.iterdir() if p.is_file()}

    assert names == {
        "trading_rules.yaml",
        "thresholds.yaml",
        "watchlist.txt",
        "overseas_watchlist.yaml",
    }


def test_trading_rules_is_untouched_and_still_parses() -> None:
    """이웃 파일 회귀 — 이 태스크는 트레이더 설정을 건드리지 않는다."""
    rules = yaml.safe_load((CONFIG / "trading_rules.yaml").read_text(encoding="utf-8"))

    assert isinstance(rules, dict) and rules


# --- 배포: tarball 에 실리는가 ----------------------------------------------


def test_package_json_ships_the_config_dir() -> None:
    """12factor 의 '하나의 아티팩트' — 설정 파일이 tarball 에 실려야 설치본이 돈다."""
    pkg = json.loads((REPO / "package.json").read_text(encoding="utf-8"))

    assert "config/" in pkg["files"], pkg["files"]
