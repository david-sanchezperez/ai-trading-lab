"""
Monitor intraday — capa de consciencia durante las horas de mercado NYSE.

Registra 5 jobs en el APScheduler existente:
  - price_check:     cada 20 min, 15:30-22:00 CET
  - news_check:      cada 30 min, 15:30-22:00 CET
  - earnings_check:  cada 15 min, 15:30-22:00 CET
  - market_open:     diario 15:30 CET
  - market_close:    diario 22:00 CET

No reemplaza los jobs existentes (pre_market, daily_run, trailing_stop).
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import LOGS_DIR
from core.data_loader import TICKERS_FLAT
from config.monitor_config import MONITOR_CONFIG
from monitor.watchers.price_watcher import PriceWatcher
from monitor.watchers.news_poller import NewsPoller
from monitor.watchers.earnings_watcher import EarningsWatcher
from monitor.classifiers.event_classifier import EventClassifier
from monitor.actions.defensive_action import DefensiveAction
from monitor.actions.opportunistic_entry import OpportunisticEntry

log = logging.getLogger(__name__)

_TZ = ZoneInfo(MONITOR_CONFIG["market_hours"]["timezone"])
_INTRADAY_LOG_DIR = LOGS_DIR / "intraday_context"


class MarketMonitor:

    def __init__(self, broker, scheduler=None):
        self.broker = broker

        # Watchers
        self.price_watcher    = PriceWatcher()
        self.news_poller      = NewsPoller()
        self.earnings_watcher = EarningsWatcher()

        # Classifier + actions
        self.event_classifier     = EventClassifier()
        self.defensive_action     = DefensiveAction()
        self.opportunistic_entry  = OpportunisticEntry()

        # Estado intraday
        self.eod_context:              dict = {}
        self.intraday_entries_today:   int  = 0
        self._event_counts = {
            "price": 0, "news": 0, "earnings": 0,
            "defensive": 0, "opportunistic": 0,
        }

        self._scheduler = scheduler

    # ── Registro de jobs ──────────────────────────────────────────────────────

    def start(self, scheduler=None) -> None:
        """Registra los 5 jobs en el APScheduler proporcionado o en self._scheduler."""
        from apscheduler.triggers.cron import CronTrigger

        sched = scheduler or self._scheduler
        if sched is None:
            # Crear scheduler propio si no se inyectó uno
            from apscheduler.schedulers.background import BackgroundScheduler
            sched = BackgroundScheduler(timezone=_TZ)
            sched.start()
            self._scheduler = sched

        cfg = MONITOR_CONFIG["market_hours"]
        tz  = cfg["timezone"]
        ivl = MONITOR_CONFIG["intervals"]

        # Horas de apertura y cierre para los triggers cron
        open_h,  open_m  = (int(x) for x in cfg["open"].split(":"))
        close_h, close_m = (int(x) for x in cfg["close"].split(":"))

        day_of_week = "mon-fri"

        sched.add_job(
            self._run_price_cycle,
            trigger=CronTrigger(
                day_of_week=day_of_week,
                hour=f"{open_h}-{close_h}",
                minute=f"*/{ivl['price_check_minutes']}",
                timezone=tz,
            ),
            id="monitor_price", replace_existing=True,
            name=f"Monitor price check (every {ivl['price_check_minutes']}min)",
            misfire_grace_time=120,
        )

        sched.add_job(
            self._run_news_cycle,
            trigger=CronTrigger(
                day_of_week=day_of_week,
                hour=f"{open_h}-{close_h}",
                minute=f"*/{ivl['news_check_minutes']}",
                timezone=tz,
            ),
            id="monitor_news", replace_existing=True,
            name=f"Monitor news poll (every {ivl['news_check_minutes']}min)",
            misfire_grace_time=120,
        )

        sched.add_job(
            self._run_earnings_cycle,
            trigger=CronTrigger(
                day_of_week=day_of_week,
                hour=f"{open_h}-{close_h}",
                minute=f"*/{ivl['earnings_check_minutes']}",
                timezone=tz,
            ),
            id="monitor_earnings", replace_existing=True,
            name=f"Monitor earnings check (every {ivl['earnings_check_minutes']}min)",
            misfire_grace_time=120,
        )

        sched.add_job(
            self.market_open_alert,
            trigger=CronTrigger(
                day_of_week=day_of_week,
                hour=open_h, minute=open_m,
                timezone=tz,
            ),
            id="monitor_open", replace_existing=True,
            name="Monitor market open alert (15:30 CET)",
        )

        sched.add_job(
            self.market_close_summary,
            trigger=CronTrigger(
                day_of_week=day_of_week,
                hour=close_h, minute=close_m,
                timezone=tz,
            ),
            id="monitor_close", replace_existing=True,
            name="Monitor market close summary (22:00 CET)",
        )

        log.info("MarketMonitor registrado — 5 jobs en scheduler")

    # ── Comprobación de horario ───────────────────────────────────────────────

    def is_market_hours(self) -> bool:
        now = datetime.now(_TZ)
        if now.strftime("%A") not in MONITOR_CONFIG["market_hours"]["days"]:
            return False
        cfg = MONITOR_CONFIG["market_hours"]
        open_h,  open_m  = (int(x) for x in cfg["open"].split(":"))
        close_h, close_m = (int(x) for x in cfg["close"].split(":"))
        open_total  = open_h  * 60 + open_m
        close_total = close_h * 60 + close_m
        now_total   = now.hour * 60 + now.minute
        return open_total <= now_total < close_total

    # ── Ciclos ────────────────────────────────────────────────────────────────

    def _run_price_cycle(self) -> None:
        if not self.is_market_hours():
            return
        log.debug("[monitor] Ciclo precio")
        try:
            positions = self.broker.get_positions()
            pos_alerts = self.price_watcher.check_positions(positions)
            uni_alerts = self.price_watcher.check_universe(list(TICKERS_FLAT))
            self._event_counts["price"] += len(pos_alerts) + len(uni_alerts)
            self._process_classified_events(pos_alerts + uni_alerts, [], None)
        except Exception as e:
            log.error(f"[monitor] Error en ciclo precio: {e}")

    def _run_news_cycle(self) -> None:
        if not self.is_market_hours():
            return
        log.debug("[monitor] Ciclo noticias")
        try:
            positions    = self.broker.get_positions()
            all_tickers  = list(set(list(positions.keys()) + list(TICKERS_FLAT)))
            events       = self.news_poller.poll(all_tickers)
            material     = [e for e in events if e.is_material]
            self._event_counts["news"] += len(material)
            if material:
                self._process_classified_events([], material, None)
        except Exception as e:
            log.error(f"[monitor] Error en ciclo noticias: {e}")

    def _run_earnings_cycle(self) -> None:
        if not self.is_market_hours():
            return
        log.debug("[monitor] Ciclo earnings")
        try:
            tickers_today = self.earnings_watcher.check_earnings_today(list(TICKERS_FLAT))
            for ticker in tickers_today:
                transcript = self.earnings_watcher.fetch_transcript(ticker)
                if transcript:
                    analysis = self.earnings_watcher.analyze_transcript(ticker, transcript)
                    self.eod_context[f"earnings_{ticker}"] = analysis.__dict__
                    self._event_counts["earnings"] += 1
                    self._process_classified_events([], [], analysis)
        except Exception as e:
            log.error(f"[monitor] Error en ciclo earnings: {e}")

    # ── Procesado de eventos clasificados ─────────────────────────────────────

    def _process_classified_events(
        self, price_alerts, news_events, transcript
    ) -> None:
        positions = self.broker.get_positions()
        events    = self.event_classifier.classify(
            price_alerts, news_events, transcript, positions
        )

        for event in events:
            if event.type == "DEFENSIVE" and event.ticker in positions:
                self._event_counts["defensive"] += 1
                rag = self._get_rag_context(event.ticker)
                self.defensive_action.evaluate_and_act(
                    event, positions[event.ticker], self.broker, rag
                )

            elif event.type == "OPPORTUNISTIC":
                self._event_counts["opportunistic"] += 1
                placed = self.opportunistic_entry.evaluate_and_act(
                    event, self.broker, self.intraday_entries_today
                )
                if placed:
                    self.intraday_entries_today += 1

            else:  # ENRICH_EOD
                key = f"{event.ticker}_{event.timestamp}"
                self.eod_context[key] = event.__dict__

    def _get_rag_context(self, ticker: str) -> str:
        try:
            from core.rag_store import get_similar_situations
            from core.data_loader import get_ticker_metadata
            meta = get_ticker_metadata(ticker)
            dummy_indicators = {"rsi": 50, "sma20": 0, "sma50": 0, "momentum": 0,
                                "confidence": 0.5, "volume_ratio": 1.0,
                                "pct_52w": 0.5, "rs_spy": 0.0}
            similar = get_similar_situations(
                ticker, dummy_indicators, "SELL", n=2,
                thesis=meta.get("thesis")
            )
            if not similar:
                return ""
            return "\n".join(
                f"- {s['metadata'].get('date','?')[:10]}: {s['text']} "
                f"(sim={s['similarity']:.2f})"
                for s in similar
            )
        except Exception:
            return ""

    # ── Alertas de apertura/cierre ────────────────────────────────────────────

    def market_open_alert(self) -> None:
        try:
            self.intraday_entries_today = 0
            self._event_counts = {k: 0 for k in self._event_counts}
            positions = self.broker.get_positions()
            cash      = self.broker.get_cash()

            # Beta del portfolio
            beta_str = ""
            try:
                from analytics.portfolio_risk import get_risk_monitor
                risk     = get_risk_monitor().compute_daily_risk(positions)
                beta_str = f"\nBeta portfolio: {risk.portfolio_beta:.2f}"
            except Exception:
                pass

            msg = (
                f"🔔 *NYSE ABIERTO*\n"
                f"Portfolio: {len(positions)} posiciones abiertas\n"
                f"Cash disponible: ${cash:,.0f}"
                f"{beta_str}\n"
                f"Monitor intraday activo — revisión cada 20 min"
            )
            from scheduler.notifier import send_notification
            send_notification(msg)
            log.info("[monitor] Mercado abierto — monitor intraday activo")
        except Exception as e:
            log.error(f"[monitor] Error en market_open_alert: {e}")

    def market_close_summary(self) -> None:
        try:
            positions = self.broker.get_positions()
            c = self._event_counts
            pos_str = "\n".join(f"  • {t}" for t in positions) or "  (ninguna)"
            msg = (
                f"🔕 *NYSE CERRADO*\n"
                f"Monitor intraday: resumen del día\n"
                f"Eventos detectados: {c['price']} precio | "
                f"{c['news']} noticias | {c['earnings']} earnings\n"
                f"Acciones tomadas: {c['defensive']} defensivas | "
                f"{c['opportunistic']} oportunistas\n"
                f"Contexto acumulado para ciclo 20:30: {len(self.eod_context)} eventos\n"
                f"Estado portfolio:\n{pos_str}"
            )
            from scheduler.notifier import send_notification
            send_notification(msg)

            # Persistir contexto EOD
            self._persist_eod_context()
            log.info("[monitor] Mercado cerrado — contexto EOD persistido")
        except Exception as e:
            log.error(f"[monitor] Error en market_close_summary: {e}")

    def _persist_eod_context(self) -> None:
        try:
            _INTRADAY_LOG_DIR.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y-%m-%d")
            path     = _INTRADAY_LOG_DIR / f"{date_str}.json"
            with open(path, "w") as f:
                json.dump(self.eod_context, f, indent=2, default=str)
            log.info(f"[monitor] EOD context → {path}")
        except Exception as e:
            log.warning(f"[monitor] No se pudo persistir EOD context: {e}")

    # ── API pública para daily_run ────────────────────────────────────────────

    def get_eod_context(self) -> dict:
        """Devuelve el contexto acumulado durante el día para el ciclo 20:30."""
        # Intentar cargar desde disco si self.eod_context está vacío (proceso nuevo)
        if not self.eod_context:
            try:
                date_str = datetime.now().strftime("%Y-%m-%d")
                path     = _INTRADAY_LOG_DIR / f"{date_str}.json"
                if path.exists():
                    with open(path) as f:
                        return json.load(f)
            except Exception:
                pass
        return dict(self.eod_context)
