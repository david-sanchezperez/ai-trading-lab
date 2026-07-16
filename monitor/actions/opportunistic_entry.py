"""
Evalúa y ejecuta entradas oportunistas intraday.

Pre-flight checks obligatorios antes de calcular el score técnico:
  1. Hora < 19:45 CET (blackout antes del cierre)
  2. intraday_entries_today < 2
  3. Sin earnings en las próximas 48h
  4. Cash disponible > 25% del equity total
  5. Ticker no en posiciones abiertas

Si pasa los checks, calcula el score técnico real.
Si score > 1.30: coloca bracket order al 50% del sizing normal.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yfinance as yf

from monitor.classifiers.event_classifier import ClassifiedEvent
from config.monitor_config import MONITOR_CONFIG

log = logging.getLogger(__name__)

_OPP_CFG = MONITOR_CONFIG["opportunistic"]
_TZ_CET  = ZoneInfo("Europe/Madrid")
_MARKET_CLOSE_CET = (22, 0)  # 22:00 CET


class OpportunisticEntry:

    def evaluate_and_act(
        self,
        event:                   ClassifiedEvent,
        broker,                                    # BaseBroker
        intraday_entries_today:  int = 0,
    ) -> bool:
        """
        Evalúa si se puede ejecutar una entrada oportunista.
        Devuelve True si se colocó una orden, False si se bloqueó.
        """
        ticker = event.ticker

        # ── Pre-flight checks ─────────────────────────────────────────────────

        # 1. Blackout antes del cierre
        if self._is_blackout():
            log.info(f"[opportunistic] {ticker}: bloqueado — blackout antes de cierre")
            return False

        # 2. Máximo de entradas intraday
        max_entries = _OPP_CFG["max_intraday_entries_per_day"]
        if intraday_entries_today >= max_entries:
            log.info(
                f"[opportunistic] {ticker}: bloqueado — "
                f"máximo de entradas diarias ({max_entries}) alcanzado"
            )
            return False

        # 3. Earnings próximas 48h
        if self._has_earnings_soon(ticker):
            log.info(f"[opportunistic] {ticker}: bloqueado — earnings en < 48h")
            return False

        # 4. Cash disponible > 25% del equity
        cash  = broker.get_cash()
        total = broker.get_portfolio_value()
        if total > 0 and cash / total < 0.25:
            log.info(
                f"[opportunistic] {ticker}: bloqueado — "
                f"cash insuficiente ({cash/total:.1%} < 25%)"
            )
            return False

        # 5. Ticker no en posiciones abiertas
        positions = broker.get_positions()
        if ticker in positions:
            log.info(f"[opportunistic] {ticker}: bloqueado — ya en portfolio")
            return False

        # ── Score técnico en tiempo real ──────────────────────────────────────
        score = self._compute_technical_score(ticker)
        if score is None or score < _OPP_CFG["min_score"]:
            log.info(
                f"[opportunistic] {ticker}: score técnico {score} "
                f"< {_OPP_CFG['min_score']} — no entrada"
            )
            return False

        # ── Sizing al 50% del ATR normal ──────────────────────────────────────
        price, atr = self._get_price_and_atr(ticker)
        if price is None or atr is None:
            log.warning(f"[opportunistic] {ticker}: no se pudo obtener precio/ATR")
            return False

        import core.portfolio_sim as sim_mod
        full_qty = sim_mod.compute_quantity_atr(ticker, price, atr, total, risk_per_trade=0.01)
        half_qty = max(1, full_qty // 2)

        stop_price = round(price - 2.0 * atr, 4)
        tp1_price  = round(price + 2.0 * atr, 4)
        tp2_price  = round(price + 3.0 * atr, 4)

        result = broker.place_bracket_order(
            ticker, "BUY", half_qty, price, stop_price, tp2_price
        )

        if result.get("status") in ("submitted", "filled"):
            log.info(
                f"[opportunistic] {ticker}: ENTRADA — "
                f"{half_qty} acciones @ ${price:.2f} (score={score:.3f})"
            )
            self._notify_entry(
                ticker, half_qty, price, stop_price, tp1_price, tp2_price,
                score, event.trigger
            )
            return True

        log.warning(f"[opportunistic] {ticker}: orden rechazada — {result.get('reason')}")
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_blackout(self) -> bool:
        now     = datetime.now(_TZ_CET)
        blackout_minutes = _OPP_CFG["blackout_before_eod_minutes"]
        close_h, close_m = _MARKET_CLOSE_CET
        close_total_min  = close_h * 60 + close_m
        now_total_min    = now.hour * 60 + now.minute
        return now_total_min >= close_total_min - blackout_minutes

    def _has_earnings_soon(self, ticker: str) -> bool:
        try:
            from graph.trading_graph import _days_to_earnings
            days = _days_to_earnings(ticker)
            return days is not None and 0 <= days <= 2
        except Exception:
            return False

    def _compute_technical_score(self, ticker: str) -> float | None:
        try:
            from core.data_loader import fetch_data
            from core.indicators import add_indicators, add_relative_strength
            from agents.technical_agent import generate_signal
            from agents.decision_agent import make_decision

            df = fetch_data(ticker, period="1y")
            df = add_indicators(df)
            df = add_relative_strength(df, None)

            tech = generate_signal(df)
            # Score técnico simple: confidence × signal_dir
            direction = 1 if tech["signal"] == "BUY" else (-1 if tech["signal"] == "SELL" else 0)
            return round(tech["confidence"] * direction * 1.5, 3)
        except Exception as e:
            log.warning(f"[opportunistic] Error calculando score de {ticker}: {e}")
            return None

    def _get_price_and_atr(self, ticker: str) -> tuple[float | None, float | None]:
        try:
            import pandas as pd
            from core.config import DATA_DIR
            csv = DATA_DIR / f"{ticker}.csv"
            if csv.exists():
                df    = pd.read_csv(csv)
                price = float(df["Close"].iloc[-1])
                prev  = df["Close"].shift(1)
                tr    = pd.concat([
                    df["High"] - df["Low"],
                    (df["High"] - prev).abs(),
                    (df["Low"]  - prev).abs(),
                ], axis=1).max(axis=1)
                atr = float(tr.rolling(14).mean().iloc[-1])
                return price, atr
        except Exception:
            pass
        return None, None

    def _notify_entry(
        self, ticker, qty, price, stop, tp1, tp2, score, trigger
    ):
        try:
            msg = (
                f"🟢 *ENTRADA OPORTUNISTA* — {ticker}\n"
                f"Qty: {qty} @ ${price:.2f}\n"
                f"Stop: ${stop:.2f} | TP1: ${tp1:.2f} | TP2: ${tp2:.2f}\n"
                f"Score técnico: {score:.3f}\n"
                f"Trigger: {trigger[:120]}"
            )
            from scheduler.notifier import send_notification
            send_notification(msg)
        except Exception:
            pass
