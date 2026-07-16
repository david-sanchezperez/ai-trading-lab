"""
Alertas Telegram para eventos del sistema de trading.

Cada función formatea un tipo de alerta específico y lo envía.
El transporte HTTP reutiliza scheduler/notifier.py.
"""

from __future__ import annotations

from scheduler.notifier import send_notification


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_cash(v: float) -> str:
    return f"${v:,.0f}"


def _fmt_pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


# ── Alertas de órdenes ────────────────────────────────────────────────────────

def notify_order_executed(
    ticker: str,
    action: str,
    qty: int,
    fill_price: float,
    stop: float,
    tp1: float,
    tp2: float,
    score: float,
    regime: float,
    cash: float,
    n_positions: int,
) -> None:
    icon = "🟢" if action == "BUY" else "🔴"
    lines = [
        f"{icon} *{action} EJECUTADO*",
        f"Ticker: `{ticker}`",
        f"Qty: {qty} @ ${fill_price:.2f}",
        f"Stop: ${stop:.2f} | TP1: ${tp1:.2f} | TP2: ${tp2:.2f}",
        f"Score: {score:+.3f} | Regime: {regime:.2f}×",
        f"Portfolio: {_fmt_cash(cash)} cash | {n_positions} posición(es)",
    ]
    send_notification("\n".join(lines))


def notify_stop_hit(
    ticker: str,
    qty: int,
    sell_price: float,
    entry_price: float,
    cash: float,
    n_positions: int,
) -> None:
    pnl = (sell_price - entry_price) * qty
    pnl_pct = (sell_price - entry_price) / entry_price * 100
    lines = [
        "🔴 *STOP HIT*",
        f"Ticker: `{ticker}`",
        f"Sell: {qty} @ ${sell_price:.2f}",
        f"PnL: ${pnl:+,.0f} ({_fmt_pct(pnl_pct)})",
        f"Portfolio: {_fmt_cash(cash)} cash | {n_positions} posición(es)",
    ]
    send_notification("\n".join(lines))


def notify_tp_executed(
    ticker: str,
    tp_type: str,
    qty: int,
    fill_price: float,
    entry_price: float,
    remaining_qty: int,
    new_stop: float | None = None,
) -> None:
    pnl = (fill_price - entry_price) * qty
    pnl_pct = (fill_price - entry_price) / entry_price * 100
    icon = "🟡" if tp_type == "TP1" else "✅"
    lines = [
        f"{icon} *TAKE PROFIT {tp_type}*",
        f"Ticker: `{ticker}`",
        f"Sell: {qty} @ ${fill_price:.2f}",
        f"PnL parcial: ${pnl:+,.0f} ({_fmt_pct(pnl_pct)})",
    ]
    if remaining_qty > 0:
        lines.append(f"Resto: {remaining_qty} acciones")
        if new_stop:
            lines.append(f"Stop actualizado: ${new_stop:.2f}")
    send_notification("\n".join(lines))


def notify_trailing_stop_updated(
    ticker: str,
    day: int,
    old_stop: float,
    new_stop: float,
    current_price: float,
    atr: float,
) -> None:
    lines = [
        "🔄 *TRAILING STOP ACTUALIZADO*",
        f"Ticker: `{ticker}` (día {day})",
        f"Stop: ${old_stop:.2f} → ${new_stop:.2f}",
        f"Precio actual: ${current_price:.2f} | ATR: ${atr:.2f}",
    ]
    send_notification("\n".join(lines))


# ── Daily summary ─────────────────────────────────────────────────────────────

def notify_daily_summary(
    date: str,
    tickers_analyzed: int,
    signals: dict,
    orders_executed: list,
    positions: dict,
    cash: float,
    positions_value: float,
    total_equity: float,
    equity_pct_change: float,
    regime: float,
    errors: list,
    cycle_start: str,
    cycle_end: str,
) -> None:
    buy_tickers  = signals.get("BUY",  [])
    sell_tickers = signals.get("SELL", [])
    hold_count   = len(signals.get("HOLD", []))

    pos_lines = []
    for ticker, info in positions.items():
        pnl_pct = info.get("pnl_pct", 0.0)
        sign = "+" if pnl_pct >= 0 else ""
        pos_lines.append(f"  • {ticker} {sign}{pnl_pct:.1f}%")

    lines = [
        f"📊 *RESUMEN DIARIO — {date}*",
        f"Ciclo: {cycle_start} → {cycle_end}",
        "",
        f"Tickers analizados: {tickers_analyzed}",
        f"Señales: {len(buy_tickers)} BUY | {len(sell_tickers)} SELL | {hold_count} HOLD",
        f"Órdenes ejecutadas: {len(orders_executed)}",
        "",
    ]

    if positions:
        lines.append(f"*Posiciones abiertas ({len(positions)}):*")
        lines.extend(pos_lines)
        lines.append("")

    lines += [
        f"Portfolio: {_fmt_cash(cash)} cash + {_fmt_cash(positions_value)} en posiciones",
        f"Total equity: {_fmt_cash(total_equity)} ({_fmt_pct(equity_pct_change)} hoy)",
        f"Régimen actual: {regime:.2f}× ({'alcista' if regime > 1.05 else 'bajista' if regime < 0.95 else 'neutral'})",
    ]

    if errors:
        lines += ["", f"⚠️ Errores: {len(errors)} — {', '.join(errors[:3])}"]

    send_notification("\n".join(lines))


# ── Errores ────────────────────────────────────────────────────────────────────

def notify_critical_error(message: str, context: str = "") -> None:
    lines = [
        "⚠️ *ERROR CRÍTICO*",
        message,
    ]
    if context:
        lines.append(f"Contexto: {context}")
    lines.append("Requiere intervención manual.")
    send_notification("\n".join(lines))


def notify_gateway_unavailable(scheduled_time: str) -> None:
    lines = [
        "⚠️ *IB Gateway no disponible*",
        f"El health-check a las {scheduled_time} falló tras 8 intentos.",
        "Pipeline continúa en *modo degradado* (análisis sin ejecución IBKR).",
        "",
        "Si persiste, reinicia el servicio:",
        "`sudo systemctl restart ibgateway.service`",
    ]
    send_notification("\n".join(lines))
