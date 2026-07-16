"""
Job principal del ciclo diario (20:30 CET, lunes–viernes).

Secuencia:
  1. Verificar que IB Gateway está disponible
  2. Verificar calendario NYSE
  3. Ejecutar pipeline completo para todos los tickers
  4. Recopilar portfolio desde IBKR (o sim en PAPER_LOCAL)
  5. Persistir daily report en logs/daily_reports/YYYY-MM-DD.json
  6. Enviar resumen por Telegram
"""

import json
import logging
import socket
import time
from datetime import datetime, date
from pathlib import Path

import pandas_market_calendars as mcal

from core.config import LOGS_DIR, DAILY_SIGNALS_PATH, MARKET_CALENDAR, PNL_HISTORY_PATH
from config.broker_config import BROKER_MODE, BrokerMode, IBKR_INITIAL_CAPITAL
from config.schedule_config import DAILY_RUN_TIME

log = logging.getLogger(__name__)

DAILY_REPORTS_DIR = LOGS_DIR / "daily_reports"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_port_open(host: str = "127.0.0.1", port: int = 4002, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _restart_ibgateway() -> None:
    """Reinicia ibgateway.service si el usuario tiene NOPASSWD configurado."""
    import subprocess
    try:
        r = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "ibgateway.service"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            log.info("[daily_run] ibgateway.service reiniciado — esperando login IBC")
        else:
            log.warning(f"[daily_run] No se pudo reiniciar ibgateway (sudo -n): {r.stderr.decode().strip()}")
    except Exception as e:
        log.warning(f"[daily_run] Error al reiniciar ibgateway: {e}")


def _wait_for_gateway_ready(max_retries: int = 8, retry_delay: float = 15.0) -> bool:
    """
    Comprueba que IB Gateway esté listo para aceptar conexiones API reales.
    Si el puerto está cerrado en el primer intento, intenta reiniciar el servicio.
    """
    for attempt in range(1, max_retries + 1):
        if not _is_port_open():
            if attempt == 1:
                log.info("[daily_run] Puerto 4002 cerrado — intentando reiniciar ibgateway.service")
                _restart_ibgateway()
            if attempt < max_retries:
                log.info(f"[daily_run] Puerto 4002 cerrado (intento {attempt}/{max_retries}) — esperando {retry_delay}s")
                time.sleep(retry_delay)
                continue
            return False
        # Puerto abierto — verificar que la API responde de verdad con IBGateway directo
        try:
            from brokers.ibkr.gateway import IBGateway
            gw = IBGateway(client_id=99)
            ok = gw.connect(max_retries=3, retry_delay=3.0)
            if ok:
                gw.disconnect()
                log.info("[daily_run] IB Gateway listo ✓")
                return True
        except Exception as e:
            log.warning(f"[daily_run] Excepción al probar conexión ib_insync: {e}")
        if attempt < max_retries:
            log.info(f"[daily_run] Gateway TCP up pero API no lista (intento {attempt}/{max_retries}) — esperando {retry_delay}s")
            time.sleep(retry_delay)
    return False


def _is_market_open_today() -> bool:
    try:
        cal = mcal.get_calendar(MARKET_CALENDAR)
        today = date.today().isoformat()
        return not cal.schedule(start_date=today, end_date=today).empty
    except Exception as e:
        log.warning(f"[daily_run] Error verificando calendario NYSE: {e} — continuando")
        return True


def _get_ibkr_portfolio() -> dict:
    """Lee portfolio desde IBKR (PAPER_IBKR/LIVE) o retorna vacío si falla."""
    try:
        from brokers.ibkr.broker import IBKRBroker
        broker = IBKRBroker(client_id=5)
        broker.connect()
        positions = broker.get_positions()
        cash      = broker.get_cash()
        value     = broker.get_portfolio_value()
        broker.disconnect()
        return {"cash": cash, "total_value": value, "positions": positions}
    except Exception as e:
        log.warning(f"[daily_run] No se pudo obtener portfolio IBKR: {e}")
        return {}


def _get_sim_portfolio() -> dict:
    """Lee portfolio simulado (PAPER_LOCAL)."""
    try:
        import core.portfolio_sim as sim
        from core.data_loader import TICKERS_FLAT
        state = sim.load()
        prices = {t: state["positions"].get(t, {}).get("avg_price", 0) for t in TICKERS_FLAT}
        return sim.summary(state, prices)
    except Exception as e:
        log.warning(f"[daily_run] No se pudo obtener portfolio simulado: {e}")
        return {}


def _save_daily_report(report: dict) -> Path:
    DAILY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DAILY_REPORTS_DIR / f"{report['date']}.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    log.info(f"[daily_run] Daily report guardado: {path}")
    return path


# ── Job principal ─────────────────────────────────────────────────────────────

def run() -> dict:
    # APScheduler usa ThreadPoolExecutor — Python 3.12 no crea event loop asyncio
    # en threads secundarios. nest_asyncio permite que ib_insync corra en sync context.
    import asyncio
    import nest_asyncio
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    nest_asyncio.apply(_loop)

    cycle_start = datetime.now().strftime("%H:%M:%S")
    date_str    = datetime.now().strftime("%Y-%m-%d")
    log.info(f"[daily_run] ──── Ciclo diario {date_str} {cycle_start} ────")

    from notifications.telegram_notifier import notify_gateway_unavailable, notify_critical_error, notify_daily_summary

    # ── 1. Verificar IB Gateway ──────────────────────────────────────────────
    ibkr_available = True
    if BROKER_MODE != BrokerMode.PAPER_LOCAL:
        ibkr_available = _wait_for_gateway_ready()
        if not ibkr_available:
            log.warning("[daily_run] IB Gateway no disponible — modo degradado (sin ejecución IBKR)")
            notify_gateway_unavailable(DAILY_RUN_TIME)
        else:
            log.info("[daily_run] IB Gateway disponible ✓")

    # ── 2. Verificar calendario NYSE ─────────────────────────────────────────
    if not _is_market_open_today():
        msg = f"NYSE no abrió hoy ({date_str}) — ciclo cancelado"
        log.info(f"[daily_run] {msg}")
        return {"status": "skipped", "reason": msg}

    # ── 2b. Portfolio risk — antes de análisis ───────────────────────────────
    risk_report_str = ""
    risk_warnings   = []
    try:
        from analytics.portfolio_risk import get_risk_monitor
        from brokers import get_broker
        _broker = get_broker()
        _broker.connect()
        positions = _broker.get_positions()
        _broker.disconnect()
        if positions:
            risk  = get_risk_monitor().compute_daily_risk(positions)
            risk_warnings = risk.warnings
            risk_report_str = get_risk_monitor().format_for_report(risk)
            if risk_warnings:
                from notifications.telegram_notifier import notify_critical_error
                notify_critical_error(
                    "Riesgos de portfolio detectados:\n" + "\n".join(risk_warnings),
                    context="daily_run"
                )
    except Exception as e:
        log.warning(f"[daily_run] Error en portfolio risk: {e}")

    # ── 3. Cargar contexto intraday del día ──────────────────────────────────
    intraday_context: dict = {}
    try:
        intraday_path = LOGS_DIR / "intraday_context" / f"{date_str}.json"
        if intraday_path.exists():
            with open(intraday_path) as f:
                intraday_context = json.load(f)
            log.info(f"[daily_run] Contexto intraday: {len(intraday_context)} eventos")
    except Exception as e:
        log.warning(f"[daily_run] No se pudo cargar contexto intraday: {e}")

    # ── 4. Ejecutar pipeline completo ────────────────────────────────────────
    errors = []
    try:
        log.info("[daily_run] Lanzando analyze_all...")
        from scripts.analyze_all import main as analyze_main
        analyze_main(intraday_context=intraday_context)
        log.info("[daily_run] analyze_all completado")
    except Exception as e:
        log.error(f"[daily_run] Error en analyze_all: {e}")
        errors.append(f"analyze_all: {e}")
        notify_critical_error(f"Error en analyze_all: {e}", context=date_str)

    # ── 4. Leer señales generadas ─────────────────────────────────────────────
    signals_data = {}
    try:
        if DAILY_SIGNALS_PATH.exists():
            with open(DAILY_SIGNALS_PATH) as f:
                signals_data = json.load(f)
    except Exception as e:
        log.warning(f"[daily_run] No se pudo leer daily_signals.json: {e}")

    summary_signals = signals_data.get("summary", {"BUY": [], "SELL": [], "HOLD": []})
    tickers_analyzed = signals_data.get("tickers_analyzed", 0)
    regime = signals_data.get("regime_adjustment", 1.0)
    signal_list = signals_data.get("signals", [])

    # "submitted" = orden enviada a IBKR (bracket), "filled" = orden de mercado ejecutada
    orders_executed = [
        s for s in signal_list
        if s.get("trade") and s["trade"]
        and s["trade"].get("status") in ("filled", "submitted")
    ]
    analysis_errors = signals_data.get("errors", [])
    for e in analysis_errors:
        errors.append(f"{e['ticker']}: {e['error']}" if isinstance(e, dict) else str(e))

    # ── 5. Portfolio actual ───────────────────────────────────────────────────
    if BROKER_MODE != BrokerMode.PAPER_LOCAL and ibkr_available:
        portfolio = _get_ibkr_portfolio()
    else:
        portfolio = signals_data.get("portfolio_snapshot") or _get_sim_portfolio()

    cash             = portfolio.get("cash", 0.0)
    positions        = portfolio.get("positions", {})
    total_value      = portfolio.get("total_value", cash)
    positions_value  = total_value - cash

    # Variación diaria: comparar con ayer en pnl_history
    equity_pct_change = 0.0
    try:
        if PNL_HISTORY_PATH.exists():
            with open(PNL_HISTORY_PATH) as f:
                history = json.load(f)
            if len(history) >= 2:
                prev = history[-2]["total_value"]
                equity_pct_change = (total_value - prev) / prev * 100
    except Exception:
        pass

    cycle_end = datetime.now().strftime("%H:%M:%S")

    # ── 6. Persistir daily report ─────────────────────────────────────────────
    report = {
        "date":             date_str,
        "cycle_start":      cycle_start,
        "cycle_end":        cycle_end,
        "broker_mode":      BROKER_MODE.value,
        "tickers_analyzed": tickers_analyzed,
        "signals": {
            "BUY":  summary_signals.get("BUY",  []),
            "SELL": summary_signals.get("SELL", []),
            "HOLD": summary_signals.get("HOLD", []),
        },
        "orders_executed": [
            {
                "ticker": o.get("ticker"),
                "action": o.get("decision"),
                "score":  o.get("score"),
                "trade":  o.get("trade"),
            }
            for o in orders_executed
        ],
        "positions_open": [
            {"ticker": t, **info}
            for t, info in positions.items()
        ],
        "portfolio_summary": {
            "cash":            cash,
            "positions_value": positions_value,
            "total_value":     total_value,
            "equity_pct_change_today": round(equity_pct_change, 4),
        },
        "regime_multiplier": regime,
        "errors":            errors,
    }
    _save_daily_report(report)

    # ── 6b. PnL history — fuente de verdad IBKR ──────────────────────────────
    # En PAPER_LOCAL, analyze_all.py escribe pnl_history desde portfolio_sim.
    # En PAPER_IBKR / LIVE, aquí usamos los datos reales de IBKR.
    if BROKER_MODE != BrokerMode.PAPER_LOCAL:
        try:
            prev_trades = 0
            history_data: list = []
            if PNL_HISTORY_PATH.exists():
                with open(PNL_HISTORY_PATH) as f:
                    history_data = json.load(f)
                if history_data:
                    prev_trades = history_data[-1].get("total_trades", 0)

            pnl_total     = round(total_value - IBKR_INITIAL_CAPITAL, 2)
            pnl_total_pct = round(pnl_total / IBKR_INITIAL_CAPITAL * 100, 2)
            pnl_entry = {
                "date":              date_str,
                "total_value":       round(total_value, 2),
                "cash":              round(cash, 2),
                "pnl_total":         pnl_total,
                "pnl_total_pct":     pnl_total_pct,
                "total_trades":      prev_trades + len(orders_executed),
                "regime_adjustment": regime,
            }
            history_data = [e for e in history_data if e["date"] != date_str]
            history_data.append(pnl_entry)
            with open(PNL_HISTORY_PATH, "w") as f:
                json.dump(history_data, f, indent=2)
            log.info(
                f"[daily_run] PnL history (IBKR) → {date_str} | "
                f"total={total_value:.2f} | PnL={pnl_total:+.2f} ({pnl_total_pct:+.2f}%)"
            )
        except Exception as e:
            log.warning(f"[daily_run] Error escribiendo pnl_history IBKR: {e}")

    # ── 7. Calibration report (si hay datos suficientes) ─────────────────────
    calibration_str = ""
    try:
        from analytics.prediction_ledger import CalibrationEngine
        calibration_str = CalibrationEngine().generate_report()
        if not calibration_str.startswith("Insuficientes"):
            from scheduler.notifier import send_notification
            send_notification(calibration_str)
    except Exception as e:
        log.warning(f"[daily_run] Error en calibration report: {e}")

    # ── 8. Slippage stats ─────────────────────────────────────────────────────
    slippage_str = ""
    try:
        from analytics.slippage_analyzer import get_slippage_analyzer
        slippage_str = get_slippage_analyzer().format_for_report()
    except Exception as e:
        log.warning(f"[daily_run] Error en slippage stats: {e}")

    # ── 9. Telegram summary ───────────────────────────────────────────────────
    try:
        notify_daily_summary(
            date=date_str,
            tickers_analyzed=tickers_analyzed,
            signals=summary_signals,
            orders_executed=orders_executed,
            positions=positions,
            cash=cash,
            positions_value=positions_value,
            total_equity=total_value,
            equity_pct_change=equity_pct_change,
            regime=regime,
            errors=errors,
            cycle_start=cycle_start,
            cycle_end=cycle_end,
        )
        # Apéndices: riesgo de portfolio + slippage
        extras = [s for s in [risk_report_str, slippage_str] if s]
        if extras:
            from scheduler.notifier import send_notification
            send_notification("\n\n".join(extras))
    except Exception as e:
        log.warning(f"[daily_run] Error enviando Telegram summary: {e}")

    log.info(
        f"[daily_run] Ciclo completado — "
        f"{tickers_analyzed} tickers | {len(orders_executed)} órdenes | "
        f"{len(errors)} errores"
    )
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run()
