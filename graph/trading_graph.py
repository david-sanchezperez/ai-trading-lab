"""
Sprint 1: LangGraph backbone
Encadena los agentes existentes como nodos de un StateGraph.
"""

from typing import TypedDict, Optional
import logging
import time
import pandas as pd

from langgraph.graph import StateGraph, END

from core.data_loader import fetch_data
from core.indicators import add_indicators, add_relative_strength
from agents.technical_agent import generate_signal
from agents.decision_agent import make_decision
from core.news_fetcher import get_ticker_sentiment
from core.session_logger import get_session_logger

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session broker — una única conexión IBKR por ciclo diario.
#
# analyze_all.main() la inicializa antes del loop de tickers y la cierra al
# final. _execute_ibkr la reutiliza sin reconectar ni desconectar entre
# tickers, eliminando los 20 ciclos connect/disconnect y los conflictos de
# client_id que causaban fallos intermitentes.
# ---------------------------------------------------------------------------

_session_broker = None  # BaseBroker | None


def set_session_broker(broker) -> None:
    global _session_broker
    _session_broker = broker


def clear_session_broker() -> None:
    global _session_broker
    if _session_broker is not None:
        try:
            _session_broker.disconnect()
        except Exception:
            pass
        _session_broker = None


# ---------------------------------------------------------------------------
# Estado compartido
# ---------------------------------------------------------------------------

class TradingState(TypedDict):
    ticker: str
    df: Optional[pd.DataFrame]
    technical_result: Optional[dict]
    sentiment_result: Optional[dict]
    critic_result: Optional[dict]
    decision: Optional[dict]
    execution_result: Optional[dict]
    portfolio: Optional[dict]
    portfolio_sim: Optional[dict]
    regime_adjustment: Optional[float]
    intraday_context: Optional[dict]  # accumulated by MarketMonitor during the day


# ---------------------------------------------------------------------------
# Nodos
# ---------------------------------------------------------------------------

def data_node(state: TradingState) -> dict:
    """Carga OHLCV para el ticker desde CSV (si existe y es del día) o descarga de Yahoo."""
    from datetime import date
    from core.config import DATA_DIR
    from core.data_loader import save_data

    ticker = state["ticker"]
    csv_path = DATA_DIR / f"{ticker}.csv"

    if csv_path.exists():
        mtime = date.fromtimestamp(csv_path.stat().st_mtime)
        if mtime == date.today():
            df = pd.read_csv(csv_path)
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            df = df.dropna(subset=["Date", "Close"]).reset_index(drop=True)
            return {"df": df}

    # CSV no existe o es de otro día — descargar y guardar
    df = fetch_data(ticker, period="1y")
    save_data(df, ticker)
    return {"df": df}


def indicators_node(state: TradingState) -> dict:
    """Añade indicadores técnicos al dataframe, incluyendo fortaleza relativa vs SPY."""
    import pandas as pd
    from datetime import date
    from core.config import DATA_DIR
    from core.data_loader import fetch_data, save_data

    df = add_indicators(state["df"])

    # SPY para fortaleza relativa — usa caché diaria igual que data_node
    spy_path = DATA_DIR / "SPY.csv"
    spy_df = None
    try:
        if spy_path.exists() and date.fromtimestamp(spy_path.stat().st_mtime) == date.today():
            spy_df = pd.read_csv(spy_path)
        else:
            spy_df = fetch_data("SPY", period="1y")
            save_data(spy_df, "SPY")
    except Exception:
        pass  # RS_SPY quedará en 0.0

    df = add_relative_strength(df, spy_df)

    return {"df": df}


def technical_node(state: TradingState) -> dict:
    """Genera señal técnica (RSI + SMA)."""
    t0 = time.monotonic()
    result = generate_signal(state["df"])
    logger = get_session_logger()
    if logger:
        logger.log_node("technical_node", state["ticker"], {
            "signal":     result["signal"],
            "confidence": result["confidence"],
            "rsi":        result["rsi"],
            "trend_up":   result["trend_up"],
            "buy_votes":  result.get("buy_votes", 0),
            "sell_votes": result.get("sell_votes", 0),
            "atr_14":     result.get("atr_14", 0),
        }, (time.monotonic() - t0) * 1000)
    return {"technical_result": result}


