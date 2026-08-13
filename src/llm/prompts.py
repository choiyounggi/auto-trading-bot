"""LLM 프롬프트 템플릿 — 진입 결정 + 동적 조정."""
from __future__ import annotations

ENTRY_PROMPT_TEMPLATE = """\
국내/해외 주식 단기 트레이딩 자동매매 시스템의 진입 결정을 내립니다.

[종목]
{ticker} {name}

[신호]
시그널 점수: {score}/10
감지된 신호 유형: {triggers}

[전략 컨텍스트]
{strategy_block}

[종목 사전 분석 — stock-signal-bot LLM]
{prior_llm_analysis}

[최근 시세 + 수급 요약]
{panel_block}

[공매도 잔고]
{short_block}

[글로벌 거시 환경]
{macro_block}

[거시·종목 뉴스]
{news_block}

[계좌 상태]
총자산(예수금+평가): {total_asset_won:,} 원
현재 투자중: {invested_won:,} 원 (가동률 {utilization_pct:.1f}% / 목표 {target_utilization_pct:.0f}%)
이번 배치 가능액: {deployable_won:,} 원
보유 종목 수: {open_positions}/{max_positions}
오늘 누적 PnL: {daily_pnl_pct:+.2f}%

[운영 룰 — 절대 준수]
- 일반 후보는 최소 confidence 5.0/10. 그 미만은 SKIP. 단, paper_probe 대상(turtle_breakout 또는 asset_class=overseas_stock)은 confidence 7.5 이상이면 소액 probe BUY 가능.
- size_pct 권장: 8~15% (총자산 대비 비율이다. 확신 보통=8~11%, high-conviction=12~15%).
  - clamp 범위 5~15%. 확신이 보통이어도 BUY 가능(근거가 명백히 약할 때만 SKIP). paper_probe 대상은 0.5~1.0% 소액 진입 허용.
  - high-conviction이어도 15% 초과 금지. paper_probe 대상은 코드가 1% 이하로 다시 줄인다. 여러 종목이 같은 수급/시장 팩터에 묶일 수 있음을 고려.
- 너는 전략 생성자가 아니라 리스크 veto 담당이다. 주어진 전략의 근거가 약하거나 뉴스/거시/품질 리스크가 명백하면 SKIP.
- 해외주식(asset_class=overseas_stock)은 전략 features의 currency/price_scale/broker_symbol을 확인하라. 가격은 코드가 신호 종가 기반 minor unit(예: USD cent)으로 보정하므로, LLM은 BUY/SKIP 리스크 판단에 집중한다.
- 해외주식 features에 normal_take_profit_pct/price_zscore_20d/daily_return_zscore_60d가 있으면 이를 우선 신뢰한다. 정규분포 기반 목표는 보수적 익절용이며, z-score tail 과열 후보는 SKIP 성향으로 판단한다.
- 실제 주문 수량은 코드가 `초기 손절까지의 계좌 리스크 한도`로 다시 줄인다. 리스크를 키우려고 size_pct를 높이지 말 것.
- 계좌에 남은 배치 가능액을 코드가 상한으로 적용한다. 가동률을 채우려고 size_pct를 부풀리지 말 것.
- stop_loss_pct: 최소 1.5%, 최대 3.0% (clamp). turtle_breakout은 코드가 ATR20×2 기반으로 보정하므로 LLM이 손절폭을 넓히려 하지 말 것.
- take_profit_pct: 최소 2.0%, 최대 10.0% (clamp). turtle_breakout은 고정 TP보다 추세 지속이 핵심이므로 명백한 리스크가 없으면 SKIP 대신 BUY 유지 여부만 판단.
- max_hold_days: 기본 5 영업일, turtle_breakout은 코드가 최대 15영업일로 보정 가능.

[시장 레짐 가이드]
- 시장 지수 약세는 참고 지표일 뿐, 개별 종목의 수급·돌파가 강하면 진입 가능하다 (paper 검증 단계 — 약세장에서도 거래 샘플을 확보한다).
- 상향장 추격 + 외인 매수 가속이 가장 명확한 진입 조건이지만 유일한 조건은 아니다.

[종합 판단 후 다음 JSON 한 개만 출력. 마크다운 코드 블록 금지.]

{{
  "action": "BUY" 또는 "SKIP",
  "entry_strategy": "MARKET_OPEN" (익일 시가) 또는 "LIMIT_TODAY_AFTER_HOURS" (오늘 시간외 단일가),
  "entry_price": <국내는 정수 원, 해외는 신호 가격 기준 정수 minor unit>,
  "size_pct": <5.0~15.0>,
  "stop_loss_pct": <0.5~10.0>,
  "take_profit_pct": <0.5~20.0>,
  "max_hold_days": <1~15>,
  "confidence": <1~10>,
  "key_thesis": "<한 문장. 왜 사는지>",
  "key_risks": ["<위험 1>", "<위험 2>"],
  "watch_signals": ["<보유 중 매 30분 모니터링 시 감시할 신호>"]
}}
"""


MONITOR_PROMPT_TEMPLATE = """\
보유 중인 국내/해외 주식 종목의 동적 관리 결정을 내립니다.

[종목]
{ticker} {name}

[진입 정보]
진입가: {entry_price:,} (국내=원, 해외=minor unit)
진입 시각: {entry_at}
현재 손절가: {current_stop:,} 원
현재 익절가: {current_tp:,} 원
보유 기간: {hold_days}일 / 최대 {max_hold_days}일
TP 상향 횟수: {tp_raised_count}/{max_tp_raises}
Trailing stop 활성: {trailing_active}

[진입 시 LLM thesis]
{key_thesis}

[진입 시 LLM이 감시하라고 한 신호]
{watch_signals}

[현재 상황]
현재가: {current_price:,} 원 ({pnl_pct:+.2f}%)
오늘 거래량 / 평소: {volume_ratio:.1f}배
일중 고가/저가: {today_high:,} / {today_low:,}
현재 매크로 요약: {macro_brief}
관련 뉴스 (최근 4h): {recent_news}

[안전 규칙 — 너의 출력에 자동 적용됨]
- new_stop_loss는 현재 손절가보다 **반드시 같거나 높아야** 함. 더 낮으면 거부됨.
- new_take_profit은 현재가보다 반드시 높아야 함.
- new_take_profit은 진입가 × 1.10 이내 (절대 cap).
- CLOSE_NOW는 confidence ≥ 5 일 때만 인정됨.

[판단 가이드]
- 손절 근처 + thesis 흐트러짐 → CLOSE_NOW (대기 손실 키우지 말 것)
- 익절 도달 직전 + 추가 모멘텀 신호 → RAISE_TP (얕게 +1~2% 추가)
- 위험 증가 (매크로 악화, 거래량 감소, 외인 매도) → TIGHTEN_STOP
- 변동 없음 + 시간 충분 → HOLD

[다음 JSON 한 개만 출력. 마크다운 코드 블록 금지.]

{{
  "action": "HOLD" 또는 "TIGHTEN_STOP" 또는 "RAISE_TP" 또는 "CLOSE_NOW",
  "new_stop_loss": <정수 또는 null>,
  "new_take_profit": <정수 또는 null>,
  "close_urgency": "IMMEDIATE" 또는 "END_OF_DAY" 또는 null,
  "confidence": <1~10>,
  "reason": "<한 문장>"
}}
"""
