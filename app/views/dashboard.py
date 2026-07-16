import json
import os
import subprocess
import streamlit as st
import pandas as pd
from core.data_loader import TICKERS

SIGNALS_PATH = "logs/daily_signals.json"

THESIS_LABELS = {
    "silicon":    "⚡ Silicon & Semiconductors",
    "infra_ai":   "🏗️ AI Infrastructure",
    "platforms":  "🌐 Platforms & Software",
    "biotech_ai": "🧬 Biotech & AI Health",
    "stabilizer": "⚖️ Stabilizers",
}


def color_decision(val):
    if val == "BUY":
        return "background-color: #1a4a2e; color: #2ecc71"
    elif val == "SELL":
        return "background-color: #4a1a1a; color: #e74c3c"
    return ""


def render():
    st.title("🗂️ Dashboard — Señales del día")

    col_title, col_btn = st.columns([8, 2])
    with col_btn:
        if st.button("🔄 Actualizar señales", use_container_width=True):
            subprocess.Popen(
                ["python", "-m", "scripts.analyze_all"],
                cwd=os.getcwd(),
            )
            st.info("Análisis lanzado en background. Refresca en ~70 min.")

    if not os.path.isfile(SIGNALS_PATH):
        st.warning("No hay señales disponibles. Ejecuta `python -m scripts.analyze_all` primero.")
        return

    with open(SIGNALS_PATH, "r") as f:
        data = json.load(f)

    date = data.get("date", "—")
    signals = data.get("signals", [])
    summary = data.get("summary", {})
    regime_adjustment = data.get("regime_adjustment")
    effective_threshold = data.get("effective_threshold")

    st.caption(f"Fecha: {date} · {len(signals)} tickers analizados")

    # ── Métricas resumen ─────────────────────────────────────────────────────
    buy_list  = summary.get("BUY", [])
    sell_list = summary.get("SELL", [])
    hold_list = summary.get("HOLD", [])

    cols = st.columns(4 if regime_adjustment is not None else 3)
    cols[0].metric("🟢 BUY",  len(buy_list),  delta=", ".join(buy_list)  or "—")
    cols[1].metric("🔴 SELL", len(sell_list), delta=", ".join(sell_list) or "—")
    cols[2].metric("⚪ HOLD", len(hold_list))
    if regime_adjustment is not None:
        regime_label = "stress" if regime_adjustment > 1.05 else ("tranquilo" if regime_adjustment < 0.95 else "neutral")
        cols[3].metric(
            "📊 Régimen macro",
            f"{regime_adjustment:.3f}x",
            delta=f"threshold {effective_threshold:.3f}" if effective_threshold else None,
            help=f"Multiplicador continuo sobre umbral base 0.7. Valor actual: {regime_label}",
        )

    st.divider()

    # Indexar señales por ticker — solo tickers con señal ese día
    signals_by_ticker = {s["ticker"]: s for s in signals}

    # ── Tabla por tesis ──────────────────────────────────────────────────────
    for thesis_key, thesis_label in THESIS_LABELS.items():
        # Tickers de esta tesis (excluye role=context automáticamente)
        tickers_in_thesis = [
            t for t, m in TICKERS.items()
            if m["thesis"] == thesis_key and m["role"] != "context"
        ]

        rows = []
        for ticker in tickers_in_thesis:
            s = signals_by_ticker.get(ticker)
            if not s:
                continue
            meta = TICKERS[ticker]
            rows.append({
                "Ticker":     ticker,
                "Role":       meta["role"],
                "Price":      f"${s.get('price', 0):,.2f}",
                "RSI":        round(s.get("rsi", 0), 1),
                "Signal":     s.get("signal", "—"),
                "Sentiment":  round(s.get("sentiment", 0), 3),
                "Critic":     s.get("critic_verdict", "—"),
                "Decision":   s.get("decision", "—"),
                "Score":      round(s.get("score", 0), 3),
                "Confidence": round(s.get("confidence", 0), 2),
            })

        if not rows:
            continue

        st.subheader(thesis_label)
        df = pd.DataFrame(rows)
        styled = df.style.map(color_decision, subset=["Decision"])
        st.dataframe(styled, use_container_width=True, hide_index=True)
