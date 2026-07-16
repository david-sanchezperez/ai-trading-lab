"""
Ejecuta el grafo completo para todos los tickers en TICKERS_FLAT
y guarda un resumen en DAILY_SIGNALS_PATH (ver core/config.py).
El portfolio simulado se carga al inicio, viaja entre tickers,
y se persiste al final del ciclo completo.
"""

import json
from datetime import datetime

import core.portfolio_sim as sim
from core.config import LOGS_DIR, DAILY_SIGNALS_PATH, PNL_HISTORY_PATH
from core.data_loader import TICKERS_FLAT
from core.market_regime import compute_regime_adjustment
from graph.trading_graph import build_graph, TradingState


def analyze_ticker(
    app_graph,
    ticker: str,
    portfolio_state: dict,
    regime_adjustment: float,
    intraday_context: dict | None = None,
) -> tuple[dict, dict]:
    """
    Ejecuta el grafo para un ticker.
    Recibe el estado del portfolio y el multiplicador de régimen macro.
    Devuelve (result, portfolio_state_updated).
    """
    initial_state: TradingState = {
        "ticker": ticker,
        "df": None,
        "technical_result": None,
        "sentiment_result": None,
        "critic_result": None,
        "decision": None,
        "execution_result": None,
        "portfolio": None,
        "portfolio_sim": portfolio_state,
        "regime_adjustment": regime_adjustment,
        "intraday_context": intraday_context or {},
    }
    result = app_graph.invoke(initial_state)
    updated_portfolio = result.get("portfolio_sim") or portfolio_state
    return result, updated_portfolio


