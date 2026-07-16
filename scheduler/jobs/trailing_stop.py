"""
Job de trailing stops: se ejecuta diariamente tras el análisis post-market.

Secuencia:
  1. Cargar posiciones abiertas + entry points
  2. Obtener precios de cierre del día desde CSV cacheado (PAPER_LOCAL)
     o de IB Gateway en tiempo real (PAPER_IBKR / LIVE)
  3. Actualizar peak prices
  4. Verificar take profits (+2×ATR y +3×ATR) y trailing stops
  5. Ejecutar sells/modificaciones según el modo de broker activo
  6. Persistir estado y notificar via Telegram

Para posiciones sin entry point (anteriores al sistema), se crea
un entry conservador desde avg_price del portfolio con ATR fallback 3.5%.
"""

import logging
from datetime import date

import pandas as pd

from core.config import DATA_DIR
from core import portfolio_sim as sim
from core import entry_tracker
from scheduler.notifier import send_notification
from config.broker_config import BROKER_MODE, BrokerMode

log = logging.getLogger(__name__)


def _get_current_price_csv(ticker: str) -> float | None:
    """Precio de cierre más reciente desde el CSV cacheado."""
    csv_path = DATA_DIR / f"{ticker}.csv"
    if not csv_path.exists():
        return None
    try:
        df = pd.read_csv(csv_path, usecols=["Close"])
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as e:
        log.warning(f"[trailing_stop] No se pudo leer precio de {ticker}: {e}")
        return None


def run() -> dict:
    import asyncio
    import nest_asyncio
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    nest_asyncio.apply(_loop)

    log.info(f"[trailing_stop] Iniciando revisión de trailing stops (modo={BROKER_MODE.value})")

    if BROKER_MODE == BrokerMode.PAPER_LOCAL:
        return _run_paper_local()
    else:
        return _run_ibkr()


# ── PAPER_LOCAL — lógica original ────────────────────────────────────────────

