"""
Job pre-market: se ejecuta a las 13:00 CET antes de la apertura de NYSE.
Secuencia: verificar calendario → descargar datos → actualizar RAG → notificar apertura.
El análisis de señales se hace en post_market.py al cierre del mercado (20:45 CET).
"""

import logging
from datetime import date

import pandas_market_calendars as mcal

from core.config import MARKET_CALENDAR
from scheduler.notifier import send_notification

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def _is_market_open_today() -> bool:
    cal = mcal.get_calendar(MARKET_CALENDAR)
    today = date.today().isoformat()
    schedule = cal.schedule(start_date=today, end_date=today)
    return not schedule.empty


def run():
    log.info("[pre_market] Iniciando job pre-market")

    # ── Paso 1: verificar calendario NYSE ────────────────────────────────────
    try:
        if not _is_market_open_today():
            msg = f"📅 Hoy ({date.today()}) NYSE no abre. Job pre-market cancelado."
            log.info(f"[pre_market] {msg}")
            send_notification(msg)
            return
        log.info("[pre_market] NYSE abre hoy — continuando")
    except Exception as e:
        log.warning(f"[pre_market] Error verificando calendario: {e} — continuando igualmente")

    # ── Paso 2: descargar datos ───────────────────────────────────────────────
    try:
        log.info("[pre_market] Descargando datos OHLCV (1y)...")
        from core.data_loader import load_all_data
        load_all_data(period="1y")
        log.info("[pre_market] Datos descargados correctamente")
    except Exception as e:
        msg = f"❌ *pre_market* — Error descargando datos: `{e}`\nAbortando."
        log.error(f"[pre_market] {msg}")
        send_notification(msg)
        return

    # ── Paso 3: actualizar RAG (no bloqueante) ────────────────────────────────
    try:
        log.info("[pre_market] Actualizando RAG...")
        from scripts.populate_rag import update_today
        n = update_today()
        log.info(f"[pre_market] RAG actualizado: {n} situaciones")
    except Exception as e:
        log.warning(f"[pre_market] WARNING — RAG update falló: {e} — continuando sin RAG actualizado")

    # ── Paso 4: recalcular win rates históricos ───────────────────────────────
    try:
        log.info("[pre_market] Recalculando win rates...")
        from scripts.compute_win_rates import compute_and_save
        compute_and_save()
        log.info("[pre_market] Win rates actualizados")
    except Exception as e:
        log.warning(f"[pre_market] WARNING — Win rates falló: {e} — continuando sin actualizar")

    # ── Paso 5: precalentar PEAD (earnings surprise) ─────────────────────────
    try:
        log.info("[pre_market] Precalculando PEAD earnings surprise...")
        from core.data_loader import TICKERS_FLAT
        from core.earnings_surprise import prefetch_all as pead_prefetch
        pead_scores = pead_prefetch(TICKERS_FLAT)
        active_pead = {t: s for t, s in pead_scores.items() if abs(s) > 0.01}
        log.info(f"[pre_market] PEAD: {len(active_pead)}/{len(pead_scores)} tickers con señal")
        if active_pead:
            log.info(f"[pre_market] PEAD activos: {active_pead}")
    except Exception as e:
        log.warning(f"[pre_market] WARNING — PEAD prefetch falló: {e} — continuando")

    # ── Paso 6: precalentar señales insider (Form 4 SEC) ─────────────────────
    try:
        log.info("[pre_market] Precalculando señales insider (Form 4)...")
        from core.data_loader import TICKERS_FLAT
        from core.insider_signal import prefetch_all as insider_prefetch
        insider_scores = insider_prefetch(TICKERS_FLAT)
        active_insider = {t: s for t, s in insider_scores.items() if s > 0.01}
        log.info(f"[pre_market] Insider: {len(active_insider)}/{len(insider_scores)} tickers con señal")
        if active_insider:
            log.info(f"[pre_market] Insider activos: {active_insider}")
    except Exception as e:
        log.warning(f"[pre_market] WARNING — Insider prefetch falló: {e} — continuando")

    # ── Paso 7: notificar apertura ────────────────────────────────────────────
    try:
        send_notification(
            f"🔔 *Apertura NYSE — {date.today()}*\n"
            f"Datos descargados y RAG actualizado.\n"
            f"Análisis de señales a las 20:45 CET."
        )
    except Exception as e:
        log.warning(f"[pre_market] Error enviando notificación de apertura: {e}")

    log.info("[pre_market] Job completado")


if __name__ == "__main__":
    run()