def sentiment_node(state: TradingState) -> dict:
    """Analiza sentimiento con FinBERT + StockTwits (small caps) sobre titulares Yahoo Finance."""
    from datetime import datetime
    from core.sentiment_store import save_sentiment
    from core.social_sentiment import get_stocktwits_sentiment

    ticker = state["ticker"]
    result = get_ticker_sentiment(ticker, max_items=10)

    # StockTwits para tickers con alto seguimiento retail
    stocktwits = get_stocktwits_sentiment(ticker)

    # Persistir para que historical_sentiment esté disponible en ciclos futuros
    if result["headlines"] > 0:
        save_sentiment(
            date=datetime.now().strftime("%Y-%m-%d"),
            ticker=ticker,
            sentiment=result["sentiment"],
            confidence=result["confidence"],
        )

    return {
        "sentiment_result": {
            "sentiment": result["sentiment"],
            "confidence": result["confidence"],
            "headlines": result["headlines"],
            "raw_results": result["raw_results"],
            "raw": f"{result['headlines']} headlines · score {result['sentiment']:+.4f}",
            "stocktwits": stocktwits,
        }
    }


def critic_node(state: TradingState) -> dict:
    """
    Critic agent: desafía las conclusiones técnicas y de sentimiento.
    Usa DeepSeek-R1:32b — el bloque <think> es parte del output visible.
    RAG: recupera situaciones históricas similares antes de razonar.
    """
    import requests
    from core.rag_store import get_similar_situations
    t0 = time.monotonic()

    technical = state["technical_result"] or {}
    sentiment = state["sentiment_result"] or {}
    df = state["df"]
    ticker = state["ticker"]

    signal = technical.get("signal", "N/A")
    confidence = technical.get("confidence", 0)
    rsi = technical.get("rsi", "N/A")
    price = technical.get("price", "N/A")
    sentiment_score = sentiment.get("sentiment", 0)

    # ── Fast path: skip DeepSeek cuando el Critic no puede cambiar el resultado ─
    # Condición 0 — SELL sin posición abierta: la ejecución lo ignoraría de todas formas
    # Condición 1 — señal muy fuerte: incluso con penalización del Critic (×0.85) y el
    #               umbral máximo de régimen (×1.3=0.91), score sigue siendo BUY/SELL.
    #               Umbral: abs(score) > 0.91/0.85 ≈ 1.07
    # Condición 2 — señal muy débil: score < 0.45×0.85 ≈ 0.38, HOLD inevitable.
    _preview = make_decision(state["technical_result"], state["sentiment_result"], ticker)
    score_preview = _preview["score"]

    regime_adj      = state.get("regime_adjustment") or 1.0
    effective_thr   = max(0.65, 0.7 * regime_adj)   # suelo 0.65 — regime calmado no baja de ahí
    strong_cutoff   = effective_thr / 0.85          # score que sobrevive penalización del Critic
    weak_cutoff     = effective_thr * 0.80          # score que no cruzaría el umbral sin penalización

    portfolio_state = state.get("portfolio_sim") or {}
    has_position = ticker in portfolio_state.get("positions", {})

    fast_path_reason = None
    if signal == "SELL" and not has_position:
        fast_path_reason = "SELL sin posición abierta — ejecución ignorada de todas formas"
    elif abs(score_preview) > strong_cutoff:
        # Score tan alto que incluso con penalización del Critic y el umbral máximo, sigue siendo señal.
        fast_path_reason = f"strong signal — score={score_preview:+.3f} > {strong_cutoff:.2f}"
    elif abs(score_preview) < weak_cutoff:
        # Score tan bajo que no cruzaría el threshold ni sin penalización.
        fast_path_reason = f"weak signal — score={score_preview:+.3f} < {weak_cutoff:.2f}"

    if fast_path_reason:
        print(f"=== CRITIC FAST PATH: {fast_path_reason} ===")
        fast_result = {
            "approved":         True,
            "verdict":          "APPROVED",
            "thinking":         "",
            "reasoning":        f"Fast path — {fast_path_reason}. LLM skipped.",
            "technical_signal": signal,
            "sentiment_score":  sentiment_score,
            "rag_precedents":   [],
            "fast_path":        True,
            "fast_path_reason":    fast_path_reason,
            "scenario":            None,
            "key_question":        None,
            "error":               False,
        }
        logger = get_session_logger()
        if logger:
            logger.log_critic(
                ticker=ticker,
                prompt_excerpt="",
                response=fast_path_reason,
                rag_docs=[],
                fast_path=True,
                verdict="APPROVED",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        return {"critic_result": fast_result}
    # ── Fin fast path ────────────────────────────────────────────────────────────

    volume_ratio = technical.get("volume_ratio", 1.0)
    pct_52w      = technical.get("pct_52w_range", 0.5)
    rs_spy       = technical.get("rs_spy", 0.0)
    buy_votes    = technical.get("buy_votes", 0)
    sell_votes   = technical.get("sell_votes", 0)
    trend_up     = float(df.iloc[-1]["SMA_20"]) > float(df.iloc[-1]["SMA_50"])

    indicators = {
        "rsi":          technical["rsi"],
        "sma20":        float(df.iloc[-1]["SMA_20"]),
        "sma50":        float(df.iloc[-1]["SMA_50"]),
        "momentum":     float(df.iloc[-1]["Momentum"]),
        "confidence":   technical["confidence"],
        "volume_ratio": volume_ratio,
        "pct_52w":      pct_52w,
        "rs_spy":       rs_spy,
    }

    from core.data_loader import get_ticker_metadata
    from core.rag_store import get_company_context
    ticker_meta = get_ticker_metadata(ticker)
    similar = get_similar_situations(
        ticker, indicators, signal, n=3,
        thesis=ticker_meta.get("thesis"),
    )

    if similar:
        precedents = "Based on similar historical situations:\n"
        for s in similar:
            m = s["metadata"]
            o5  = m.get("outcome_5d", 0.0)
            o10 = m.get("outcome_10d", 0.0)
            has_outcomes = o5 != 0.0 or o10 != 0.0
            if has_outcomes:
                precedents += (
                    f"- {m['date'][:10]}: {s['text']} "
                    f"(similarity: {s['similarity']:.2f})\n"
                )
            else:
                precedents += (
                    f"- {m['date'][:10]}: RSI={m['rsi']:.1f}, {m['signal']} "
                    f"→ outcome: {m['outcome']} (similarity: {s['similarity']:.2f})\n"
                )
        precedents += "Use these precedents in your analysis.\n\n"
    else:
        precedents = ""

    company_ctx = get_company_context(ticker)
    company_section = f"Company context:\n{company_ctx}\n\n" if company_ctx else ""

    # Evento clave del día via Qwen3.6 (solo si hay headlines suficientes)
    from core.news_analyzer import extract_key_event
    raw_results = sentiment.get("raw_results", [])
    key_event = extract_key_event(ticker, raw_results) if len(raw_results) >= 2 else None
    key_event_section = f"Key news event today: {key_event}\n\n" if key_event else ""

    # StockTwits (small caps con retail following)
    stocktwits = sentiment.get("stocktwits")
    stocktwits_section = ""
    if stocktwits:
        stocktwits_section = (
            f"StockTwits sentiment: {stocktwits['bullish_pct']:.0%} bullish / "
            f"{stocktwits['bearish_pct']:.0%} bearish "
            f"({stocktwits['labeled']} labeled messages)\n\n"
        )

    # Intraday context from MarketMonitor (if available for this ticker)
    intraday_ctx = state.get("intraday_context") or {}
    intraday_section = ""
    ticker_ctx = {k: v for k, v in intraday_ctx.items() if ticker in k}
    if ticker_ctx:
        import json as _json
        intraday_section = (
            f"Intraday context for {ticker} today:\n"
            + _json.dumps(ticker_ctx, indent=2, default=str)[:800]
            + "\n\n"
        )

    rsi = technical.get("rsi", 50)

    if rsi < 35 and signal == "SELL":
        scenario = "RSI in oversold zone but SELL signal — potential contradiction"
        key_question = "Is selling justified when RSI signals oversold conditions?"

    elif rsi > 65 and signal == "BUY":
        scenario = "RSI in overbought zone but BUY signal — potential overextension"
        key_question = "Is buying justified when RSI signals overbought conditions?"

    elif signal == "BUY" and not trend_up and rs_spy < -0.03:
        scenario = "BUY against bearish trend with relative weakness vs SPY"
        key_question = "Is it prudent to buy when the ticker underperforms the market in a downtrend?"

    elif signal == "BUY" and pct_52w > 0.82:
        scenario = "BUY near 52-week highs — potential overextension"
        key_question = "Is the price too extended near its 52-week highs?"

    elif signal == "BUY" and sentiment_score < -0.3:
        scenario = "BUY signal but negative sentiment — divergence"
        key_question = "Does negative sentiment invalidate the bullish signal?"

    elif signal == "SELL" and sentiment_score > 0.3:
        scenario = "SELL signal but positive sentiment — divergence"
        key_question = "Does positive sentiment invalidate the bearish signal?"

    elif signal == "HOLD":
        scenario = "HOLD signal — evaluate if there is reason to act"
        key_question = "Is there a clear reason to act, or is holding the correct decision?"

    else:
        scenario = "Aligned signals — verify coherence"
        key_question = "Are all indicators pointing in the same direction?"

    prompt = f"""You are a critical analyst reviewing a trading signal.

Scenario detected: {scenario}

Current data:
- Signal: {signal} (confidence: {confidence:.0%}) — votes {buy_votes}B / {sell_votes}S
- RSI: {rsi:.2f}
- Price: ${price:.2f}
- Trend: {"UPTREND" if trend_up else "DOWNTREND"} (SMA20 vs SMA50)
- 52w range position: {pct_52w:.0%} (0%=52w low, 100%=52w high)
- Volume ratio: {volume_ratio:.1f}x average
- Relative strength vs SPY (20d excess return): {rs_spy:+.2%}
- Sentiment: {sentiment_score:+.4f} ({sentiment.get('headlines', 0)} headlines)

{company_section}{key_event_section}{stocktwits_section}{intraday_section}{precedents}Key question: {key_question}

Analyze:
1. Is the scenario coherent or contradictory?
2. What do the historical precedents suggest?
3. Does the trend, relative strength, and 52w position support the signal?
4. Does sentiment support or contradict the signal?
5. On the very last line of your response, write exactly one of:
   VERDICT: APPROVED
   VERDICT: CHALLENGED
   Do not write anything after the verdict line.
"""

    from core.config import CRITIC_LLM_URL, CRITIC_LLM_MODEL

    try:
        response = requests.post(
            CRITIC_LLM_URL,
            json={
                "model": CRITIC_LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": 0.6,
                "top_p": 0.95,
                "top_k": 20,
                "presence_penalty": 1.5,
                "chat_template_kwargs": {"enable_thinking": True},
            },
            timeout=120,
        )
        response.raise_for_status()

        msg      = response.json()["choices"][0]["message"]
        thinking = msg.get("reasoning_content") or ""
        content  = msg.get("content") or ""

        print("=== THINKING ===")
        print(thinking)
        print("=== RESPONSE ===")
        print(content)

        last_line = content.strip().split("\n")[-1].upper()
        approved  = "VERDICT: APPROVED" in last_line

        critic_result = {
            "approved":            approved,
            "verdict":             "APPROVED" if approved else "CHALLENGED",
            "thinking":            thinking,
            "reasoning":           content,
            "technical_signal":    signal,
            "sentiment_score":     sentiment_score,
            "rag_precedents":      similar,
            "fast_path":           False,
            "fast_path_reason":    None,
            "scenario":            scenario,
            "key_question":        key_question,
            "error":               False,
        }

    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.HTTPError,
        KeyError,
        IndexError,
        ValueError,
    ) as e:
        log.error(f"[critic_node] {ticker}: LLM no disponible — {type(e).__name__}: {e}")
        critic_result = {
            "approved":            True,
            "verdict":             "APPROVED_ON_ERROR",
            "thinking":            "",
            "reasoning":           f"LLM unavailable: {type(e).__name__}: {e}",
            "technical_signal":    signal,
            "sentiment_score":     sentiment_score,
            "rag_precedents":      similar,
            "fast_path":           True,
            "fast_path_reason":    None,
            "scenario":            scenario,
            "key_question":        key_question,
            "error":               True,
        }

    logger = get_session_logger()
    if logger:
        logger.log_critic(
            ticker=ticker,
            prompt_excerpt=prompt,
            response=critic_result.get("reasoning", ""),
            rag_docs=similar,
            fast_path=critic_result.get("fast_path", False),
            verdict=critic_result.get("verdict", ""),
            duration_ms=(time.monotonic() - t0) * 1000,
            error=critic_result.get("error", False),
        )

    return {"critic_result": critic_result}


