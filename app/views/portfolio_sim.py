import io
import json
import streamlit as st
import pandas as pd
import yfinance as yf
from core.config import PNL_HISTORY_PATH, LOGS_DIR
from config.broker_config import IBKR_INITIAL_CAPITAL
from core.fiscal import get_resumen_fiscal, add_operacion, load_fiscal

DAILY_REPORTS_DIR = LOGS_DIR / "daily_reports"


def _get_market_prices(tickers: list[str]) -> dict:
    prices = {}
    for ticker in tickers:
        try:
            prices[ticker] = float(yf.Ticker(ticker).fast_info["last_price"])
        except Exception:
            prices[ticker] = 0.0
    return prices


def _load_latest_report() -> dict | None:
    reports = sorted(DAILY_REPORTS_DIR.glob("*.json"))
    if not reports:
        return None
    return json.loads(reports[-1].read_text())


def _load_all_orders() -> list[dict]:
    """Agrega orders_executed de todos los daily_reports, más reciente primero."""
    orders = []
    for f in sorted(DAILY_REPORTS_DIR.glob("*.json"), reverse=True):
        try:
            r = json.loads(f.read_text())
            for o in r.get("orders_executed", []):
                t = (o.get("trade") or {}).get("trade") or {}
                if not t:
                    continue
                orders.append({
                    "Fecha":   r["date"],
                    "Ticker":  t.get("ticker", ""),
                    "Acción":  t.get("action", ""),
                    "Qty":     t.get("quantity", 0),
                    "Precio":  t.get("price", 0.0),
                    "Total":   round(t.get("quantity", 0) * t.get("price", 0.0), 2),
                    "Score":   o.get("score", 0.0),
                })
        except Exception:
            continue
    return orders


