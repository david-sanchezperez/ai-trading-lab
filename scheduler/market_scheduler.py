"""
Scheduler principal del sistema.
Registra y ejecuta los jobs pre-market y post-market según el calendario NYSE.
Arranque: python -m scheduler.market_scheduler
"""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import TIMEZONE, PRE_MARKET_TIME, POST_MARKET_TIME, TRAILING_STOP_TIME
from scheduler.jobs.pre_market import run as pre_market_run
from scheduler.jobs.daily_run import run as daily_run
from scheduler.jobs.trailing_stop import run as trailing_stop_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ── Estado del health check ───────────────────────────────────────────────────
# Evita spam de Telegram: solo notifica cuando cambia el estado de ib_insync
_last_ibkr_ok: bool | None = None


def _resolve_outcomes():
    """Resuelve T+1/T+5/T+20 outcomes para predicciones pendientes."""
    try:
        from analytics.prediction_ledger import OutcomeTracker, CalibrationEngine
        counts = OutcomeTracker().resolve_outcomes()
        if any(counts.values()):
            metrics = CalibrationEngine().compute_metrics()
            if not metrics.get("insufficient_data"):
                report = CalibrationEngine().generate_report()
                from scheduler.notifier import send_notification
                send_notification(report)
    except Exception as e:
        log.warning(f"[scheduler] Error en resolve_outcomes: {e}")


def _weekly_calibration():
    """Reporte semanal de calibración (domingos 10:00 CET)."""
    try:
        from analytics.prediction_ledger import CalibrationEngine
        from core.config import LOGS_DIR
        import json
        from datetime import date

        engine  = CalibrationEngine()
        metrics = engine.compute_metrics()
        report  = engine.generate_report()

        cal_dir = LOGS_DIR / "calibration"
        cal_dir.mkdir(parents=True, exist_ok=True)
        week_key = date.today().strftime("%Y-W%W")
        with open(cal_dir / f"{week_key}.json", "w") as f:
            json.dump(metrics, f, indent=2)

        from scheduler.notifier import send_notification
        send_notification(report)
        log.info(f"[scheduler] Calibration report semanal enviado ({week_key})")
    except Exception as e:
        log.warning(f"[scheduler] Error en calibración semanal: {e}")


def _parse_time(t: str) -> tuple[int, int]:
    h, m = t.split(":")
    return int(h), int(m)


# ── Helpers de diagnóstico ────────────────────────────────────────────────────

def _port_open() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 4002), timeout=3):
            return True
    except OSError:
        return False