def decision_node(state: TradingState) -> dict:
    """
    Toma la decisión final combinando técnico + sentimiento.

    Dos mecanismos ortogonales:
    - score * 0.85: penalización individual si el Critic lanzó CHALLENGED
    - effective_threshold: umbral ajustado por régimen macro (0.8x-1.3x)
    """
    t0 = time.monotonic()
    critic = state.get("critic_result") or {}
    critic_approved = critic.get("approved", True)

    regime_adjustment = state.get("regime_adjustment") or 1.0
    base_threshold = 0.7
    effective_threshold = round(max(0.65, base_threshold * regime_adjustment), 4)

    decision = make_decision(state["technical_result"], state["sentiment_result"], state["ticker"])

    score = decision["score"]
    score = score * 0.85 if not critic_approved else score
    critic_override = not critic_approved

    if score > effective_threshold:
        action = "BUY"
    elif score < -effective_threshold:
        action = "SELL"
    else:
        action = "HOLD"

    decision["action"] = action
    decision["threshold_used"] = effective_threshold
    decision["regime_adjustment"] = regime_adjustment
    decision["critic_override"] = critic_override

    logger = get_session_logger()
    if logger:
        tech   = state.get("technical_result") or {}
        tech_s = {"BUY": 1, "SELL": -1, "HOLD": 0}.get(tech.get("signal", "HOLD"), 0)
        tech_c = float(tech.get("confidence", 0.0))
        sent_v = float((state.get("sentiment_result") or {}).get("sentiment", 0.0))
        wr     = decision.get("win_rate") or {}
        logger.log_node("decision_node", state["ticker"], {
            "score":                round(score, 3),
            "action":               action,
            "threshold_used":       effective_threshold,
            "regime_adjustment":    regime_adjustment,
            "critic_override":      critic_override,
            "tech":                 round(tech_s * tech_c, 3),
            "sentiment":            round(sent_v * 0.6, 3),
            "historical_sentiment": round(decision.get("historical_sentiment", 0.0) * 0.3, 3),
            "wr_contribution":      wr.get("contribution", 0),
            "wr_key":               wr.get("key"),
            "pead":                 decision.get("pead", 0),
            "insider":              decision.get("insider", 0),
        }, (time.monotonic() - t0) * 1000)

    # Audit trail de la evaluación completa (propuesta → critic → decisión).
    # log_evaluation nunca lanza — un fallo de auditoría no interrumpe el pipeline.
    from audit.decision_audit import log_evaluation
    log_evaluation(state, decision, final_score=score, effective_threshold=effective_threshold)

    return {"decision": decision}


