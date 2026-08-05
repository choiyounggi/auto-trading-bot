"""LLM 분석용 종목 컨텍스트 — 가격/수급/뉴스 + 거시 환경 통합."""
from __future__ import annotations

from typing import Iterable

import pandas as pd

from src.signal.data.news_brave import NewsItem


def _fmt_won_eok(v: float) -> str:
    eok = v / 1e8
    sign = "+" if eok >= 0 else ""
    return f"{sign}{eok:,.0f}억"


def build_context(
    ticker: str,
    name: str,
    triggers: Iterable[str],
    panel: pd.DataFrame,
    news: list[NewsItem],
    macro_block: str = "",
    macro_news: list[NewsItem] | None = None,
    short_block: str = "",
    last_n: int = 7,
) -> str:
    lines: list[str] = []
    lines.append(f"[종목] {name} ({ticker})")
    lines.append(f"[감지 신호] {', '.join(triggers)}")
    lines.append("")

    if not panel.empty:
        tail = panel.tail(last_n)
        lines.append(f"[최근 {len(tail)}일 시세]")
        for dt, row in tail.iterrows():
            close = int(row.get("종가", 0))
            chg = float(row.get("등락률", 0.0))
            vol = int(row.get("거래량", 0))
            lines.append(f"  {dt}  종가 {close:>8,}  등락률 {chg:+.2f}%  거래량 {vol:>10,}")
        lines.append("")

        lines.append(f"[최근 {len(tail)}일 수급 (순매수 거래대금)]")
        lines.append(f"  {'일자':<10} {'외국인':>10} {'기관':>10} {'개인':>10} {'금융투자':>10}")
        for dt, row in tail.iterrows():
            lines.append(
                f"  {dt:<10} "
                f"{_fmt_won_eok(float(row.get('foreign_net', 0))):>10} "
                f"{_fmt_won_eok(float(row.get('inst_net', 0))):>10} "
                f"{_fmt_won_eok(float(row.get('indiv_net', 0))):>10} "
                f"{_fmt_won_eok(float(row.get('finance_net', 0))):>10}"
            )
        lines.append("")

    if macro_block:
        lines.append(macro_block)
        lines.append("")

    if short_block:
        lines.append(short_block)
        lines.append("")

    if macro_news:
        lines.append("[거시 헤드라인 (최근 24h)]")
        for n in macro_news[:8]:
            age = f" ({n.age})" if n.age else ""
            lines.append(f"  - {n.title}{age}")
            if n.description:
                lines.append(f"    └ {n.description[:140]}")
        lines.append("")

    if news:
        lines.append("[종목 관련 뉴스 (최근 1주)]")
        for n in news[:6]:
            age = f" ({n.age})" if n.age else ""
            lines.append(f"  - {n.title}{age}")
            if n.description:
                lines.append(f"    └ {n.description[:140]}")
        lines.append("")
    else:
        lines.append("[종목 뉴스] 없음")
        lines.append("")

    return "\n".join(lines)


PROMPT_TEMPLATE = """한국 주식 단일 종목의 수급/가격, 글로벌 거시 환경, 종목·거시 뉴스를 종합 분석합니다.

{context}

---
요청: 한국어 7~10문장 분석. 반드시 다음을 모두 포함:

1) **신호 발생 원인 추정** — 수급 패턴 + 종목 뉴스 + 업종 사이클을 연결해서 "왜 이런 신호가 나왔는지" 가장 그럴듯한 가설.

2) **거시 환경이 이 신호의 가치에 미친 영향** — 시장 전체(코스피/코스닥/다우/나스닥)가 같은 방향으로 움직였다면, 종목 고유 강세인지 시장에 휩쓸린 것인지 명시. 예: "코스피 -3%인 날 종목 -5%면 시장 영향 + 종목 약세 추가, +2%면 시장 역행 강세".

3) **거시 이벤트의 단발성 vs 지속성 추정** — 지정학·정치 발언(트럼프/시진핑/지정학)은 보통 1~3일 단발 영향이 큼. 통화정책(금리·QT·환율)은 수 주~수 개월. 실적·구조 변화는 분기 단위. 어디에 해당하는지 추정과 근거.

4) **2차/3차 파급효과 분석** — 표면적 영향 외에 연쇄 효과. 예: "이란 강경 발언 → 유가↑ → 항공·화학 부정적, 정유·방산 긍정적 / 위험회피 심리 → 미국채 매수 → 달러 강세 → 신흥국 자금 유출 → 한국 IT·바이오 매도".

5) **단기 위험 요소** — 이미 급등 여부, 차익매물 가능성, 임박한 이벤트(실적·임상·FOMC).

6) **다음에 같은 신호가 나오면 어떻게 판단할지 1줄 가이드** — 이번 사례에서 배운 점.

규칙 (반드시 준수):
- 매매 추천 절대 금지. "사세요/파세요/지금이 기회/매수 추천" 같은 표현 금지.
- 데이터에 없는 내용은 추측 금지 — "확인 필요"로 표기.
- 객관적·간결. 마크다운 헤더(#) 사용 금지. 문장 단위로 구성.
- 종목 뉴스가 없거나 거시가 강하게 영향 줬으면 그 사실을 명시.
"""


def build_prompt(context_block: str) -> str:
    return PROMPT_TEMPLATE.format(context=context_block)