def _svc_active(name: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _test_ibkr_api() -> bool:
    """Prueba conexión ib_insync con client_id=98 (nunca conflicta con el broker principal)."""
    try:
        from brokers.ibkr.gateway import IBGateway
        gw = IBGateway(client_id=98)
        # 3 reintentos con backoff 3s → 3s, 6s (total máx ~15s)
        ok = gw.connect(max_retries=3, retry_delay=3.0)
        if ok:
            gw.disconnect()
        return ok
    except Exception:
        return False


# ── Startup check ─────────────────────────────────────────────────────────────

def _startup_check():
    """
    Se ejecuta 5 min después del arranque. Verifica gateway + servicios y
    envía Telegram con estado completo.
    """
    import asyncio
    import nest_asyncio
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    nest_asyncio.apply(_loop)

    global _last_ibkr_ok
    from scheduler.notifier import send_notification

    xvfb_ok = _svc_active("xvfb.service")
    gw_ok   = _svc_active("ibgateway.service")
    port_ok = _port_open()
    ibkr_ok = _test_ibkr_api() if port_ok else False

    _last_ibkr_ok = ibkr_ok  # inicializa baseline para health check

    icon = "✅" if (xvfb_ok and gw_ok and port_ok and ibkr_ok) else "⚠️"
    lines = [
        f"{icon} *Sistema arrancado* — Trading Scheduler activo",
        "",
        f"Xvfb :99:        {'✅ activo'  if xvfb_ok else '❌ inactivo'}",
        f"IB Gateway:      {'✅ activo'  if gw_ok   else '❌ inactivo'}",
        f"Puerto 4002:     {'✅ abierto' if port_ok else '❌ cerrado'}",
        f"ib\\_insync API:  {'✅ conecta' if ibkr_ok else '❌ no conecta'}",
    ]
    send_notification("\n".join(lines))
    log.info(f"[scheduler] Startup check — gateway: {'OK' if ibkr_ok else 'FAIL'}")


# ── Health check periódico ────────────────────────────────────────────────────

def _health_check():
    """
    Se ejecuta cada 30 min en horario de mercado (L-V 15:45-22:00 CET).
    Solo notifica por Telegram si el estado de ib_insync CAMBIA respecto
    al último check (evita spam).
    """
    import asyncio
    import nest_asyncio
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    nest_asyncio.apply(_loop)

    global _last_ibkr_ok
    from scheduler.notifier import send_notification

    port_ok = _port_open()
    ibkr_ok = _test_ibkr_api() if port_ok else False

    log.info(f"[scheduler] Health check — ib_insync: {'OK' if ibkr_ok else 'FAIL'}")

    # Notificar solo si el estado cambió
    if _last_ibkr_ok is None or ibkr_ok != _last_ibkr_ok:
        if ibkr_ok:
            msg = "✅ *IB Gateway reconectado* — ib\\_insync API operativa"
        else:
            gw_ok = _svc_active("ibgateway.service")
            msg = (
                "⚠️ *IB Gateway sin conexión*\n\n"
                f"IB Gateway service: {'✅ activo' if gw_ok else '❌ inactivo'}\n"
                f"Puerto 4002:        {'✅ abierto' if port_ok else '❌ cerrado'}\n"
                f"ib\\_insync API:     ❌ no conecta\n\n"
                "Las órdenes caerán a PaperBroker local hasta que se recupere."
            )
        send_notification(msg)
        _last_ibkr_ok = ibkr_ok


# ── Builder ───────────────────────────────────────────────────────────────────

def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=TIMEZONE)

    pre_h,   pre_m   = _parse_time(PRE_MARKET_TIME)
    post_h,  post_m  = _parse_time(POST_MARKET_TIME)
    trail_h, trail_m = _parse_time(TRAILING_STOP_TIME)

    scheduler.add_job(
        pre_market_run,
        trigger=CronTrigger(hour=pre_h, minute=pre_m, timezone=TIMEZONE),
        id="pre_market",
        name=f"Pre-market job ({PRE_MARKET_TIME} CET)",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        daily_run,
        trigger=CronTrigger(hour=post_h, minute=post_m, timezone=TIMEZONE),
        id="daily_run",
        name=f"Daily run ({POST_MARKET_TIME} CET)",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        trailing_stop_run,
        trigger=CronTrigger(hour=trail_h, minute=trail_m, timezone=TIMEZONE),
        id="trailing_stop",
        name=f"Trailing stops job ({TRAILING_STOP_TIME} CET)",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        _resolve_outcomes,
        trigger=CronTrigger(hour=21, minute=30, day_of_week="mon-fri", timezone=TIMEZONE),
        id="outcome_resolution",
        name="Outcome resolution (21:30 CET)",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        _weekly_calibration,
        trigger=CronTrigger(day_of_week="sun", hour=10, minute=0, timezone=TIMEZONE),
        id="calibration_report",
        name="Weekly calibration report (Sun 10:00 CET)",
        misfire_grace_time=3600,
    )

    # Health check cada 30 min en horario de mercado (L-V 15:45-22:00 CET)
    scheduler.add_job(
        _health_check,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="15-21",
            minute="15,45",
            timezone=TIMEZONE,
        ),
        id="ibkr_health_check",
        name="IBKR health check (cada 30 min, horario mercado)",
        misfire_grace_time=120,
    )

    return scheduler


# ── Entry point ───────────────────────────────────────────────────────────────

def _acquire_instance_lock():
    """
    Garantiza que solo una instancia del scheduler corre a la vez.
    Usa un file lock exclusivo — si ya hay una instancia, termina con error.
    """
    import fcntl
    lock_path = "/tmp/trading-scheduler.lock"
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error(
            "[scheduler] Ya hay una instancia corriendo (lock en %s). "
            "Detén la instancia anterior antes de lanzar una nueva.", lock_path
        )
        raise SystemExit(1)
    lock_file.write(str(__import__("os").getpid()))
    lock_file.flush()
    # No cerramos el archivo — el lock se libera automáticamente al terminar el proceso
    return lock_file


_lock_handle = None

def run():
    global _lock_handle
    _lock_handle = _acquire_instance_lock()
    scheduler = build_scheduler()

    # Startup check: 5 min tras el arranque, verifica gateway y notifica por Telegram
    from datetime import datetime, timedelta
    startup_run_time = datetime.now() + timedelta(minutes=5)
    scheduler.add_job(
        _startup_check,
        trigger="date",
        run_date=startup_run_time,
        id="startup_check",
        name="Startup gateway check (5 min tras arranque)",
    )

    log.info("=" * 55)
    log.info("  AI Trading Lab — Market Scheduler arrancando")
    log.info("=" * 55)
    for job in scheduler.get_jobs():
        log.info(f"  [{job.id}] {job.name}")
    log.info("=" * 55)
    log.info("  Ctrl+C para detener")
    log.info("=" * 55)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("[scheduler] Detenido por el usuario (Ctrl+C)")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    run()