def _days_to_earnings(ticker: str) -> int | None:
    """Días hasta el próximo earnings. None si no hay datos o falla."""
    from datetime import date as _date, datetime as _dt
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        today = _date.today()
        dates = []
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
        elif hasattr(cal, "loc"):
            try:
                row = cal.loc["Earnings Date"]
                dates = list(row.values)
            except KeyError:
                return None
        for d in dates:
            if d is None or str(d) == "NaT":
                continue
            if hasattr(d, "date"):
                d = d.date()
            elif isinstance(d, str):
                try:
                    d = _dt.fromisoformat(d[:10]).date()
                except ValueError:
                    continue
            days = (d - today).days
            if days >= -1:
                return days
        return None
    except Exception:
        return None


def execution_node(state: TradingState) -> dict:
    """
    Ejecuta la orden via broker (PAPER_LOCAL o IBKR).

    PAPER_LOCAL: portfolio simulado en JSON local (comportamiento original).
    PAPER_IBKR / LIVE: bracket orders en IB Gateway, portfolio leído de IBKR.
    """
    t0 = time.monotonic()
    from config.broker_config import BROKER_MODE, BrokerMode

    if BROKER_MODE == BrokerMode.PAPER_LOCAL:
        result = _execute_paper_local(state)
    else:
        result = _execute_ibkr(state)

    logger = get_session_logger()
    if logger:
        exec_res     = result.get("execution_result") or {}
        trade        = exec_res.get("trade") or {}
        trade_detail = (trade.get("trade") or {}) if isinstance(trade, dict) else {}
        log_status   = "error" if exec_res.get("status") == "error" else "ok"
        logger.log_node("execution_node", state["ticker"], {
            "action":   exec_res.get("action", "HOLD"),
            "status":   trade.get("status", ""),
            "quantity": trade_detail.get("quantity"),
            "price":    trade_detail.get("price"),
            "reason":   trade.get("reason"),
        }, (time.monotonic() - t0) * 1000, status=log_status)

    return result


