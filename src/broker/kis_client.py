"""KIS (한국투자증권) REST API 클라이언트.

공식 SDK 참조: github.com/koreainvestment/open-trading-api/examples_user/kis_auth.py
REST 직접 호출 (MCP 우회) — JSON 응답, 결정론적 처리.

환경변수:
  KIS_MODE        = paper / real
  KIS_APP_KEY     = 앱키
  KIS_APP_SECRET  = 앱시크리트
  KIS_CANO        = 종합계좌번호 8자리 (앞)
  KIS_ACNT_PRDT_CD = 계좌상품코드 2자리 (뒤) — 보통 "01"

Token 캐시: ~/.kis-token-{paper|real}.json (24시간 유효, 6시간 이내 재발급 시 동일 token)
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE_URL_PROD = "https://openapi.koreainvestment.com:9443"
BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"


# 일시적 '초당 거래건수 초과' 재시도 정책 (wiki: timeouts-and-retries rule 4 / 429 행).
RATE_LIMIT_MARKER = "초당 거래건수"
RATE_LIMIT_MAX_ATTEMPTS = 3   # 총 시도 횟수 (2~3회 예산)
RATE_LIMIT_BASE_SEC = 0.5
RATE_LIMIT_CAP_SEC = 4.0


def _rate_limit_backoff(attempt: int, rand=random.random) -> float:
    """Full jitter: random(0, min(cap, base * 2**attempt))."""
    return rand() * min(RATE_LIMIT_CAP_SEC, RATE_LIMIT_BASE_SEC * 2 ** attempt)


def _base_url(mode: str) -> str:
    return BASE_URL_PROD if mode == "real" else BASE_URL_PAPER


def _token_cache_path(mode: str) -> Path:
    return Path.home() / f".kis-token-{mode}.json"


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int = 0) -> int:
    return int(_to_float(value, float(default)) or 0)


def _to_minor_price(value, scale: int = 100) -> int:
    return int(round(_to_float(value) * scale))


OVERSEAS_ORDER_TR_IDS: dict[tuple[str, str], tuple[str, str]] = {
    ("NASD", "buy"): ("TTTT1002U", "VTTT1002U"),
    ("NYSE", "buy"): ("TTTT1002U", "VTTT1002U"),
    ("AMEX", "buy"): ("TTTT1002U", "VTTT1002U"),
    ("NASD", "sell"): ("TTTT1006U", "VTTT1006U"),
    ("NYSE", "sell"): ("TTTT1006U", "VTTT1006U"),
    ("AMEX", "sell"): ("TTTT1006U", "VTTT1006U"),
    ("SEHK", "buy"): ("TTTS1002U", "VTTS1002U"),
    ("SEHK", "sell"): ("TTTS1001U", "VTTS1001U"),
    ("SHAA", "buy"): ("TTTS0202U", "VTTS0202U"),
    ("SHAA", "sell"): ("TTTS1005U", "VTTS1005U"),
    ("SZAA", "buy"): ("TTTS0305U", "VTTS0305U"),
    ("SZAA", "sell"): ("TTTS0304U", "VTTS0304U"),
    ("TKSE", "buy"): ("TTTS0308U", "VTTS0308U"),
    ("TKSE", "sell"): ("TTTS0307U", "VTTS0307U"),
    ("HASE", "buy"): ("TTTS0311U", "VTTS0311U"),
    ("HASE", "sell"): ("TTTS0310U", "VTTS0310U"),
    ("VNSE", "buy"): ("TTTS0311U", "VTTS0311U"),
    ("VNSE", "sell"): ("TTTS0310U", "VTTS0310U"),
}


def _overseas_order_tr_id(mode: str, exchange: str, side: str) -> str:
    prod, paper = OVERSEAS_ORDER_TR_IDS[(exchange.upper(), side)]
    return paper if mode == "paper" else prod


# ============================================================
# 데이터 클래스
# ============================================================

@dataclass
class Quote:
    ticker: str
    current_price: int = 0
    today_open: int = 0
    today_high: int = 0
    today_low: int = 0
    prev_close: int = 0
    d_change_pct: float = 0.0
    volume: int = 0
    market: str = "KR"
    currency: str = "KRW"
    price_scale: int = 1
    raw: dict = field(default_factory=dict)


@dataclass
class Balance:
    cash: int = 0           # 주문가능금액 / 예수금 (해외는 minor currency unit)
    total_eval: int = 0     # 총 평가금액
    positions: list[dict] = field(default_factory=list)
    currency: str = "KRW"
    price_scale: int = 1
    raw: dict = field(default_factory=dict)


@dataclass
class OrderResult:
    broker_order_id: str
    accepted: bool
    raw: dict


# ============================================================
# KIS Client
# ============================================================

class KisClient:
    def __init__(self, mode: str = "paper", allow_trading: bool = False):
        self.mode = mode.lower()
        self.allow_trading = allow_trading or os.environ.get("KIS_ENABLE_TRADING", "").lower() == "true"
        self.base_url = _base_url(self.mode)

        self.app_key = os.environ.get("KIS_APP_KEY", "")
        self.app_secret = os.environ.get("KIS_APP_SECRET", "")
        self.cano = os.environ.get("KIS_CANO", "")
        self.acnt_prdt_cd = os.environ.get("KIS_ACNT_PRDT_CD", "01")

        self._token: str | None = None
        self._expires_at: float = 0.0
        # 초당 거래건수 제한 (KIS REST: 모의 공칭 2건/초, 실전 20건/초).
        # 실측(2026-07-06): 모의서버는 같은 초 창의 2번째 호출도 거부하는 경우가 있어
        # 1.05s로 초 경계를 항상 넘긴다. 넘어도 get_quote 재시도가 안전망.
        self._min_request_interval = 1.05 if self.mode == "paper" else 0.06
        # HTTP read timeout — 실측(2026-08): 모의(VTS) 서버는 launchd 잡이 몰리는
        # 정각 시간대에 10초를 자주 넘긴다(read timeout 빈발). 실전 서버는 빠르고
        # 안정적이라 10초 유지 — 주문 경로가 죽은 서버에 오래 매달리지 않게 한다.
        self._http_timeout = 20 if self.mode == "paper" else 10
        self._last_request_at = 0.0
        self._load_token_cache()

    @contextmanager
    def session(self):
        """API 키 검증 + 사용 context (MCP 호환용 — 실제 stateful 연결 없음)."""
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET 미설정")
        if not self.cano:
            raise RuntimeError("KIS_CANO (계좌번호 8자리) 미설정")
        yield self

    # --------------------------------------------------------
    # Token 관리
    # --------------------------------------------------------

    def _load_token_cache(self) -> None:
        p = _token_cache_path(self.mode)
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            tok = data.get("access_token")
            exp = data.get("expires_at", 0)
            if tok and exp > time.time():
                self._token = tok
                self._expires_at = exp
                log.info("KIS token 캐시 로드 (만료: %s)",
                         datetime.fromtimestamp(exp).isoformat())
        except Exception as e:
            log.warning("token 캐시 로드 실패: %s", e)

    def _save_token_cache(self) -> None:
        p = _token_cache_path(self.mode)
        try:
            p.write_text(
                json.dumps({"access_token": self._token, "expires_at": self._expires_at}),
                encoding="utf-8",
            )
            os.chmod(p, 0o600)
        except Exception as e:
            log.warning("token 캐시 저장 실패: %s", e)

    def _get_token(self) -> str:
        if self._token and time.time() < self._expires_at:
            return self._token
        log.info("KIS OAuth 토큰 신규 발급 시도...")
        # 토큰 POST 도 초당 거래건수에 포함된다 — 다른 호출과 동일하게 간격을 두고
        # _last_request_at 을 갱신해 뒤따르는 API 호출이 같은 초에 겹치지 않게 한다.
        self._throttle()
        r = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=self._http_timeout,
        )
        if r.status_code != 200:
            log.warning("token 발급 실패: %d %s", r.status_code, r.text[:300])
            raise RuntimeError(f"KIS token 발급 실패: HTTP {r.status_code}")
        data = r.json()
        self._token = data.get("access_token")
        if not self._token:
            raise RuntimeError(f"access_token 응답 없음: {data}")
        # expires_in (초) 또는 access_token_token_expired (YYYY-MM-DD HH:MM:SS)
        exp_in = data.get("expires_in")
        if isinstance(exp_in, (int, float)):
            self._expires_at = time.time() + float(exp_in) - 3600  # 1시간 안전 여유
        else:
            exp_str = data.get("access_token_token_expired")
            if exp_str:
                self._expires_at = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").timestamp() - 3600
            else:
                self._expires_at = time.time() + 23 * 3600  # default 23h
        self._save_token_cache()
        log.info("KIS token 신규 발급 완료 (만료: %s)",
                 datetime.fromtimestamp(self._expires_at).isoformat())
        return self._token

    def _throttle(self) -> None:
        """연속 API 호출 간 최소 간격 보장 — '초당 거래건수 초과' 방지.

        모든 API 호출이 요청 직전 _headers()를 거치므로 여기서 일괄 적용된다.
        """
        wait = self._min_request_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _headers(self, tr_id: str) -> dict:
        # 토큰을 먼저 해결한다. 발급이 일어나면 그 POST 가 _throttle() 을 거치므로
        # 아래 _throttle() 이 이번 API 호출을 토큰 POST 로부터 띄운다.
        token = self._get_token()
        self._throttle()
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }

    # --------------------------------------------------------
    # 잔고 조회
    # --------------------------------------------------------

    def get_balance(self, *, sleep=time.sleep, rand=random.random) -> Balance | None:
        """모의: VTTC8434R / 실전: TTTC8434R.

        일시적 '초당 거래건수 초과'와 네트워크 오류(read timeout 등)는
        full jitter 백오프로 최대 RATE_LIMIT_MAX_ATTEMPTS 회까지 재시도한다.
        그 외 rt_cd != "0" 거절은 즉시 None (재시도 대상이 아니다).

        네트워크 오류를 재시도하는 근거(2026-08 실측): launchd 잡들이 정각에
        일제히 시작해 VTS 서버가 그 시간대에 read timeout을 자주 냈고, 같은
        틱 내 재시도만으로 회복 가능한 일시 장애였다.
        """
        tr_id = "VTTC8434R" if self.mode == "paper" else "TTTC8434R"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        for attempt in range(RATE_LIMIT_MAX_ATTEMPTS):
            try:
                r = requests.get(
                    f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
                    headers=self._headers(tr_id),
                    params=params,
                    timeout=self._http_timeout,
                )
                data = r.json()
                if data.get("rt_cd") != "0":
                    msg = data.get("msg1") or ""
                    if RATE_LIMIT_MARKER in msg and attempt < RATE_LIMIT_MAX_ATTEMPTS - 1:
                        wait = _rate_limit_backoff(attempt, rand=rand)
                        log.info("get_balance rate limit → %.2fs 후 재시도 (%d/%d)",
                                 wait, attempt + 2, RATE_LIMIT_MAX_ATTEMPTS)
                        sleep(wait)
                        continue
                    log.warning("get_balance 실패: %s %s", data.get("rt_cd"), msg)
                    return None
                output1 = data.get("output1", [])  # 보유종목 array
                output2 = data.get("output2", [])  # 계좌요약 array (single)
                summary = output2[0] if output2 else {}
                return Balance(
                    cash=int(summary.get("prvs_rcdl_excc_amt", 0) or 0),  # 가수도정산금액 (출금가능)
                    total_eval=int(summary.get("tot_evlu_amt", 0) or 0),  # 총평가금액
                    positions=[{
                        "ticker": p.get("pdno"),
                        "name": p.get("prdt_name"),
                        "qty": int(p.get("hldg_qty", 0) or 0),
                        "avg_price": int(float(p.get("pchs_avg_pric", 0) or 0)),
                        "current_price": int(p.get("prpr", 0) or 0),
                        "eval_amt": int(p.get("evlu_amt", 0) or 0),
                        "pnl_pct": float(p.get("evlu_pfls_rt", 0) or 0),
                    } for p in output1],
                    raw=data,
                )
            except requests.RequestException as e:
                if attempt < RATE_LIMIT_MAX_ATTEMPTS - 1:
                    wait = _rate_limit_backoff(attempt, rand=rand)
                    log.info("get_balance 네트워크 오류 → %.2fs 후 재시도 (%d/%d): %s",
                             wait, attempt + 2, RATE_LIMIT_MAX_ATTEMPTS, e)
                    sleep(wait)
                    continue
                log.warning("get_balance 네트워크 오류 (재시도 소진): %s", e)
                return None

    def get_overseas_balance(self, exchange: str = "NASD", currency: str = "USD") -> Balance | None:
        """해외주식 잔고 조회. 모의: VTTS3012R / 실전: TTTS3012R."""
        tr_id = "VTTS3012R" if self.mode == "paper" else "TTTS3012R"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "TR_CRCY_CD": currency,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        try:
            r = requests.get(
                f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance",
                headers=self._headers(tr_id),
                params=params,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                log.warning("get_overseas_balance 실패: %s %s", data.get("rt_cd"), data.get("msg1"))
                return None
            output1 = data.get("output1", []) or []
            output2 = data.get("output2", {}) or {}
            if isinstance(output2, list):
                summary = output2[0] if output2 else {}
            else:
                summary = output2
            positions = []
            for row in output1:
                symbol = row.get("ovrs_pdno") or row.get("pdno") or row.get("ovrs_item_cd")
                qty = _to_int(row.get("ovrs_cblc_qty") or row.get("cblc_qty") or row.get("hldg_qty"))
                if not symbol or qty <= 0:
                    continue
                positions.append({
                    "ticker": str(symbol),
                    "name": row.get("ovrs_item_name") or row.get("prdt_name") or str(symbol),
                    "qty": qty,
                    "avg_price": _to_minor_price(row.get("pchs_avg_pric") or row.get("frcr_pchs_amt1"), 100),
                    "current_price": _to_minor_price(row.get("now_pric2") or row.get("ovrs_now_pric1") or row.get("ovrs_stck_prpr"), 100),
                    "eval_amt": _to_minor_price(row.get("ovrs_stck_evlu_amt") or row.get("frcr_evlu_amt2"), 100),
                    "pnl_pct": _to_float(row.get("evlu_pfls_rt")),
                    "asset_class": "overseas_stock",
                    "exchange": exchange,
                    "currency": currency,
                    "price_scale": 100,
                })
            cash = _to_minor_price(
                summary.get("frcr_dncl_amt_2")
                or summary.get("frcr_buy_psbl_amt")
                or summary.get("ord_psbl_frcr_amt")
                or summary.get("tot_frcr_cblc_smtl"),
                100,
            )
            total_eval = _to_minor_price(
                summary.get("ovrs_tot_pfls")
                or summary.get("tot_evlu_pfls_amt")
                or summary.get("frcr_evlu_tota"),
                100,
            )
            return Balance(
                cash=cash,
                total_eval=total_eval,
                positions=positions,
                currency=currency,
                price_scale=100,
                raw=data,
            )
        except requests.RequestException as e:
            log.warning("get_overseas_balance 네트워크 오류: %s", e)
            return None

    def get_deposit(self) -> int:
        """주문가능금액 단독 조회 (VTTC8908R / TTTC8908R)."""
        tr_id = "VTTC8908R" if self.mode == "paper" else "TTTC8908R"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": "005930",  # 임의 종목
            "ORD_UNPR": "0",
            "ORD_DVSN": "01",  # 시장가
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        try:
            r = requests.get(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                headers=self._headers(tr_id),
                params=params,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                return 0
            return int(data.get("output", {}).get("ord_psbl_cash", 0) or 0)
        except requests.RequestException:
            return 0

    # --------------------------------------------------------
    # 시세 조회
    # --------------------------------------------------------

    def get_quote(self, ticker: str, _retry: bool = True) -> Quote | None:
        """현재가. tr_id: FHKST01010100 (실/모의 동일).

        '초당 거래건수 초과'(rate limit)는 1초 대기 후 1회 재시도한다.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
        }
        try:
            r = requests.get(
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers("FHKST01010100"),
                params=params,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                msg = data.get("msg1") or ""
                if _retry and "초당 거래건수" in msg:
                    log.info("get_quote %s rate limit → 1초 후 재시도", ticker)
                    time.sleep(1.0)
                    return self.get_quote(ticker, _retry=False)
                log.warning("get_quote %s 실패: %s", ticker, msg)
                return None
            o = data.get("output", {})
            return Quote(
                ticker=ticker,
                current_price=int(o.get("stck_prpr", 0) or 0),
                today_open=int(o.get("stck_oprc", 0) or 0),
                today_high=int(o.get("stck_hgpr", 0) or 0),
                today_low=int(o.get("stck_lwpr", 0) or 0),
                prev_close=int(o.get("stck_sdpr", 0) or 0),
                d_change_pct=float(o.get("prdy_ctrt", 0) or 0),
                volume=int(o.get("acml_vol", 0) or 0),
                raw=data,
            )
        except requests.RequestException as e:
            log.warning("get_quote %s 네트워크 오류: %s", ticker, e)
            return None

    def get_overseas_quote(
        self,
        symbol: str,
        quote_exchange: str = "NAS",
        currency: str = "USD",
        price_scale: int = 100,
    ) -> Quote | None:
        """해외주식 현재가. tr_id: HHDFS00000300 (실/모의 공통)."""
        params = {"AUTH": "", "EXCD": quote_exchange, "SYMB": symbol}
        try:
            r = requests.get(
                f"{self.base_url}/uapi/overseas-price/v1/quotations/price",
                headers=self._headers("HHDFS00000300"),
                params=params,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                log.warning("get_overseas_quote %s 실패: %s", symbol, data.get("msg1"))
                return None
            o = data.get("output", {}) or {}

            def price(*keys: str) -> int:
                for key in keys:
                    if o.get(key) not in (None, ""):
                        return _to_minor_price(o.get(key), price_scale)
                return 0

            def num(*keys: str) -> int:
                for key in keys:
                    if o.get(key) not in (None, ""):
                        return _to_int(o.get(key))
                return 0

            return Quote(
                ticker=symbol,
                current_price=price("last", "ovrs_nmix_prpr", "stck_prpr", "close"),
                today_open=price("open", "ovrs_nmix_oprc", "stck_oprc"),
                today_high=price("high", "ovrs_nmix_hgpr", "stck_hgpr"),
                today_low=price("low", "ovrs_nmix_lwpr", "stck_lwpr"),
                prev_close=price("base", "ovrs_nmix_sdpr", "stck_sdpr"),
                d_change_pct=_to_float(o.get("rate") or o.get("prdy_ctrt")),
                volume=num("tvol", "acml_vol"),
                market="OVERSEAS",
                currency=currency,
                price_scale=price_scale,
                raw=data,
            )
        except requests.RequestException as e:
            log.warning("get_overseas_quote %s 네트워크 오류: %s", symbol, e)
            return None

    # --------------------------------------------------------
    # 주문 — 매수/매도/취소
    # --------------------------------------------------------

    def get_overseas_buyable(self, ticker: str, price: float, exchange: str = "NASD") -> dict | None:
        """해외주식 매수가능금액조회 (통합증거금/원화 반영). read-only.

        모의: VTTS3007R / 실전: TTTS3007R. 반환: output dict (실패 시 None).
        """
        tr_id = "VTTS3007R" if self.mode == "paper" else "TTTS3007R"
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange.upper(),
            "OVRS_ORD_UNPR": f"{float(price):.2f}",
            "ITEM_CD": ticker.upper(),
        }
        try:
            r = requests.get(
                f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-psamount",
                headers=self._headers(tr_id),
                params=params,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                log.warning("get_overseas_buyable 실패: %s %s", data.get("rt_cd"), data.get("msg1"))
                return None
            return data.get("output", {}) or {}
        except requests.RequestException as e:
            log.warning("get_overseas_buyable 네트워크 오류: %s", e)
            return None

    def get_daily_closes(self, ticker: str, count: int = 7) -> list[int]:
        """최근 일별 종가(과거→최근). KIS 국내주식 기간별시세 FHKST03010100."""
        from datetime import date, timedelta
        end = date.today()
        start = end - timedelta(days=count * 3 + 12)
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        try:
            r = requests.get(
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                headers=self._headers("FHKST03010100"),
                params=params,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                log.warning("get_daily_closes %s 실패: %s", ticker, data.get("msg1"))
                return []
            rows = data.get("output2", []) or []
            parsed = []
            for row in rows:
                d = row.get("stck_bsop_date")
                cl = row.get("stck_clpr")
                if d and cl:
                    try:
                        parsed.append((d, int(cl)))
                    except ValueError:
                        pass
            parsed.sort(key=lambda x: x[0])
            return [c for _, c in parsed][-count:]
        except requests.RequestException as e:
            log.warning("get_daily_closes %s 네트워크 오류: %s", ticker, e)
            return []

    def submit_buy(self, ticker: str, qty: int, price: int, order_type: str = "limit") -> OrderResult:
        return self._submit_order("buy", ticker, qty, price, order_type=order_type)

    def submit_sell(self, ticker: str, qty: int, price: int, order_type: str = "limit") -> OrderResult:
        return self._submit_order("sell", ticker, qty, price, order_type=order_type)

    def submit_overseas_buy(
        self,
        symbol: str,
        qty: int,
        price_minor: int,
        exchange: str = "NASD",
        price_scale: int = 100,
    ) -> OrderResult:
        return self._submit_overseas_order("buy", symbol, qty, price_minor, exchange, price_scale)

    def submit_overseas_sell(
        self,
        symbol: str,
        qty: int,
        price_minor: int,
        exchange: str = "NASD",
        price_scale: int = 100,
    ) -> OrderResult:
        return self._submit_overseas_order("sell", symbol, qty, price_minor, exchange, price_scale)

    def _submit_overseas_order(
        self,
        side: str,
        symbol: str,
        qty: int,
        price_minor: int,
        exchange: str,
        price_scale: int,
    ) -> OrderResult:
        if not self.allow_trading:
            log.warning(
                "KIS_ENABLE_TRADING 미설정 — 해외 주문 거부 (%s %s %d@%s)",
                side, symbol, qty, price_minor,
            )
            return OrderResult("", False, {"error": "trading_disabled"})
        if qty <= 0 or price_minor <= 0:
            return OrderResult("", False, {"error": "invalid_qty_or_price"})

        exchange = exchange.upper()
        tr_id = _overseas_order_tr_id(self.mode, exchange, side)
        price_text = f"{float(price_minor) / price_scale:.2f}" if price_scale > 1 else str(price_minor)
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": price_text,
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # paper 해외주문은 지정가만 안전하게 사용
        }
        if side == "sell":
            body["SLL_TYPE"] = "00"
        try:
            r = requests.post(
                f"{self.base_url}/uapi/overseas-stock/v1/trading/order",
                headers=self._headers(tr_id),
                json=body,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                log.warning("해외 주문 실패 %s %s: %s", side, symbol, data.get("msg1"))
                return OrderResult("", False, data)
            output = data.get("output", {}) or {}
            broker_id = output.get("ODNO") or output.get("odno") or output.get("KRX_FWDG_ORD_ORGNO", "")
            return OrderResult(broker_order_id=str(broker_id), accepted=True, raw=data)
        except (requests.RequestException, KeyError) as e:
            log.warning("해외 주문 네트워크/파라미터 오류 %s %s: %s", side, symbol, e)
            return OrderResult("", False, {"error": str(e)})

    def _submit_order(self, side: str, ticker: str, qty: int, price: int,
                      order_type: str = "limit") -> OrderResult:
        if not self.allow_trading:
            log.warning("KIS_ENABLE_TRADING 미설정 — 주문 거부 (%s %s %d@%d)", side, ticker, qty, price)
            return OrderResult("", False, {"error": "trading_disabled"})

        # 모의/실전 + buy/sell tr_id
        if self.mode == "paper":
            tr_id = "VTTC0802U" if side == "buy" else "VTTC0801U"
        else:
            tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"

        is_market = order_type == "market"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": ticker,
            "ORD_DVSN": "01" if is_market else "00",  # 01=시장가, 00=지정가
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0" if is_market else str(price),
        }
        try:
            r = requests.post(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(tr_id),
                json=body,
                timeout=self._http_timeout,
            )
            data = r.json()
            if data.get("rt_cd") != "0":
                log.warning("주문 실패 %s %s: %s", side, ticker, data.get("msg1"))
                return OrderResult("", False, data)
            output = data.get("output", {})
            broker_id = output.get("ODNO") or output.get("KRX_FWDG_ORD_ORGNO", "")
            return OrderResult(broker_order_id=str(broker_id), accepted=True, raw=data)
        except requests.RequestException as e:
            log.warning("주문 네트워크 오류 %s %s: %s", side, ticker, e)
            return OrderResult("", False, {"error": str(e)})

    def cancel_order(self, broker_order_id: str, ticker: str, qty: int) -> bool:
        if not self.allow_trading:
            return False
        tr_id = "VTTC0803U" if self.mode == "paper" else "TTTC0803U"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": broker_order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소 / 01=정정
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        try:
            r = requests.post(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl",
                headers=self._headers(tr_id),
                json=body,
                timeout=self._http_timeout,
            )
            return r.json().get("rt_cd") == "0"
        except requests.RequestException as e:
            log.warning("cancel_order %s 실패: %s", broker_order_id, e)
            return False

    def modify_order(self, broker_order_id: str, ticker: str, qty: int, new_price: int) -> bool:
        """정정 주문. cancel과 동일 endpoint, RVSE_CNCL_DVSN_CD='01'."""
        if not self.allow_trading:
            return False
        tr_id = "VTTC0803U" if self.mode == "paper" else "TTTC0803U"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": broker_order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "01",  # 정정
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(new_price),
            "QTY_ALL_ORD_YN": "Y",
        }
        try:
            r = requests.post(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl",
                headers=self._headers(tr_id),
                json=body,
                timeout=self._http_timeout,
            )
            return r.json().get("rt_cd") == "0"
        except requests.RequestException as e:
            log.warning("modify_order %s 실패: %s", broker_order_id, e)
            return False
