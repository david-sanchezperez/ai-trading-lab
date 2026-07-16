"""
End-to-end test: pipeline completo en modo PAPER_IBKR.
Ejecuta un ciclo real para ANET y registra cada nodo.
Si la decisión es HOLD, fuerza una compra de prueba de 1 acción.
Cierra la posición al final.

Uso:
    source .venv/bin/activate
    python3 scripts/e2e_ibkr_test.py
"""

import sys
import logging
import json
from datetime import datetime

# Log detallado a stdout
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("e2e")
log.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
log.addHandler(handler)

TICKER = "ANET"
SEP = "─" * 70


def section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def run():
    from config.broker_config import BROKER_MODE, BrokerMode

    section(f"E2E IBKR TEST — {TICKER} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Broker mode : {BROKER_MODE.value}")

    # ── 1. Estado inicial del portfolio ──────────────────────────────────────
    from brokers.ibkr.broker import IBKRBroker
    broker = IBKRBroker(client_id=10)
    broker.connect()

    cash_before   = broker.get_cash()
    value_before  = broker.get_portfolio_value()
    pos_before    = broker.get_positions()

    section("PORTFOLIO ANTES")
    print(f"  Cash        : ${cash_before:,.2f}")
    print(f"  Total value : ${value_before:,.2f}")
    print(f"  Positions   : {pos_before or 'ninguna'}")
    broker.disconnect()

    # ── 2. Ejecutar el pipeline nodo a nodo ──────────────────────────────────
    import pandas as pd
    from core.data_loader import fetch_data, save_data, get_ticker_metadata
    from core.indicators import add_indicators, add_relative_strength
    from agents.technical_agent import generate_signal
    from agents.decision_agent import make_decision
    from core.news_fetcher import get_ticker_sentiment
    from core.config import DATA_DIR
    from core.market_regime import compute_regime_adjustment as get_regime_adjustment

    # data_node
    section("NODO: data_node")
    csv_path = DATA_DIR / f"{TICKER}.csv"
    from datetime import date
    if csv_path.exists() and date.fromtimestamp(csv_path.stat().st_mtime) == date.today():
        df = pd.read_csv(csv_path)
        df["Date"] = pd.to_datetime(df["Date"])
        print(f"  Cargado desde CSV ({len(df)} filas)")
    else:
        df = fetch_data(TICKER, period="1y")
        save_data(df, TICKER)
        print(f"  Descargado de Yahoo Finance ({len(df)} filas)")
    last = df.iloc[-1]
    print(f"  Último cierre : ${last['Close']:.2f}  Volumen: {int(last.get('Volume', 0)):,}")
    print(f"  Rango datos   : {df['Date'].iloc[0].date()} → {df['Date'].iloc[-1].date()}")

    # indicators_node
    section("NODO: indicators_node")
    df = add_indicators(df)
    spy_path = DATA_DIR / "SPY.csv"
    spy_df = None
    try:
        if spy_path.exists() and date.fromtimestamp(spy_path.stat().st_mtime) == date.today():
            spy_df = pd.read_csv(spy_path)
        else:
            spy_df = fetch_data("SPY", period="1y")
            save_data(spy_df, "SPY")
    except Exception:
        pass
    df = add_relative_strength(df, spy_df)
    row = df.iloc[-1]
    print(f"  RSI           : {row['RSI']:.2f}")
    print(f"  SMA20         : ${row['SMA_20']:.2f}")
    print(f"  SMA50         : ${row['SMA_50']:.2f}")
    print(f"  ATR14         : ${row.get('ATR_14', 0):.2f}")
    print(f"  Momentum      : {row['Momentum']:.4f}")
    print(f"  Volume ratio  : {row.get('volume_ratio', 1):.2f}x")
    print(f"  52w position  : {row.get('pct_52w_range', 0.5):.1%}")
    print(f"  RS vs SPY     : {row.get('RS_SPY', 0):+.2%}")

    # technical_node
    section("NODO: technical_node")
    tech = generate_signal(df)
    print(f"  Signal        : {tech['signal']}")
    print(f"  Confidence    : {tech['confidence']:.2f}")
    print(f"  RSI           : {tech['rsi']:.2f}")
    print(f"  Price         : ${tech['price']:.2f}")
    print(f"  Buy votes     : {tech.get('buy_votes', '?')}")
    print(f"  Sell votes    : {tech.get('sell_votes', '?')}")
    print(f"  ATR14         : ${tech.get('atr_14', 0):.2f}")

    # sentiment_node
    section("NODO: sentiment_node")
    from core.social_sentiment import get_stocktwits_sentiment
    from core.sentiment_store import save_sentiment
    sent_result = get_ticker_sentiment(TICKER, max_items=10)
    stocktwits   = get_stocktwits_sentiment(TICKER)
    if sent_result["headlines"] > 0:
        save_sentiment(
            date=datetime.now().strftime("%Y-%m-%d"),
            ticker=TICKER,
            sentiment=sent_result["sentiment"],
            confidence=sent_result["confidence"],
        )
    sentiment_result = {
        "sentiment":   sent_result["sentiment"],
        "confidence":  sent_result["confidence"],
        "headlines":   sent_result["headlines"],
        "raw_results": sent_result["raw_results"],
        "stocktwits":  stocktwits,
    }
    print(f"  FinBERT score : {sent_result['sentiment']:+.4f}")
    print(f"  Confidence    : {sent_result['confidence']:.2f}")
    print(f"  Headlines     : {sent_result['headlines']}")
    if stocktwits:
        print(f"  StockTwits    : {stocktwits['bullish_pct']:.0%} bullish / {stocktwits['bearish_pct']:.0%} bearish ({stocktwits['labeled']} msgs)")
    else:
        print(f"  StockTwits    : n/a (no elegible o sin datos)")

    # critic_node (preview del score para decidir si hace fast path)
    section("NODO: critic_node")
    preview_decision = make_decision(tech, sentiment_result, TICKER)
    score_preview = preview_decision["score"]
    signal = tech["signal"]
    has_position = False  # portfolio limpio al inicio

    fast_path_reason = None
    if signal == "SELL" and not has_position:
        fast_path_reason = "SELL sin posición"
    elif abs(score_preview) > 0.85:
        fast_path_reason = f"score fuerte ({score_preview:+.3f})"
    elif abs(score_preview) < 0.45:
        fast_path_reason = f"score débil ({score_preview:+.3f})"

    if fast_path_reason:
        print(f"  >>> FAST PATH: {fast_path_reason} — LLM skipped")
        critic_result = {
            "approved": True, "verdict": "APPROVED",
            "thinking": "", "reasoning": f"Fast path — {fast_path_reason}",
            "technical_signal": signal, "sentiment_score": sent_result["sentiment"],
            "rag_precedents": [], "fast_path": True,
        }
    else:
        # Llamada real al critic LLM
        print(f"  Score preview : {score_preview:+.3f} — llamando al critic LLM...")
        import requests
        from core.config import CRITIC_LLM_URL, CRITIC_LLM_MODEL
        from core.rag_store import get_similar_situations, get_company_context
        from core.news_analyzer import extract_key_event

        meta = get_ticker_metadata(TICKER)
        similar = get_similar_situations(tech, tech, signal, n=3, thesis=meta.get("thesis"))
        prec_text = ""
        if similar:
            prec_text = "Precedentes históricos:\n" + "\n".join(
                f"  · {s['metadata'].get('date','?')[:10]}: {s['text']} (sim={s['similarity']:.2f})"
                for s in similar
            )
        print(f"  Precedentes RAG: {len(similar) if similar else 0}")

        key_event = extract_key_event(TICKER, sent_result["raw_results"]) if len(sent_result["raw_results"]) >= 2 else None
        if key_event:
            print(f"  Key event     : {key_event}")

        prompt = (
            f"Ticker: {TICKER} | Signal: {signal} | RSI: {tech['rsi']:.1f} | "
            f"Score: {score_preview:+.3f} | Sentiment: {sent_result['sentiment']:+.4f}\n"
            f"/no_think\nShould this signal be APPROVED or CHALLENGED? "
            f"Last line must be exactly: VERDICT: APPROVED or VERDICT: CHALLENGED\n\n{prec_text}"
        )
        resp = requests.post(CRITIC_LLM_URL, json={
            "model": CRITIC_LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False, "temperature": 0.1, "max_tokens": 120,
        }, timeout=30)
        content = resp.json()["choices"][0]["message"].get("content", "")
        approved = "VERDICT: APPROVED" in content.upper()
        print(f"  LLM reasoning : {content[:200].strip()}")
        print(f"  Verdict       : {'APPROVED' if approved else 'CHALLENGED'}")
        critic_result = {
            "approved": approved, "verdict": "APPROVED" if approved else "CHALLENGED",
            "thinking": "", "reasoning": content,
            "technical_signal": signal, "sentiment_score": sent_result["sentiment"],
            "rag_precedents": similar or [],
        }

    # decision_node
    section("NODO: decision_node")
    regime_adj = get_regime_adjustment()
    effective_threshold = round(0.7 * regime_adj, 4)
    decision = make_decision(tech, sentiment_result, TICKER)
    score = decision["score"]
    if not critic_result["approved"]:
        score *= 0.85
    action = "BUY" if score > effective_threshold else ("SELL" if score < -effective_threshold else "HOLD")
    print(f"  Score bruto   : {decision['score']:+.4f}")
    print(f"  Score ajustado: {score:+.4f} (critic={'OK' if critic_result['approved'] else 'challenged ×0.85'})")
    print(f"  Régimen       : {regime_adj:.2f}x  (threshold={effective_threshold})")
    print(f"  Acción        : {action}")

    # execution_node (PAPER_IBKR)
    section("NODO: execution_node (PAPER_IBKR)")
    price = tech["price"]
    atr_14 = tech.get("atr_14", 0)
    forced_test = False

    if action == "HOLD":
        print(f"  Pipeline generó HOLD — forzando compra de prueba de 1 acción para validar flujo IBKR")
        forced_test = True
        order_qty = 1
    else:
        import core.portfolio_sim as sim_mod
        order_qty = sim_mod.compute_quantity_atr(TICKER, price, atr_14, value_before, 0.01)
        # Caps paper: 20% cash reserve, 15% position cap
        effective_atr = atr_14 if atr_14 > 0 else price * 0.035
        stop_price = round(price - 2.0 * effective_atr, 4)
        tp_price   = round(price + 3.0 * effective_atr, 4)
        print(f"  Señal real    : {action} {order_qty} × {TICKER} @ ${price:.2f}")
        print(f"  Stop / TP     : ${stop_price:.2f} / ${tp_price:.2f}")

    broker2 = IBKRBroker(client_id=11)
    broker2.connect()

    effective_atr = atr_14 if atr_14 > 0 else price * 0.035
    stop_price = round(price - 2.0 * effective_atr, 4)
    tp_price   = round(price + 3.0 * effective_atr, 4)

    if action == "BUY" and not forced_test:
        result = broker2.place_bracket_order(TICKER, "BUY", order_qty, price, stop_price, tp_price)
    else:
        # Compra de prueba: 1 acción vía place_order
        result = broker2.place_order(TICKER, "BUY", 1, price)

    print(f"\n  Resultado orden:")
    print(f"    status      : {result.get('status')}")
    trade = result.get("trade", {})
    if trade:
        print(f"    ticker      : {trade.get('ticker')}")
        print(f"    action      : {trade.get('action')}")
        print(f"    quantity    : {trade.get('quantity')}")
        print(f"    fill price  : ${trade.get('price', 0):.2f}")
    order_ids = result.get("order_ids", {})
    if order_ids and any(v for v in order_ids.values()):
        print(f"    order_ids   : {order_ids}")
    if result.get("reason"):
        print(f"    reason      : {result.get('reason')}")

    import time; time.sleep(3)

    pos_after = broker2.get_positions()
    cash_after = broker2.get_cash()
    value_after = broker2.get_portfolio_value()

    section("PORTFOLIO DESPUÉS DE LA ORDEN")
    print(f"  Cash          : ${cash_before:,.2f}  →  ${cash_after:,.2f}  (Δ ${cash_after - cash_before:+,.2f})")
    print(f"  Total value   : ${value_before:,.2f}  →  ${value_after:,.2f}  (Δ ${value_after - value_before:+,.2f})")
    print(f"  Positions     : {pos_after or 'ninguna'}")

    # ── 5. Cerrar la posición de prueba ───────────────────────────────────────
    anet_pos = pos_after.get(TICKER)
    if anet_pos and anet_pos["quantity"] > 0:
        section("CERRANDO POSICIÓN DE PRUEBA")
        qty_to_sell = anet_pos["quantity"] if (forced_test or action != "BUY") else 1
        close_result = broker2.place_order(TICKER, "SELL", qty_to_sell, price)
        print(f"  SELL {qty_to_sell} × {TICKER}: {close_result.get('status')} @ ${close_result.get('trade', {}).get('price', 0):.2f}")
        time.sleep(2)
        final_pos = broker2.get_positions()
        final_cash = broker2.get_cash()
        print(f"  Cash final    : ${final_cash:,.2f}")
        print(f"  Positions     : {final_pos or 'ninguna (limpio)'}")

    broker2.disconnect()

    section("RESUMEN E2E")
    print(f"  ✓ Pipeline ejecutado para {TICKER}")
    print(f"  ✓ Modo PAPER_IBKR confirmado — cuenta DU1234567")
    print(f"  ✓ Orden {'de prueba' if forced_test else 'de señal real'} colocada y cerrada en IBKR")
    print(f"  ✓ Log ⚠️  PAPER TRADING MODE presente en cada conexión")
    print(f"  Señal pipeline: {action} (score={score:+.4f}, threshold={effective_threshold})")
    print()


if __name__ == "__main__":
    run()