def render():
    st.title("🤖 Portfolio IBKR Paper")

    tab_ibkr, tab_fiscal = st.tabs(["📊 IBKR Paper", "📋 Libro Fiscal"])

    # ══════════════════════════════════════════════════════════════
    # TAB 1 — IBKR Paper
    # ══════════════════════════════════════════════════════════════
    with tab_ibkr:
        report = _load_latest_report()

        if report is None:
            st.info("No hay datos de ciclo diario todavía. El scheduler escribe el primer report a las 20:30.")
            return

        report_date  = report["date"]
        broker_mode  = report.get("broker_mode", "—")
        positions_raw = report.get("positions_open", [])
        ps           = report.get("portfolio_summary", {})
        total_value  = ps.get("total_value", 0.0)
        cash         = ps.get("cash", 0.0)
        pnl_total     = round(total_value - IBKR_INITIAL_CAPITAL, 2)
        pnl_total_pct = round(pnl_total / IBKR_INITIAL_CAPITAL * 100, 2) if IBKR_INITIAL_CAPITAL else 0.0

        st.caption(f"Datos del ciclo: **{report_date}** · modo `{broker_mode}`")

        # ── Métricas principales ──────────────────────────────────
        c1, c2, c3 = st.columns(3)
        c1.metric("Capital total",      f"${total_value:,.2f}")
        c2.metric("Cash disponible",    f"${cash:,.2f}")
        c3.metric("Posiciones abiertas", len(positions_raw))

        c4, c5, c6 = st.columns(3)
        c4.metric("PnL total",       f"${pnl_total:+,.2f}",    delta=f"${pnl_total:+,.2f}")
        c5.metric("PnL %",           f"{pnl_total_pct:+.2f}%", delta=f"{pnl_total_pct:+.2f}%")
        c6.metric("Capital base IBKR", f"${IBKR_INITIAL_CAPITAL:,.0f}")

        st.divider()

        # ── Equity Curve ──────────────────────────────────────────
        try:
            history = json.loads(PNL_HISTORY_PATH.read_text())
            if len(history) >= 2:
                st.subheader("📈 Equity Curve (IBKR)")
                df_hist = pd.DataFrame(history).set_index("date")
                col1, col2 = st.columns(2)
                with col1:
                    st.caption("Valor total portfolio ($)")
                    st.line_chart(df_hist[["total_value"]])
                with col2:
                    st.caption("PnL % acumulado vs base $250k")
                    st.line_chart(df_hist[["pnl_total_pct"]])
                st.divider()
        except Exception:
            pass

        # ── Posiciones abiertas ───────────────────────────────────
        if positions_raw:
            st.subheader("📂 Posiciones abiertas")
            open_tickers = [p["ticker"] for p in positions_raw]
            market_prices = _get_market_prices(open_tickers)

            rows = []
            for p in positions_raw:
                ticker    = p["ticker"]
                qty       = p.get("quantity", 0)
                avg_price = p.get("avg_price", 0.0)
                cur_price = market_prices.get(ticker, avg_price)
                mkt_value = round(qty * cur_price, 2)
                cost      = round(qty * avg_price, 2)
                pnl       = round(mkt_value - cost, 2)
                # abs(cost): en posiciones cortas 'cost' es negativo, y dividir
                # sin abs() invierte el signo del % (una ganancia se vería como pérdida)
                pnl_pct   = round(pnl / abs(cost) * 100, 2) if cost else 0.0
                rows.append({
                    "Ticker":        ticker,
                    "Qty":           qty,
                    "Avg Price":     avg_price,
                    "Current Price": cur_price,
                    "Market Value":  mkt_value,
                    "PnL $":         pnl,
                    "PnL %":         pnl_pct,
                })

            df_pos = pd.DataFrame(rows)

            def _color_pnl(val):
                if not isinstance(val, (int, float)):
                    return ""
                return "color: #2ecc71" if val >= 0 else "color: #e74c3c"

            styled_pos = (
                df_pos.style
                .map(_color_pnl, subset=["PnL $", "PnL %"])
                .format({
                    "Avg Price":     "${:.2f}",
                    "Current Price": "${:.2f}",
                    "Market Value":  "${:,.2f}",
                    "PnL $":         "${:+,.2f}",
                    "PnL %":         "{:+.2f}%",
                })
            )
            st.dataframe(styled_pos, use_container_width=True, hide_index=True)
        else:
            st.info("No hay posiciones abiertas según el último ciclo.")

        st.divider()

        # ── Historial de órdenes ──────────────────────────────────
        st.subheader("📜 Historial de órdenes ejecutadas")
        all_orders = _load_all_orders()

        if not all_orders:
            st.caption("Sin órdenes registradas todavía.")
        else:
            df_orders = pd.DataFrame(all_orders)

            def _color_action(val):
                if val == "BUY":
                    return "background-color: #1a4a2e; color: #2ecc71"
                elif val == "SELL":
                    return "background-color: #4a1a1a; color: #e74c3c"
                return ""

            styled_orders = (
                df_orders.style
                .map(_color_action, subset=["Acción"])
                .format({
                    "Precio": "${:.2f}",
                    "Total":  "${:,.2f}",
                    "Score":  "{:.3f}",
                })
            )
            st.dataframe(styled_orders, use_container_width=True, hide_index=True)

        st.divider()
        st.caption(f"Fuente: IBKR Paper account · Capital base: ${IBKR_INITIAL_CAPITAL:,.0f}")

    # ══════════════════════════════════════════════════════════════
    # TAB 2 — Libro Fiscal
    # ══════════════════════════════════════════════════════════════
    with tab_fiscal:

        # ── Resumen por ticker ────────────────────────────────────
        st.subheader("📊 Resumen fiscal por ticker")
        resumen = get_resumen_fiscal()
        por_ticker = resumen.get("por_ticker", {})

        if por_ticker:
            rows = []
            for ticker, r in por_ticker.items():
                rows.append({
                    "Ticker":                ticker,
                    "Qty comprada":          r["qty_comprada"],
                    "Total comprado EUR":    r["total_comprado_eur"],
                    "Qty vendida":           r["qty_vendida"],
                    "Total vendido EUR":     r["total_vendido_eur"],
                    "Resultado realiz. EUR": r["resultado_realizado_eur"],
                })
            df_res = pd.DataFrame(rows)

            def color_resultado(val):
                if not isinstance(val, (int, float)):
                    return ""
                return "color: #2ecc71" if val >= 0 else "color: #e74c3c"

            styled_res = (
                df_res.style
                .map(color_resultado, subset=["Resultado realiz. EUR"])
                .format({
                    "Total comprado EUR":    "€{:,.2f}",
                    "Total vendido EUR":     "€{:,.2f}",
                    "Resultado realiz. EUR": "€{:+,.2f}",
                })
            )
            st.dataframe(styled_res, use_container_width=True, hide_index=True)

            total = resumen["resultado_total_eur"]
            st.metric("Resultado total realizado",
                      f"€{total:+,.2f}",
                      delta=f"€{total:+,.2f}")
        else:
            st.info("Sin operaciones fiscales registradas.")

        st.divider()

        # ── Formulario añadir operación manual ───────────────────
        with st.expander("➕ Añadir operación manual"):
            with st.form("fiscal_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    ticker     = st.text_input("Ticker", placeholder="NVDA").upper().strip()
                    cantidad   = st.number_input("Cantidad", min_value=1,
                                                step=1, value=1, format="%d")
                with c2:
                    accion     = st.selectbox("Acción", ["COMPRA", "VENTA"])
                    precio_usd = st.number_input("Precio USD", min_value=0.01,
                                                step=0.01, value=100.0)
                notas = st.text_input("Notas", placeholder="Opcional")

                submitted = st.form_submit_button("Registrar operación",
                                                  use_container_width=True)
                if submitted:
                    if not ticker:
                        st.error("Introduce un ticker válido.")
                    else:
                        op = add_operacion(ticker, accion, int(cantidad),
                                           precio_usd, origen="manual", notas=notas)
                        st.success(
                            f"✅ {accion} {cantidad} {ticker} @ ${precio_usd:.2f} "
                            f"| EUR/USD: {op['eurusd']:.4f} "
                            f"| Total: €{op['total_eur']:.2f}"
                        )
                        st.rerun()

        st.divider()

        # ── Historial completo ────────────────────────────────────
        st.subheader("📋 Historial de operaciones")
        data_fiscal = load_fiscal()
        ops = sorted(data_fiscal.get("operaciones", []),
                     key=lambda x: x["fecha"], reverse=True)

        if not ops:
            st.caption("Sin operaciones registradas.")
        else:
            rows = []
            for op in ops:
                rows.append({
                    "Fecha":      op["fecha"],
                    "Ticker":     op["ticker"],
                    "Acción":     op["accion"],
                    "Cantidad":   op["cantidad"],
                    "Precio USD": op["precio_usd"],
                    "EUR/USD":    op["eurusd"],
                    "Precio EUR": op["precio_eur"],
                    "Total EUR":  op["total_eur"],
                    "Origen":     op["origen"],
                })
            df_ops = pd.DataFrame(rows)

            def color_accion(val):
                if val == "COMPRA":
                    return "background-color: #1a4a2e; color: #2ecc71"
                elif val == "VENTA":
                    return "background-color: #4a1a1a; color: #e74c3c"
                return ""

            styled_ops = df_ops.style.map(color_accion, subset=["Acción"])
            st.dataframe(styled_ops, use_container_width=True, hide_index=True)

            # ── Exportar CSV ──────────────────────────────────────
            csv_buf = io.StringIO()
            df_ops.to_csv(csv_buf, index=False)
            last_fecha = ops[0]["fecha"] if ops else "export"
            st.download_button(
                label="⬇️ Exportar CSV fiscal",
                data=csv_buf.getvalue(),
                file_name=f"fiscal_{last_fecha}.csv",
                mime="text/csv",
                use_container_width=True,
            )
