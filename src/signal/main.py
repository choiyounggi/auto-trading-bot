"""일일 실행 엔트리포인트.

Usage:
    python -m src.signal.main             # 오늘 기준
    python -m src.signal.main 20260514    # 특정 일자
    python -m src.signal.main --dry-run   # 텔레그램 송신 생략, stdout만
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import yaml

from src.signal.analysis.flow_analyzer import (
    analyze_ticker_flow,
    cumulative_netbuy,
    cumulative_return,
)
from src.signal.analysis.llm_analyzer import analyze as llm_analyze
from src.signal.analysis.price_analyzer import analyze_ticker_price
from src.signal.analysis.signal_engine import combine, TickerSignal
from src.signal.analysis.strategy_builder import (
    build_short_balance_pool,
    build_strategy_signals,
    fetch_fundamentals,
)
from src.signal.analysis.overseas_strategy_builder import build_overseas_strategy_signals
from src.signal.analysis.ticker_context import build_context, build_prompt
from src.signal.data.macro_context import fetch_macro_snapshot, render_macro_block
from src.signal.data.short_balance import fetch_short_balance, render_short_block
from src.signal.data.news_brave import search_macro_headlines, search_news
from src.signal.data.naver_finance import fetch_all_kospi_kosdaq, render_naver_block
from src.signal.data.dump_signals import dump_signals_json
from src.signal.data.overseas_yfinance_source import (
    fetch_overseas_panel,
    fetch_yfinance_panel_symbol,
    load_overseas_watchlist,
    metadata_by_symbol,
)
from src.signal.data.pykrx_source import trading_days
from src.signal.data.sources import fetch_ticker_panel
from src.signal.notify.telegram_bot import send_message
from src.signal.universe import load_universe, ticker_to_name
from src.util.keychain import load_signal_keys, load_telegram_keys

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("stock-signal")


def load_config() -> dict:
    with open(ROOT / "config" / "thresholds.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging() -> None:
    logs_dir = ROOT / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    fname = logs_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(fname, encoding="utf-8"), logging.StreamHandler()],
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("date", nargs="?", default=None, help="YYYYMMDD (default: today)")
    p.add_argument("--date", dest="date_opt", default=None, help="YYYYMMDD (positional 대체)")
    p.add_argument("--dry-run", action="store_true", help="텔레그램 송신 생략")
    p.add_argument("--lookback", type=int, default=25, help="수집 거래일 수")
    p.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="0=전체. 디버깅용 상위 N개로 제한",
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="추적 종목 LLM 자동 분석 생략",
    )
    p.add_argument(
        "--max-llm",
        type=int,
        default=5,
        help="LLM 자동 분석 대상 종목 수 (BUY 점수 상위 N)",
    )
    p.add_argument(
        "--force-ticker",
        default=None,
        help="해당 종목코드만 panel 빌드 + LLM 분석 (BUY 임계 우회). 디버깅·즉시조회용.",
    )
    p.add_argument(
        "--overseas-only",
        action="store_true",
        help="해외주식 watchlist만 수집/신호 생성 (미국장 개장 직후 job용).",
    )
    p.add_argument(
        "--include-overseas",
        action="store_true",
        help="국내 일일 신호에 해외주식 후보도 함께 포함 (기본은 분리 운영).",
    )
    return p.parse_args(argv)


def evaluate_a3(
    panels: dict, params: dict
) -> set[str]:
    """cross-sectional A3: 누적 순매수 상위 + 수익률 하위 교집합."""
    accumulate_window = int(params.get("accumulate_window", 5))
    pct_rank = float(params.get("decoupling_pct_rank", 0.2))

    rows = []
    for tk, panel in panels.items():
        cn = cumulative_netbuy(panel, accumulate_window)
        cr = cumulative_return(panel, accumulate_window)
        rows.append((tk, cn, cr))
    if not rows:
        return set()

    # 누적 순매수 상위 pct_rank 비율
    rows.sort(key=lambda r: r[1], reverse=True)
    top_n = max(1, int(len(rows) * pct_rank))
    top_netbuy = {r[0] for r in rows[:top_n]}

    # 수익률 하위 pct_rank 비율 (덜 오른 종목)
    rows.sort(key=lambda r: r[2])
    low_return = {r[0] for r in rows[:top_n]}

    return top_netbuy & low_return


def format_message(
    buys: list[TickerSignal],
    cautions: list[TickerSignal],
    ref_date: str,
    strategy_signals: list[dict] | None = None,
) -> str:
    lines = [f"*수급 신호 리포트* `{ref_date}`", ""]
    if buys:
        lines.append("🔍 *추적* (스마트머니 유입 패턴)")
        for s in sorted(buys, key=lambda x: -x.score)[:15]:
            tg = ", ".join(s.triggers)
            lines.append(f"- `{s.ticker}` *{s.name}* (+{s.score})\n  └ {tg}")
        lines.append("")
    if cautions:
        lines.append("⚠️ *경계* (의심 패턴)")
        for s in sorted(cautions, key=lambda x: x.score)[:10]:
            tg = ", ".join(s.triggers)
            lines.append(f"- `{s.ticker}` *{s.name}* ({s.score})\n  └ {tg}")
    if strategy_signals:
        lines.append("🧠 *전략 후보* (다중 전략)")
        for s in sorted(strategy_signals, key=lambda x: -x.get("strategy_score", 0))[:12]:
            if s.get("eligible", True) is False:
                badge = f" 🚫부적격({', '.join(s.get('filter_reasons') or []) or '?'})"
            elif s.get("value_warnings"):
                badge = f" ⚠️{', '.join(s['value_warnings'])}"
            else:
                badge = ""
            lines.append(
                f"- `{s['ticker']}` *{s['name']}* "
                f"[{s['strategy_id']}] (+{s['strategy_score']}){badge}"
            )
        lines.append("")
    if not buys and not cautions and not strategy_signals:
        lines.append("_임계 도달 종목 없음_")
    lines.append("")
    lines.append("_※ 매매 추천이 아닌 수급 패턴 감지. 펀더멘털·뉴스·차트 별도 확인 필요._")
    return "\n".join(lines)


def run(argv: Iterable[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])
    setup_logging()
    # Keychain → os.environ inject. 로깅을 먼저 켜야 launchd 실행 기록에 남는다.
    # 리포트는 길이/상태 문자열만 담는다 — 값은 절대 로그로 나가지 않는다.
    signal_keys = load_signal_keys()
    telegram_keys = load_telegram_keys()
    log.info("signal credentials: %s", signal_keys)
    log.info("telegram credentials: %s", telegram_keys)
    cfg = load_config()
    params = cfg["params"]

    date_str = args.date or args.date_opt
    end = datetime.strptime(date_str, "%Y%m%d").date() if date_str else date.today()
    log.info("ref_date=%s lookback=%d", end, args.lookback)

    # 강제 분석 모드 — 종목 1개만 빌드 + LLM 호출 (BUY 임계 우회)
    if args.overseas_only:
        tickers = []
        log.info("overseas-only 모드: 국내 universe/watchlist skip")
    elif args.force_ticker:
        tickers = [args.force_ticker]
        log.info("force-ticker 모드: %s", args.force_ticker)
    else:
        tickers = []

    watchlist_path = ROOT / "config" / "watchlist.txt"
    if not tickers and not args.overseas_only and watchlist_path.exists():
        for ln in watchlist_path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            ticker = ln.split("#", 1)[0].strip()
            if ticker:
                tickers.append(ticker)
        if tickers:
            log.info("watchlist 모드: %d 종목", len(tickers))

    if not tickers and not args.overseas_only:
        universe = load_universe(cfg["indices"], end.strftime("%Y%m%d"))
        tickers = list(universe.keys())
        log.info("universe 모드: %d 종목", len(tickers))

    if args.max_tickers > 0:
        tickers = tickers[: args.max_tickers]
    log.info("최종 종목 수=%d", len(tickers))

    if not tickers and not args.overseas_only:
        log.error("종목 0개. config/watchlist.txt에 코드를 적거나 KRX_ID/PW 설정 필요.")
        return 2

    panels: dict = {}
    src_count = {"krx": 0, "naver": 0, "empty": 0}
    if tickers:
        log.info("computing trading day window...")
        days = trading_days(end, args.lookback)
        log.info("window %s ~ %s (%d days)", days[0], days[-1], len(days))

        log.info("fetching per-ticker panels (%d tickers)...", len(tickers))
        for i, tk in enumerate(tickers, 1):
            if i % 25 == 0 or i == len(tickers):
                log.info("  progress %d/%d", i, len(tickers))
            try:
                panel, src = fetch_ticker_panel(tk, days)
                panels[tk] = panel
                src_count[src] = src_count.get(src, 0) + 1
            except Exception as e:
                log.warning("  skip %s: %s", tk, e)
                panels[tk] = pd.DataFrame()
                src_count["empty"] += 1
    else:
        log.info("domestic ticker 없음 — KRX trading day/window skip")
    log.info("data sources used: %s", src_count)

    # 해외주식 watchlist는 yfinance 일봉으로 별도 수집하고, 전략 후보에만 편입한다.
    # 국내 수급 기반 flow/caution 로직은 6자리 KRX 종목에만 적용한다.
    domestic_tickers = list(tickers)
    overseas_watchlist = load_overseas_watchlist(ROOT / "config" / "overseas_watchlist.yaml") if (args.overseas_only or args.include_overseas) else []
    overseas_loaded = []
    if overseas_watchlist:
        log.info("fetching overseas panels (%d tickers)...", len(overseas_watchlist))
        for item in overseas_watchlist:
            panel = fetch_overseas_panel(item, end, args.lookback)
            if panel.empty:
                src_count["overseas_empty"] = src_count.get("overseas_empty", 0) + 1
                continue
            panels[item.symbol] = panel
            overseas_loaded.append(item)
            src_count["yfinance"] = src_count.get("yfinance", 0) + 1
        log.info("overseas panels loaded=%d", len(overseas_loaded))

    overseas_tickers = [item.symbol for item in overseas_loaded]
    overseas_meta = metadata_by_symbol(overseas_loaded)
    all_tickers = domestic_tickers + overseas_tickers

    overseas_refs: dict = {}
    if overseas_loaded:
        ref_symbols: set[str] = {"SPY", "QQQ", "^VIX"}
        for item in overseas_loaded:
            ref_symbols.add(item.benchmark_symbol)
            if item.sector_etf:
                ref_symbols.add(item.sector_etf)
        for ref in sorted(ref_symbols):
            if ref in panels:
                overseas_refs[ref] = panels[ref]
                continue
            try:
                scale_prices = ref != "^VIX"
                overseas_refs[ref] = fetch_yfinance_panel_symbol(
                    ref,
                    ref,
                    end,
                    args.lookback,
                    currency="USD" if scale_prices else "INDEX",
                    scale_prices=scale_prices,
                )
                if overseas_refs[ref].empty:
                    log.info("overseas ref %s: empty", ref)
            except Exception as e:
                log.warning("overseas ref %s fetch 실패: %s", ref, e)
                overseas_refs[ref] = pd.DataFrame()

    log.info("evaluating cross-sectional A3...")
    a3_set = evaluate_a3({tk: panels[tk] for tk in domestic_tickers if tk in panels}, params)

    log.info("computing per-ticker signals...")
    names = ticker_to_name(domestic_tickers)
    names.update({item.symbol: item.name for item in overseas_loaded})
    buys: list[TickerSignal] = []
    cautions: list[TickerSignal] = []
    buy_thr = int(cfg["signal"]["buy_threshold"])
    caution_thr = int(cfg["signal"]["caution_threshold"])

    for tk in domestic_tickers:
        panel = panels[tk]
        if panel.empty or panel["종가"].isna().all():
            continue
        flow = analyze_ticker_flow(panel, params)
        price = analyze_ticker_price(panel, params)
        sig = combine(
            tk,
            names.get(tk, tk),
            flow,
            price,
            cfg["buy_weights"],
            cfg["caution_weights"],
            a3_active=tk in a3_set,
        )
        if sig.score >= buy_thr:
            buys.append(sig)
        elif sig.score <= caution_thr:
            cautions.append(sig)

    if domestic_tickers:
        log.info("fundamentals fetch...")
        fundamentals = fetch_fundamentals(end)
        log.info("fundamentals rows=%d", len(fundamentals))
    else:
        fundamentals = {}
        log.info("fundamentals skip (overseas-only)")

    short_balances: dict = {}
    short_pool = [tk for tk in build_short_balance_pool(panels) if tk.isdigit()]
    log.info("short-cover pool=%d", len(short_pool))
    for tk in short_pool:
        sb = fetch_short_balance(tk)
        if sb:
            short_balances[tk] = sb

    strategy_signals = build_strategy_signals(
        tickers=domestic_tickers,
        names=names,
        panels=panels,
        flow_buys=buys,
        short_balances=short_balances,
        fundamentals=fundamentals,
    )
    if overseas_tickers:
        strategy_signals.extend(build_overseas_strategy_signals(
            tickers=overseas_tickers,
            names=names,
            panels=panels,
            metadata=overseas_meta,
            reference_panels=overseas_refs,
            market_risk_cfg=cfg.get("overseas_market_risk"),
        ))
        strategy_signals.sort(key=lambda x: (-x.get("strategy_score", 0), x.get("strategy_id", ""), x.get("ticker", "")))
    log.info(
        "BUY=%d CAUTION=%d strategy_signals=%d",
        len(buys), len(cautions), len(strategy_signals)
    )
    msg = format_message(buys, cautions, end.strftime("%Y-%m-%d"), strategy_signals)
    print(msg)

    # force-ticker 모드: 신호 임계 무관, 해당 ticker 강제 LLM 분석
    if args.force_ticker and args.force_ticker in panels:
        sig = next((s for s in buys + cautions if s.ticker == args.force_ticker), None)
        if sig is None:
            tk = args.force_ticker
            sig = TickerSignal(
                ticker=tk,
                name=names.get(tk, tk),
                score=0,
                triggers=["FORCE: 임계 미달 강제 분석"],
            )
        if sig not in buys:
            buys.append(sig)
        log.info("force-ticker LLM 대상으로 추가: %s (+%d)", sig.ticker, sig.score)

    def dump_snapshot(
        stage: str,
        macro_snaps: list | None = None,
        macro_news: list | None = None,
        naver_data: dict | None = None,
        llm_results: dict | None = None,
        short_balances: dict | None = None,
        strategy_signals_arg: list[dict] | None = None,
        fundamentals_arg: dict | None = None,
    ) -> None:
        """stock-trader가 읽을 signal JSON을 단계별로 저장."""
        try:
            out_path = dump_signals_json(
                today=end,
                buys=buys,
                cautions=cautions,
                panels=panels,
                llm_results=llm_results or {},
                short_balances=short_balances or {},
                macro_snaps=macro_snaps or [],
                macro_news=macro_news or [],
                naver_data=naver_data or {},
                strategy_signals=strategy_signals_arg or strategy_signals,
                fundamentals=fundamentals_arg or fundamentals,
                name_suffix=".us" if args.overseas_only else "",
            )
            log.info("signal JSON dump 완료(%s): %s", stage, out_path)
        except Exception as e:
            log.warning("signal JSON dump 실패(%s): %s", stage, e)

    if args.dry_run:
        log.info("dry-run: skip telegram + skip LLM")
        return 0

    if buys or cautions or strategy_signals:
        send_message(msg)
        log.info("telegram sent (summary)")
    else:
        if args.overseas_only:
            log.info("no signals (overseas-only); skip telegram")
        else:
            # 하트비트: 후보 0건이어도 하루 1회 "임계 도달 종목 없음"을 발송해
            # 침묵(봇 정상 vs 장애)의 모호함을 없앤다.
            send_message(msg)
            log.info("no signals; sent empty heartbeat")
        dump_snapshot("empty")
        return 0

    # 거시 컨텍스트 — LLM 분석 여부와 무관하게 trader 시장 레짐 필터용으로 저장
    log.info("거시 환경 fetch (yfinance + Brave)...")
    macro_snaps = fetch_macro_snapshot()
    macro_block = render_macro_block(macro_snaps)

    # 네이버 finance 통합 (KOSPI/KOSDAQ 가격 + 매매주체 + 시황 기사)
    naver_data: dict = {}
    try:
        naver_data = fetch_all_kospi_kosdaq(headlines_per_market=5, invest_days=5)
        naver_block = render_naver_block(naver_data)
        macro_block = macro_block + "\n" + naver_block
        log.info("  naver finance: kospi_quote=%s, kosdaq_flow=%d일, headlines=%d",
                 "OK" if naver_data.get("kospi_quote") else "FAIL",
                 len(naver_data.get("kospi_flow").flows) if naver_data.get("kospi_flow") else 0,
                 len(naver_data.get("headlines", [])))
    except Exception as e:
        log.warning("naver finance fetch 실패: %s", e)

    macro_news = search_macro_headlines(count_per_query=3)
    log.info("  macro indices=%d, headlines=%d", len(macro_snaps), len(macro_news))

    # LLM 분석이 길어져도 trader가 16:45에 읽을 수 있도록 선저장
    dump_snapshot(
        "pre-llm",
        macro_snaps=macro_snaps,
        macro_news=macro_news,
        naver_data=naver_data,
        short_balances=short_balances,
        strategy_signals_arg=strategy_signals,
        fundamentals_arg=fundamentals,
    )

    # 추적 종목 자동 분석 (LLM). flow BUY가 없으면 strategy 후보만으로 trader ENTRY LLM 판단.
    if args.no_llm or not buys:
        return 0

    targets = sorted(buys, key=lambda x: -x.score)[: args.max_llm]
    log.info("LLM 자동 분석 시작: %d 종목 (max_llm=%d)", len(targets), args.max_llm)

    # stock-trader가 읽을 dump용 누적 dict
    llm_results: dict = {}

    for sig in targets:
        try:
            panel = panels.get(sig.ticker, pd.DataFrame())
            news = search_news(sig.name, sig.ticker, count=6)
            sb = fetch_short_balance(sig.ticker)
            short_balances[sig.ticker] = sb
            short_block = render_short_block(sb)
            if sb:
                log.info("  공매도잔고 %s: %.2f%% (5d %+.2f, 20d %+.2f)",
                         sig.ticker, sb.latest_pct, sb.pct_5d_change, sb.pct_20d_change)
            ctx = build_context(
                sig.ticker, sig.name, sig.triggers, panel, news,
                macro_block=macro_block,
                macro_news=macro_news,
                short_block=short_block,
            )
            prompt = build_prompt(ctx)
            log.info("  → %s %s LLM 호출...", sig.ticker, sig.name)
            text, src = llm_analyze(prompt, timeout=180)
            log.info("  ← %s 응답 src=%s len=%d", sig.ticker, src, len(text))
            llm_results[sig.ticker] = {"source": src, "text": text, "length": len(text)}
            header = f"🔬 *{sig.name}* `{sig.ticker}` 분석 (+{sig.score})"
            triggers_line = "신호: " + ", ".join(sig.triggers)
            news_lines = []
            if news:
                news_lines.append("\n📰 관련 뉴스:")
                for n in news[:3]:
                    news_lines.append(f"- [{n.title[:60]}]({n.url})")
            footer = f"\n_분석 출처: {src}_"
            body = f"{header}\n_{triggers_line}_\n\n{text[:3500]}{''.join(news_lines)}{footer}"
            send_message(body, parse_mode="Markdown")
        except Exception as e:
            log.warning("  LLM 분석 실패 %s: %s", sig.ticker, e)
            try:
                send_message(f"⚠️ {sig.name} ({sig.ticker}) 분석 실패: {e}")
            except Exception:
                pass

    log.info("LLM 자동 분석 완료")

    # LLM 분석 결과 포함 최종 저장
    dump_snapshot(
        "final",
        macro_snaps=macro_snaps,
        macro_news=macro_news,
        naver_data=naver_data,
        llm_results=llm_results,
        short_balances=short_balances,
        strategy_signals_arg=strategy_signals,
        fundamentals_arg=fundamentals,
    )

    return 0


if __name__ == "__main__":
    sys.exit(run())