def _execute_paper_local(state: TradingState) -> dict:
    """Lógica original de ejecución contra portfolio simulado en JSON."""
    import core.portfolio_sim as sim
    from datetime import datetime

    decision = state["decision"]
    action = decision.get("action", "HOLD") if decision else "HOLD"
    ticker = state["ticker"]
    price = state["technical_result"]["price"]
    date_str = datetime.now().strftime("%Y-%m-%d")

    portfolio_state = state.get("portfolio_sim") or sim.load()

    trade_result = None
    if action == "SELL" and ticker not in portfolio_state.get("positions", {}):
        trade_result = {"status": "skipped", "reason": "No position to sell"}
        action = "HOLD"

    if action == "BUY":
        from core.config import EARNINGS_BUFFER_DAYS
        days_to_earnings = _days_to_earnings(ticker)
        if days_to_earnings is not None and -1 <= days_to_earnings <= EARNINGS_BUFFER_DAYS:
            trade_result = {"status": "skipped", "reason": f"Earnings en {days_to_earnings}d — riesgo de gap"}
            action = "HOLD"

    if action in ("BUY", "SELL"):
        atr_14 = state["technical_result"].get("atr_14", 0)
        portfolio_value = portfolio_state.get("cash", 0) + sum(
            p["quantity"] * p["avg_price"]
            for p in portfolio_state.get("positions", {}).values()
        )
        quantity = sim.compute_quantity_atr(ticker, price, atr_14, portfolio_value, risk_per_trade=0.01)

        if action == "BUY":
            min_cash_reserve = portfolio_value * 0.20
            cost_of_trade = quantity * price
            if portfolio_state["cash"] - cost_of_trade < min_cash_reserve:
                affordable_qty = max(0, int((portfolio_state["cash"] - min_cash_reserve) / price))
                if affordable_qty == 0:
                    trade_result = {"status": "skipped", "reason": "Cash reserve mínima (20%) — no hay margen para comprar"}
                    action = "HOLD"
                else:
                    quantity = affordable_qty

        if action == "BUY":
            max_exposure = portfolio_value * 0.15
            current_exposure = portfolio_state.get("positions", {}).get(ticker, {}).get("quantity", 0) * price
            room = max_exposure - current_exposure
            if room <= 0:
                trade_result = {"status": "skipped", "reason": f"Max position cap (15%) alcanzado para {ticker}"}
                action = "HOLD"
            else:
                quantity = min(quantity, max(1, int(room / price)))

        if action in ("BUY", "SELL"):
            if action == "BUY":
                result = sim.buy(portfolio_state, ticker, price, quantity, date_str)
            else:
                full_qty = portfolio_state["positions"][ticker]["quantity"]
                result = sim.sell(portfolio_state, ticker, price, full_qty, date_str)

            trade_result = {"status": result["status"]}
            if result["status"] == "filled":
                trade_result["trade"] = result["trade"]
                from core import entry_tracker
                if action == "BUY":
                    entry_tracker.record_entry(ticker, price, atr_14, quantity, date_str)
                elif action == "SELL":
                    entry_tracker.remove_entry(ticker)
            else:
                trade_result["reason"] = result.get("reason")

            portfolio_state = result["state"]

    positions_prices = {
        t: pos["avg_price"]
        for t, pos in portfolio_state.get("positions", {}).items()
    }
    positions_prices[ticker] = price
    portfolio_summary = sim.summary(portfolio_state, positions_prices)

    execution_result = {
        "status": "executed",
        "action": action,
        "trade": trade_result,
        "portfolio": portfolio_summary,
    }
    return {
        "execution_result": execution_result,
        "portfolio": portfolio_summary,
        "portfolio_sim": portfolio_state,
    }


