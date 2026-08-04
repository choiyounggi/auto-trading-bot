"""시간외 단일가(ORD_DVSN=07) 모의투자 지원 여부 프로브 (2026-07-06).

launchd(GUI) 컨텍스트에서 실행 — SSH에선 Keychain 시크릿 읽기가 ACL 차단.
229200(KODEX 코스닥150) 1주를 종가로 시간외 단일가 매수 시도 → 응답 기록 →
접수되면 즉시 취소. 결과는 data/logs/probe_after_hours.log.
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(
        Path(__file__).resolve().parent.parent / "data/logs/probe_after_hours.log")],
)
log = logging.getLogger("probe")

import requests  # noqa: E402

from src.util.keychain import load_kis_keys  # noqa: E402
from src.broker.kis_client import KisClient  # noqa: E402

TICKER = "229200"  # KODEX 코스닥150 (~1.5만원 — 프로브 비용 최소)

load_kis_keys("paper")
client = KisClient(mode="paper", allow_trading=True)

quote = client.get_quote(TICKER)
if not quote or quote.current_price <= 0:
    log.error("시세 조회 실패 — 프로브 중단")
    sys.exit(1)
price = quote.current_price
log.info("프로브 시작: %s 1주 @ %d (시간외 단일가 ORD_DVSN=07)", TICKER, price)

body = {
    "CANO": client.cano,
    "ACNT_PRDT_CD": client.acnt_prdt_cd,
    "PDNO": TICKER,
    "ORD_DVSN": "07",  # 시간외 단일가
    "ORD_QTY": "1",
    "ORD_UNPR": str(price),
}
r = requests.post(
    f"{client.base_url}/uapi/domestic-stock/v1/trading/order-cash",
    headers=client._headers("VTTC0802U"),
    json=body,
    timeout=10,
)
data = r.json()
log.info("응답: rt_cd=%s msg_cd=%s msg1=%s", data.get("rt_cd"), data.get("msg_cd"), data.get("msg1"))
log.info("raw: %s", json.dumps(data, ensure_ascii=False)[:500])

if data.get("rt_cd") == "0":
    odno = (data.get("output") or {}).get("ODNO", "")
    log.info("접수됨! 주문번호=%s → 즉시 취소 시도", odno)
    ok = client.cancel_order(odno, TICKER, 1)
    log.info("취소 결과: %s", ok)
else:
    log.info("거부됨 — 모의투자 시간외 단일가 미지원 여부 판정 근거")