def _run_paper_local() -> dict:
    portfolio_state = sim.load()
    positions = portfolio_state.get("positions", {})

    if not positions:
        log.info("[trailing_stop] Sin posiciones abiertas — nada que revisar")
        return {"stops_triggered": [], "tps_triggered": []}

    today = date.today()
    date_str = today.isoformat()

    current_prices = {}
    for ticker in positions:
        price = _get_current_price_csv(ticker)
        if price is not None:
            current_prices[ticker] = price

    entry_tracker.update_peaks(current_prices)
    entries = entry_tracker.load()
    stops_triggered = []
    tps_triggered = []
    notifications = []

    for ticker, pos in list(positions.items()):
        price = current_prices.get(ticker)
        if price is None:
            log.warning(f"[trailing_stop] Sin precio para {ticker} — skip")
            continue

        if ticker not in entries:
            atr_fallback = round(pos["avg_price"] * 0.035, 4)
            entry_tracker.record_entry(ticker, pos["avg_price"], atr_fallback, pos["quantity"], date_str)
            entries = entry_tracker.load()
            log.info(f"[trailing_stop] Entry bootstrap para {ticker} (sin tracking previo)")

        entry = entries[ticker]
        qty = pos["quantity"]
        stop_level = entry_tracker.get_stop_level(entry, today)
        tp1_level  = entry_tracker.get_tp1_level(entry)
        tp2_level  = entry_tracker.get_tp2_level(entry)

        log.info(
            f"[trailing_stop] {ticker}: precio={price:.2f} "
            f"stop={stop_level:.2f} tp1={tp1_level:.2f} tp2={tp2_level:.2f} "
            f"tp1_triggered={entry.get('tp1_triggered', False)}"
        )

        if entry.get("tp1_triggered") and price >= tp2_level:
            result = sim.sell(portfolio_state, ticker, price, qty, date_str)
            if result["status"] == "filled":
                sim.save(result["state"])
                portfolio_state = result["state"]
                entry_tracker.remove_entry(ticker)
                pnl = round((price - entry["entry_price"]) * qty, 2)
                pnl_pct = round((price - entry["entry_price"]) / entry["entry_price"] * 100, 2)
                msg = (
                    f"✅ *TAKE PROFIT 2* — {ticker}\n"
                    f"Precio: ${price:.2f} | TP2: ${tp2_level:.2f}\n"
                    f"Vendidas: {qty} acciones | PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
                )
                tps_triggered.append({"ticker": ticker, "type": "TP2", "price": price, "qty": qty, "pnl": pnl})
                notifications.append(msg)
                log.info(f"[trailing_stop] TP2 ejecutado: {ticker} @ {price:.2f}")
            continue

        if not entry.get("tp1_triggered") and price >= tp1_level:
            tp1_qty = max(1, qty // 2)
            result = sim.sell(portfolio_state, ticker, price, tp1_qty, date_str)
            if result["status"] == "filled":
                sim.save(result["state"])
                portfolio_state = result["state"]
                remaining = qty - tp1_qty
                entry_tracker.mark_tp1_triggered(ticker, remaining)
                entries = entry_tracker.load()
                pnl = round((price - entry["entry_price"]) * tp1_qty, 2)
                pnl_pct = round((price - entry["entry_price"]) / entry["entry_price"] * 100, 2)
                msg = (
                    f"🎯 *TAKE PROFIT 1* — {ticker}\n"
                    f"Precio: ${price:.2f} | TP1: ${tp1_level:.2f}\n"
                    f"Vendidas: {tp1_qty} acciones | Quedan: {remaining} | "
                    f"PnL parcial: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
                )
                tps_triggered.append({"ticker": ticker, "type": "TP1", "price": price, "qty": tp1_qty, "pnl": pnl})
                notifications.append(msg)
                log.info(f"[trailing_stop] TP1 ejecutado: {ticker} @ {price:.2f}")
            continue

        if price <= stop_level:
            result = sim.sell(portfolio_state, ticker, price, qty, date_str)
            if result["status"] == "filled":
                sim.save(result["state"])
                portfolio_state = result["state"]
                entry_tracker.remove_entry(ticker)
                entries = entry_tracker.load()
                pnl = round((price - entry["entry_price"]) * qty, 2)
                pnl_pct = round((price - entry["entry_price"]) / entry["entry_price"] * 100, 2)
                msg = (
                    f"🛑 *STOP LOSS* — {ticker}\n"
                    f"Precio: ${price:.2f} | Stop: ${stop_level:.2f}\n"
                    f"Vendidas: {qty} acciones | PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
                )
                stops_triggered.append({"ticker": ticker, "price": price, "stop": stop_level, "pnl": pnl})
                notifications.append(msg)
                log.info(f"[trailing_stop] Stop ejecutado: {ticker} @ {price:.2f} (stop={stop_level:.2f})")

    _send_summary(notifications, stops_triggered, tps_triggered, today.isoformat())
    return {"stops_triggered": stops_triggered, "tps_triggered": tps_triggered}


# ── PAPER_IBKR / LIVE ─────────────────────────────────────────────────────────

def _run_ibkr() -> dict:
    """
    En modo IBKR:
    - Actualiza trailing stop via broker.modify_stop() (IBKR ejecuta automáticamente)
    - TP1 (venta parcial 50%) se gestiona manualmente via broker.place_order()
    - TP2 ya está colocado como hijo del bracket original
    """
    from brokers import get_broker

    broker = get_broker()
    if not broker.connect():
        log.error("[trailing_stop] No se pudo conectar a IB Gateway — abortando")
        return {"stops_triggered": [], "tps_triggered": []}

    try:
        today = date.today()
        date_str = today.isoformat()

        all_positions = broker.get_positions()
        # Filtrar posiciones negativas (short involuntario por bug de bracket) y qty=0
        positions = {t: p for t, p in all_positions.items() if p.get("quantity", 0) > 0}
        if not positions:
            log.info("[trailing_stop] Sin posiciones long IBKR — nada que revisar")
            return {"stops_triggered": [], "tps_triggered": []}

        # Limpiar local_state de tickers ya cerrados (posición=0 o short) para evitar
        # que el sistema bloquee re-entradas en esos tickers indefinidamente
        if hasattr(broker, "_local_state"):
            from brokers.ibkr.broker import _save_local_state
            stale = [t for t in list(broker._local_state.keys()) if all_positions.get(t, {}).get("quantity", 0) <= 0]
            for t in stale:
                broker._local_state.pop(t, None)
                log.info(f"[trailing_stop] local_state limpiado para {t} (posición cerrada o short)")
            if stale:
                _save_local_state(broker._local_state)

        current_prices = {}
        for ticker in positions:
            price = broker.get_price(ticker)
            if price is not None:
                current_prices[ticker] = price

        entry_tracker.update_peaks(current_prices)
        entries = entry_tracker.load()
        stops_triggered = []
        tps_triggered = []
        notifications = []

        for ticker, pos in list(positions.items()):
            price = current_prices.get(ticker)
            if price is None:
                log.warning(f"[trailing_stop] Sin precio IBKR para {ticker} — skip")
                continue

            if ticker not in entries:
                atr_fallback = round(pos["avg_price"] * 0.035, 4)
                entry_tracker.record_entry(ticker, pos["avg_price"], atr_fallback, pos["quantity"], date_str)
                entries = entry_tracker.load()
                log.info(f"[trailing_stop] Entry bootstrap IBKR para {ticker}")

            entry = entries[ticker]
            qty = pos["quantity"]
            stop_level = entry_tracker.get_stop_level(entry, today)
            tp1_level  = entry_tracker.get_tp1_level(entry)

            log.info(
                f"[trailing_stop] {ticker}: precio={price:.2f} "
                f"stop_level={stop_level:.2f} tp1={tp1_level:.2f} "
                f"tp1_triggered={entry.get('tp1_triggered', False)}"
            )

            # TP1 — venta parcial manual (50%), IBKR no soporta bracket con venta parcial
            if not entry.get("tp1_triggered") and price >= tp1_level:
                tp1_qty = max(1, qty // 2)
                result = broker.place_order(ticker, "SELL", tp1_qty, price)
                if result.get("status") in ("filled", "submitted"):
                    remaining = qty - tp1_qty
                    entry_tracker.mark_tp1_triggered(ticker, remaining)
                    entries = entry_tracker.load()
                    tps_triggered.append({"ticker": ticker, "type": "TP1", "price": price, "qty": tp1_qty})
                    try:
                        from notifications.telegram_notifier import notify_tp_executed
                        notify_tp_executed(
                            ticker=ticker, tp_type="TP1", qty=tp1_qty,
                            fill_price=price, entry_price=entry["entry_price"],
                            remaining_qty=remaining, new_stop=stop_level,
                        )
                    except Exception:
                        pass
                    log.info(f"[trailing_stop] TP1 IBKR ejecutado: {ticker} @ {price:.2f}")
                continue

            # Trailing stop — mover el stop en IBKR
            local = broker._local_state.get(ticker, {}) if hasattr(broker, "_local_state") else {}
            old_stop = local.get("stop_price", stop_level)
            ok = broker.modify_stop(ticker, stop_level)
            if ok:
                log.info(f"[trailing_stop] Stop actualizado en IBKR: {ticker} → {stop_level:.2f}")
                if stop_level > old_stop:
                    try:
                        from notifications.telegram_notifier import notify_trailing_stop_updated
                        days_held = (today - entry_tracker._parse_entry_date(entry)).days if hasattr(entry_tracker, "_parse_entry_date") else 0
                        notify_trailing_stop_updated(
                            ticker=ticker, day=days_held,
                            old_stop=old_stop, new_stop=stop_level,
                            current_price=price, atr=entry.get("atr_entry", 0),
                        )
                    except Exception:
                        pass
            else:
                log.warning(f"[trailing_stop] No se pudo actualizar stop IBKR para {ticker}")

        _send_summary(notifications, stops_triggered, tps_triggered, date_str)
        return {"stops_triggered": stops_triggered, "tps_triggered": tps_triggered}

    finally:
        broker.disconnect()


def _send_summary(notifications: list, stops: list, tps: list, date_str: str) -> None:
    if notifications:
        header = f"⚡ *Trailing Stops — {date_str}*\n\n"
        send_notification(header + "\n\n".join(notifications))
    else:
        log.info("[trailing_stop] Sin stops ni TPs activados hoy")
    log.info(f"[trailing_stop] Completado — stops: {len(stops)}, TPs: {len(tps)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run()