def _execute_ibkr(state: TradingState) -> dict:
    """Ejecución via IB Gateway (PAPER_IBKR / LIVE)."""
    import core.portfolio_sim as sim
    from brokers import get_broker
    from core.config import EARNINGS_BUFFER_DAYS
    import logging as _log
    _logger = _log.getLogger(__name__)

    decision = state["decision"]
    action = decision.get("action", "HOLD") if decision else "HOLD"
    ticker = state["ticker"]
    price = state["technical_result"]["price"]
    atr_14 = state["technical_result"].get("atr_14", 0)

    # Preferir el session broker (una única conexión para todo el ciclo) para
    # evitar los 20 ciclos connect/disconnect que causan conflictos de client_id.
    # Si no existe (ejecución fuera del scheduler), crear una conexión propia.
    using_session = _session_broker is not None
    if using_session:
        broker = _session_broker
        if not broker.ensure_connected():
            _logger.error(f"[execute_ibkr] {ticker}: session broker no pudo reconectar")
            return {
                "execution_result": {"status": "error", "action": "HOLD",
                                     "trade": {"status": "rejected", "reason": "Session broker sin conexión"},
                                     "portfolio": {}},
                "portfolio": {},
                "portfolio_sim": state.get("portfolio_sim"),
            }
    else:
        broker = get_broker()
        if not broker.connect():
            return {
                "execution_result": {"status": "error", "action": "HOLD",
                                     "trade": {"status": "rejected", "reason": "IB Gateway no disponible"},
                                     "portfolio": {}},
                "portfolio": {},
                "portfolio_sim": state.get("portfolio_sim"),
            }

    trade_result = None
    portfolio_summary = {}

    try:
        positions = broker.get_positions()
        cash = broker.get_cash()
        portfolio_value = broker.get_portfolio_value()

        if action == "SELL" and positions.get(ticker, {}).get("quantity", 0) <= 0:
            trade_result = {"status": "skipped", "reason": "No position to sell"}
            action = "HOLD"

        # Cubrir corto involuntario antes de nada: si hay una posición corta
        # (originada por un bug de bracket u otra causa), una señal BUY debe
        # cerrarla con una orden de mercado simple, no abrir un bracket largo
        # nuevo — la lógica de exposición/cap de abajo asume posiciones largas
        # y calcularía mal el "room" disponible con quantity negativa.
        if action == "BUY" and positions.get(ticker, {}).get("quantity", 0) < 0:
            cover_qty = abs(positions[ticker]["quantity"])
            trade_result = broker.place_order(ticker, "BUY", cover_qty, price)
            if trade_result.get("status") in ("filled", "submitted"):
                from core import entry_tracker
                entry_tracker.remove_entry(ticker)
            portfolio_summary = {
                "cash":        broker.get_cash(),
                "positions":   broker.get_positions(),
                "total_value": broker.get_portfolio_value(),
            }
            return {
                "execution_result": {
                    "status": "executed", "action": "COVER",
                    "trade": trade_result, "portfolio": portfolio_summary,
                },
                "portfolio": portfolio_summary,
                "portfolio_sim": state.get("portfolio_sim"),
            }

        # Evitar órdenes BUY duplicadas: si ya hay una orden GTC BUY activa en IBKR
        # para este ticker, no colocar otra. Usa open orders live (no local_state,
        # que no se limpia automáticamente cuando un bracket se cierra).
        if action == "BUY":
            try:
                pending_tickers = broker.get_pending_buy_tickers() if hasattr(broker, "get_pending_buy_tickers") else set()
                if ticker in pending_tickers:
                    trade_result = {"status": "skipped", "reason": f"Orden BUY activa en IBKR para {ticker} — esperando fill"}
                    action = "HOLD"
            except Exception:
                pass  # si falla la consulta, seguimos con el flujo normal

        if action == "BUY":
            days_to_earnings = _days_to_earnings(ticker)
            if days_to_earnings is not None and -1 <= days_to_earnings <= EARNINGS_BUFFER_DAYS:
                trade_result = {"status": "skipped", "reason": f"Earnings en {days_to_earnings}d — riesgo de gap"}
                action = "HOLD"

        if action in ("BUY", "SELL"):
            quantity = sim.compute_quantity_atr(ticker, price, atr_14, portfolio_value, risk_per_trade=0.01)

            if action == "BUY":
                min_cash_reserve = portfolio_value * 0.20
                cost_of_trade = quantity * price
                if cash - cost_of_trade < min_cash_reserve:
                    affordable_qty = max(0, int((cash - min_cash_reserve) / price))
                    if affordable_qty == 0:
                        trade_result = {"status": "skipped", "reason": "Cash reserve mínima (20%) — no hay margen para comprar"}
                        action = "HOLD"
                    else:
                        quantity = affordable_qty

            if action == "BUY":
                max_exposure = portfolio_value * 0.15
                current_qty = positions.get(ticker, {}).get("quantity", 0)
                room = max_exposure - current_qty * price
                if room <= 0:
                    trade_result = {"status": "skipped", "reason": f"Max position cap (15%) alcanzado para {ticker}"}
                    action = "HOLD"
                else:
                    quantity = min(quantity, max(1, int(room / price)))

        # Beta cap — bloquear/reducir si beta proyectada excede límites.
        # Se pasa portfolio_value para que el denominador incluya cash, no solo posiciones.
        if action == "BUY":
            try:
                from analytics.portfolio_risk import PortfolioRiskMonitor
                proj_beta = PortfolioRiskMonitor().projected_beta_after_entry(
                    ticker, quantity, positions, new_price=price,
                    portfolio_value=portfolio_value,
                )
                if proj_beta > 1.6:
                    trade_result = {"status": "skipped", "reason": f"Beta cap: beta proyectada {proj_beta:.2f} > 1.6"}
                    action = "HOLD"
                elif proj_beta > 1.4:
                    quantity = max(1, int(quantity * 0.7))
            except Exception:
                pass

        if action == "BUY":
            effective_atr = atr_14 if atr_14 and atr_14 > 0 else price * 0.035
            stop_price = round(price - 2.0 * effective_atr, 4)
            tp1_price  = round(price + 2.0 * effective_atr, 4)
            tp_price   = round(price + 3.0 * effective_atr, 4)
            trade_result = broker.place_bracket_order(ticker, "BUY", quantity, price, stop_price, tp_price)
            if trade_result.get("status") in ("filled", "submitted"):
                try:
                    from notifications.telegram_notifier import notify_order_executed
                    notify_order_executed(
                        ticker=ticker, action="BUY", qty=quantity,
                        fill_price=trade_result.get("trade", {}).get("price", price),
                        stop=stop_price, tp1=tp1_price, tp2=tp_price,
                        score=state.get("decision", {}).get("score", 0),
                        regime=state.get("decision", {}).get("regime_adjustment", 1.0),
                        cash=cash - quantity * price,
                        n_positions=len(positions) + 1,
                    )
                except Exception:
                    pass
                # Ledger + slippage
                try:
                    from analytics.prediction_ledger import get_ledger
                    from analytics.slippage_analyzer import get_slippage_analyzer
                    fill     = trade_result.get("trade", {}).get("price", price)
                    order_id = str(trade_result.get("order_ids", {}).get("entry", "") or "")
                    pid = get_ledger().log_signal(state, trade_result)
                    get_ledger().log_fill(pid, fill, order_id)
                    get_slippage_analyzer().log_execution(ticker, price, fill, quantity)
                except Exception:
                    pass

        elif action == "SELL":
            full_qty = positions[ticker]["quantity"]
            trade_result = broker.place_order(ticker, "SELL", full_qty, price)
            if trade_result.get("status") in ("filled", "submitted"):
                from core import entry_tracker
                entry_tracker.remove_entry(ticker)

        portfolio_summary = {
            "cash":        broker.get_cash(),
            "positions":   broker.get_positions(),
            "total_value": broker.get_portfolio_value(),
        }

    except Exception as exc:
        _logger.error(f"[execute_ibkr] {ticker}: error en ejecución — {exc}")
        return {
            "execution_result": {"status": "error", "action": "HOLD",
                                 "trade": {"status": "rejected", "reason": str(exc)},
                                 "portfolio": {}},
            "portfolio": {},
            "portfolio_sim": state.get("portfolio_sim"),
        }
    finally:
        if not using_session:
            broker.disconnect()

    execution_result = {
        "status": "executed",
        "action": action,
        "trade": trade_result,
        "portfolio": portfolio_summary,
    }
    return {
        "execution_result": execution_result,
        "portfolio": portfolio_summary,
        "portfolio_sim": state.get("portfolio_sim"),
    }


