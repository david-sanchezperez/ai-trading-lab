"""
Vista de trailing stops y posiciones abiertas.
Muestra: días en posición, stop actual, TP1/TP2, peak price, PnL.
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core.config import DATA_DIR
from core import portfolio_sim as sim
from core import entry_tracker


def _current_price(ticker: str) -> float | None:
    csv = DATA_DIR / f"{ticker}.csv"
    if not csv.exists():
        return None
    try:
        df = pd.read_csv(csv)
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


def render():
    st.title("Trailing Stops & Posiciones")

    portfolio_state = sim.load()
    positions = portfolio_state.get("positions", {})

    if not positions:
        st.info("No hay posiciones abiertas.")
        return

    entries = entry_tracker.load()
    today = date.today()

    rows = []
    for ticker, pos in sorted(positions.items()):
        price = _current_price(ticker)
        if price is None:
            price = pos["avg_price"]

        entry = entries.get(ticker, {})
        avg_entry = entry.get("entry_price", pos["avg_price"])
        atr       = entry.get("atr_entry", avg_entry * 0.02)
        peak      = entry.get("peak_price", price)
        tp1_done  = entry.get("tp1_triggered", False)
        qty       = pos["quantity"]

        try:
            ed = datetime.strptime(entry.get("entry_date", str(today)), "%Y-%m-%d").date()
            days_held = (today - ed).days
        except ValueError:
            days_held = 0

        stop = entry_tracker.get_stop_level(entry, today) if entry else avg_entry * 0.95
        tp1  = entry_tracker.get_tp1_level(entry) if entry else avg_entry * 1.04
        tp2  = entry_tracker.get_tp2_level(entry) if entry else avg_entry * 1.06

        stop_pct   = (price - stop) / price * 100
        tp1_pct    = (tp1 - price) / price * 100
        tp2_pct    = (tp2 - price) / price * 100
        pnl_pct    = (price - avg_entry) / avg_entry * 100
        market_val = round(price * qty, 2)

        stop_type = (
            "Fijo 2×ATR" if days_held <= 5 else
            "Trailing 1.5×ATR" if days_held <= 10 else
            "Trailing 1×ATR"
        )

        rows.append({
            "Ticker":       ticker,
            "Qty":          qty,
            "Entrada":      round(avg_entry, 2),
            "Actual":       round(price, 2),
            "PnL%":         round(pnl_pct, 1),
            "Días":         days_held,
            "Peak":         round(peak, 2),
            "Stop":         round(stop, 2),
            "Stop margen%": round(stop_pct, 1),
            "Stop tipo":    stop_type,
            "TP1":          round(tp1, 2),
            "TP1 dist%":    round(tp1_pct, 1),
            "TP1 done":     tp1_done,
            "TP2":          round(tp2, 2),
            "TP2 dist%":    round(tp2_pct, 1),
            "Valor €":      market_val,
        })

    df = pd.DataFrame(rows)

    # ── Métricas resumen ─────────────────────────────────────────────────────
    total_market = df["Valor €"].sum()
    cash = portfolio_state["cash"]
    portfolio_value = total_market + cash
    pnl_total = portfolio_value - 100_000
    pnl_pct_total = pnl_total / 100_000 * 100

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio total", f"${portfolio_value:,.0f}",
              delta=f"{pnl_pct_total:+.2f}%")
    c2.metric("Cash disponible", f"${cash:,.0f}",
              delta=f"{cash/portfolio_value*100:.0f}% del portfolio")
    c3.metric("Posiciones abiertas", len(positions))
    c4.metric("En stops activos", len(df[df["Stop margen%"] < 5]))

    st.divider()

    # ── Tabla principal ──────────────────────────────────────────────────────
    st.subheader("Posiciones y niveles de stop")

    def color_pnl(val):
        if isinstance(val, (int, float)):
            return "color: #2ecc71" if val > 0 else ("color: #e74c3c" if val < 0 else "")
        return ""

    def color_stop_margin(val):
        if isinstance(val, (int, float)):
            if val < 3:
                return "background-color: #5c1a1a"
            elif val < 7:
                return "background-color: #5c3a1a"
        return ""

    display_cols = ["Ticker", "Qty", "Entrada", "Actual", "PnL%",
                    "Días", "Peak", "Stop", "Stop margen%", "Stop tipo",
                    "TP1", "TP1 dist%", "TP1 done", "TP2", "TP2 dist%"]

    styled = (
        df[display_cols]
        .style
        .map(color_pnl, subset=["PnL%", "TP1 dist%", "TP2 dist%"])
        .map(color_stop_margin, subset=["Stop margen%"])
        .format({
            "Entrada": "${:.2f}", "Actual": "${:.2f}",
            "Peak": "${:.2f}", "Stop": "${:.2f}",
            "TP1": "${:.2f}", "TP2": "${:.2f}",
            "PnL%": "{:+.1f}%", "Stop margen%": "{:.1f}%",
            "TP1 dist%": "{:+.1f}%", "TP2 dist%": "{:+.1f}%",
        })
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── Alertas: stops muy ajustados ─────────────────────────────────────────
    at_risk = df[df["Stop margen%"] < 5]
    if not at_risk.empty:
        st.warning(
            f"⚠️ {len(at_risk)} posición(es) con stop a menos del 5% del precio actual: "
            + ", ".join(at_risk["Ticker"].tolist())
        )

    # ── TP1s ya ejecutados ───────────────────────────────────────────────────
    tp1_done_list = df[df["TP1 done"] == True]["Ticker"].tolist()
    if tp1_done_list:
        st.info(f"🎯 TP1 ya ejecutado en: {', '.join(tp1_done_list)} — esperando TP2")

    st.caption(f"Precios desde CSV local · Actualizado: {today}")
