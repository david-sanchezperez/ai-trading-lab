"""
Scheduler mínimo para DS220+.
Solo responsabilidad: despertar el PC antes de las ventanas de mercado.
No despierta el PC en fines de semana ni festivos NYSE.
El PC tiene su propio systemd service que arranca los jobs de análisis.
"""
import logging
from datetime import date

import pandas_market_calendars as mcal
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.jobs.wol_job import wake_pc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

MADRID = pytz.timezone("Europe/Madrid")


def _is_market_open_today() -> bool:
    cal = mcal.get_calendar("NYSE")
    today = date.today().isoformat()
    schedule = cal.schedule(start_date=today, end_date=today)
    return not schedule.empty


def _wol_job(label: str) -> None:
    """Despierta el PC solo si NYSE abre hoy."""
    if not _is_market_open_today():
        logger.info(f"[{label}] NYSE no abre hoy ({date.today()}) — WoL cancelado")
        return

    logger.info(f"[{label}] NYSE abre hoy — enviando WoL")
    success = wake_pc()
    if not success:
        logger.error(f"[{label}] PC no respondió — revisar manualmente")


def wol_pre_market():
    """Job 12:50 CET — despierta PC 10 min antes de la descarga de datos (13:00)."""
    _wol_job("wol_pre_market")


def wol_post_market():
    """Job 20:20 CET — despierta PC 10 min antes del análisis post-market (20:30)."""
    _wol_job("wol_post_market")


scheduler = BlockingScheduler(timezone=MADRID)

scheduler.add_job(
    wol_pre_market,
    CronTrigger(hour=12, minute=50, timezone=MADRID),
    id="wol_pre_market",
    name="Wake PC — pre-market data download (13:00)",
    misfire_grace_time=300,
)

scheduler.add_job(
    wol_post_market,
    CronTrigger(hour=20, minute=20, timezone=MADRID),
    id="wol_post_market",
    name="Wake PC — post-market analysis (20:30)",
    misfire_grace_time=300,
)

if __name__ == "__main__":
    logger.info("DS220+ WoL scheduler arrancando")
    logger.info("  Jobs registrados:")
    logger.info("  - 12:50 CET — WoL pre-market (solo días NYSE)")
    logger.info("  - 20:20 CET — WoL post-market (solo días NYSE)")
    scheduler.start()