# ---------------------------------------------------------------------------
# Construcción del grafo
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    graph = StateGraph(TradingState)

    # Registrar nodos
    graph.add_node("data_node", data_node)
    graph.add_node("indicators_node", indicators_node)
    graph.add_node("technical_node", technical_node)
    graph.add_node("sentiment_node", sentiment_node)
    graph.add_node("critic_node", critic_node)
    graph.add_node("decision_node", decision_node)
    graph.add_node("execution_node", execution_node)

    # Edges: flujo principal
    graph.set_entry_point("data_node")
    graph.add_edge("data_node", "indicators_node")

    # Fan-out: técnico y sentimiento en paralelo
    graph.add_edge("indicators_node", "technical_node")
    graph.add_edge("indicators_node", "sentiment_node")

    # Fan-in al critic
    graph.add_edge("technical_node", "critic_node")
    graph.add_edge("sentiment_node", "critic_node")

    graph.add_edge("critic_node", "decision_node")
    graph.add_edge("decision_node", "execution_node")
    graph.add_edge("execution_node", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Entrypoint de prueba
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = build_graph()

    initial_state: TradingState = {
        "ticker": "NVDA",
        "df": None,
        "technical_result": None,
        "sentiment_result": None,
        "critic_result": None,
        "decision": None,
        "execution_result": None,
        "portfolio": None,
        "portfolio_sim": None,
        "regime_adjustment": None,
        "intraday_context": None,
    }

    print("=== Ejecutando TradingGraph ===")
    result = app.invoke(initial_state)

    print(f"\nTicker:     {result['ticker']}")
    print(f"Technical:  {result['technical_result']}")
    print(f"Sentiment:  {result['sentiment_result']}")
    critic = result['critic_result']
    print(f"\nCritic verdict: {critic.get('verdict')}")
    print(f"Critic reasoning:\n{critic.get('reasoning')}")
    print(f"\nDecision:   {result['decision']}")
    print(f"Execution:  {result['execution_result']}")