def main(intraday_context: dict | None = None):
    from config.broker_config import BROKER_MODE, BrokerMode
    from graph.trading_graph import set_session_broker, clear_session_broker
    from core.session_logger import SessionLogger, set_session_logger

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    app_graph = build_graph()
    date_str = datetime.now().strftime("%Y-%m-%d")
    session_id = datetime.now().strftime("%H%M%S")
    set_session_logger(SessionLogger(session_id))

    # ── Session broker: una única conexión IBKR para todo el ciclo ──────────
    # Evita los 20 ciclos connect/disconnect (uno por ticker) que causaban
    # conflictos de client_id y fallos intermitentes de ejecución.
    if BROKER_MODE != BrokerMode.PAPER_LOCAL:
        import asyncio
        import nest_asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        nest_asyncio.apply()

        from brokers import get_broker
        session_broker = get_broker()
        if session_broker.connect():
            set_session_broker(session_broker)
            print("Session broker IBKR conectado ✓")
        else:
            print("⚠️ Session broker no pudo conectar — se usará conexión por ticker como fallback")

    # Computar régimen macro UNA vez para todo el ciclo
    regime_adjustment = compute_regime_adjustment()
    print(f"Regime adjustment: {regime_adjustment:.3f}x "
          f"(effective threshold: {0.7 * regime_adjustment:.3f})")

    # Cargar portfolio simulado al inicio del ciclo
    portfolio_state = sim.load()
    print(f"Portfolio loaded — cash: {portfolio_state['cash']:.2f}€ | "
          f"positions: {len(portfolio_state['positions'])}")

    results = []
    summary_signals = {"BUY": [], "SELL": [], "HOLD": []}
    errors = []

    for ticker in TICKERS_FLAT:
        print(f"Analyzing {ticker}...", end=" ", flush=True)
        try:
            result, portfolio_state = analyze_ticker(
                app_graph, ticker, portfolio_state, regime_adjustment,
                intraday_context=intraday_context,
            )

            tech = result.get("technical_result") or {}
            sent = result.get("sentiment_result") or {}
            critic = result.get("critic_result") or {}
            decision = result.get("decision") or {}
            execution = result.get("execution_result") or {}
            action = decision.get("action", "HOLD")

            entry = {
                "date": date_str,
                "ticker": ticker,
                "signal": tech.get("signal"),
                "rsi": round(tech.get("rsi", 0), 2),
                "price": round(tech.get("price", 0), 2),
                "sentiment": sent.get("sentiment", 0),
                "sentiment_headlines": sent.get("headlines", 0),
                "critic_verdict": critic.get("verdict"),
                "critic_override": decision.get("critic_override", False),
                "decision": action,
                "score": decision.get("score", 0),
                "confidence": decision.get("confidence", 0),
                "pead": decision.get("pead", 0),
                "insider": decision.get("insider", 0),
                "trade": execution.get("trade"),
            }
            results.append(entry)
            summary_signals[action].append(ticker)

            trade_info = ""
            trade = execution.get("trade")
            if trade and trade.get("status") in ("filled", "submitted"):
                t = trade.get("trade") or {}
                act = t.get("action", "")
                qty = t.get("quantity", "?")
                px  = t.get("price", 0)
                trade_info = f" → {act} {qty}x@${px:.2f}" if px else f" → {trade.get('status')}"
            print(f"{action} (score: {entry['score']:+.3f}){trade_info}")

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append({"ticker": ticker, "error": str(e)})

    market_prices = {r["ticker"]: r["price"] for r in results if r.get("price")}
    trades_today = sum(
        1 for r in results
        if r.get("trade") and r["trade"]
        and r["trade"].get("status") in ("filled", "submitted")
    )

    # En PAPER_LOCAL: portfolio_sim es la fuente de verdad → persiste y escribe pnl_history.
    # En PAPER_IBKR / LIVE: IBKR es la fuente de verdad → daily_run.py escribe pnl_history
    # después de leer el portfolio real de IBKR.
    portfolio_summary: dict
    if BROKER_MODE == BrokerMode.PAPER_LOCAL:
        sim.save(portfolio_state)
        portfolio_summary = sim.summary(portfolio_state, market_prices)
        print(f"\nPortfolio saved — cash: {portfolio_state['cash']:.2f}€ | "
              f"value: {portfolio_summary['total_value']:.2f}€ | "
              f"trades today: {trades_today}")

        pnl_entry = {
            "date": date_str,
            "total_value": portfolio_summary["total_value"],
            "cash": portfolio_summary["cash"],
            "pnl_total": portfolio_summary["pnl_total"],
            "pnl_total_pct": portfolio_summary["pnl_total_pct"],
            "total_trades": portfolio_summary["total_trades"],
            "regime_adjustment": regime_adjustment,
        }
        if PNL_HISTORY_PATH.exists():
            with open(PNL_HISTORY_PATH, "r") as f:
                history = json.load(f)
        else:
            history = []
        history = [e for e in history if e["date"] != date_str]
        history.append(pnl_entry)
        with open(PNL_HISTORY_PATH, "w") as f:
            json.dump(history, f, indent=2)
        print(f"PnL history → {PNL_HISTORY_PATH} ({len(history)} entries)")
    else:
        # Portfolio_sim no se persiste en modo IBKR — IBKR es la fuente de verdad.
        # daily_run.py leerá el portfolio de IBKR y escribirá pnl_history.
        portfolio_summary = sim.summary(portfolio_state, market_prices)
        print(f"\nTrades today: {trades_today} (portfolio_sim no persistido en modo IBKR)")

    # Persistir señales del día — daily_run.py lo lee para Telegram + daily report
    daily_signals_data = {
        "date":              date_str,
        "tickers_analyzed":  len(results),
        "regime_adjustment": regime_adjustment,
        "summary":           summary_signals,
        "signals":           results,
        "errors":            errors,
        "portfolio_snapshot": portfolio_summary,
    }
    with open(DAILY_SIGNALS_PATH, "w") as f:
        json.dump(daily_signals_data, f, indent=2, default=str)

    print(f"\n{'='*45}")
    print(f"Date: {date_str}")
    print(f"Analyzed: {len(results)} tickers | Errors: {len(errors)}")
    print(f"BUY  ({len(summary_signals['BUY'])}):  {', '.join(summary_signals['BUY']) or '—'}")
    print(f"SELL ({len(summary_signals['SELL'])}): {', '.join(summary_signals['SELL']) or '—'}")
    print(f"HOLD ({len(summary_signals['HOLD'])}): {len(summary_signals['HOLD'])} tickers")
    print(f"Saved → {DAILY_SIGNALS_PATH}")

    # Cerrar session broker y session logger al final del ciclo
    if BROKER_MODE != BrokerMode.PAPER_LOCAL:
        clear_session_broker()
        print("Session broker IBKR desconectado ✓")
    set_session_logger(None)


if __name__ == "__main__":
    main()
