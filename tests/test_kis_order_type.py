# -*- coding: utf-8 -*-
from __future__ import annotations

from src.broker.kis_client import KisClient


def _client(monkeypatch, captured):
    c = KisClient(mode="paper")
    c.allow_trading = True
    c.cano = "12345678"
    c.acnt_prdt_cd = "01"
    c.base_url = "http://test"
    monkeypatch.setattr(c, "_headers", lambda tr_id: {})

    class _Resp:
        def json(self):
            return {"rt_cd": "0", "output": {"ODNO": "0001"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr("src.broker.kis_client.requests.post", fake_post)
    return c


def test_market_order_body(monkeypatch):
    captured = {}
    c = _client(monkeypatch, captured)
    r = c.submit_buy("009150", 3, 2184000, order_type="market")
    assert r.accepted is True
    assert captured["json"]["ORD_DVSN"] == "01"   # 시장가
    assert captured["json"]["ORD_UNPR"] == "0"
    assert captured["json"]["ORD_QTY"] == "3"


def test_limit_order_body_default(monkeypatch):
    captured = {}
    c = _client(monkeypatch, captured)
    c.submit_buy("009150", 2, 2184000)
    assert captured["json"]["ORD_DVSN"] == "00"   # 지정가(기본)
    assert captured["json"]["ORD_UNPR"] == "2184000"


def test_trading_disabled_rejects(monkeypatch):
    captured = {}
    c = _client(monkeypatch, captured)
    c.allow_trading = False
    r = c.submit_buy("009150", 1, 1000, order_type="market")
    assert r.accepted is False     # 가드: trading 비활성 → post 미호출
    assert "json" not in captured
