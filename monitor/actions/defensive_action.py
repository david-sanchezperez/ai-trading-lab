"""
Evalúa y ejecuta acciones defensivas cuando se detecta un riesgo
en una posición abierta.

Llama a Qwen3.6 con contexto completo (posición, evento, RAG, régimen).
Ejecuta la decisión via IBKRBroker y notifica por Telegram.
"""

import json
import logging
import re
from datetime import date, datetime

from monitor.classifiers.event_classifier import ClassifiedEvent
from monitor.llm_queue import call_llm
from config.monitor_config import MONITOR_CONFIG

log = logging.getLogger(__name__)

_DEF_CFG = MONITOR_CONFIG["defensive"]


class DefensiveAction:

    def evaluate_and_act(
        self,
        event:       ClassifiedEvent,
        position:    dict,         # {"quantity": int, "avg_price": float}
        broker,                    # BaseBroker
        rag_context: str = "",
    ) -> str:
        """
        Evalúa la posición ante el evento y ejecuta si es necesario.
        Devuelve: "CLOSED" | "STOP_TIGHTENED" | "MONITORING"
        """
        ticker    = event.ticker
        qty       = position.get("quantity", 0)
        avg_cost  = position.get("avg_price", 0.0)

        # Precio actual
        current_price = broker.get_price(ticker) or avg_cost
        pnl_pct       = (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0.0

        # Días en posición (desde entry_tracker si disponible)
        days_held = self._get_days_held(ticker)

        # Stop actual desde local_state del broker (si es IBKRBroker)
        stop_price = self._get_current_stop(broker, ticker, avg_cost)

        # ATR14 para el ticker
        atr = self._get_atr(ticker, current_price)

        # Régimen macro
        try:
            from core.market_regime import compute_regime_adjustment
            regime = compute_regime_adjustment()
        except Exception:
            regime = 1.0

        # ── Safety rule: no cerrar en menos de 1 día, salvo PnL < -5% ────────
        if days_held < _DEF_CFG["min_position_days"] and pnl_pct > -5.0:
            log.info(
                f"[defensive] {ticker}: posición < {_DEF_CFG['min_position_days']}d "
                f"y PnL {pnl_pct:.1f}% — no se actúa (safety rule)"
            )
            return "MONITORING"

        # ── Prompt a Qwen3.6 ──────────────────────────────────────────────────
        prompt = (
            f"You are a risk manager reviewing an open position.\n\n"
            f"Open position: {qty} shares of {ticker}\n"
            f"Average cost: ${avg_cost:.2f} | Current price: ${current_price:.2f}\n"
            f"Unrealized PnL: {pnl_pct:+.1f}% | Days held: {days_held}\n"
            f"Current stop: ${stop_price:.2f}\n\n"
            f"Event detected: {event.trigger}\n"
            f"Evidence: {json.dumps(event.evidence, default=str)[:400]}\n\n"
        )
        if rag_context:
            prompt += f"Similar historical situations:\n{rag_context}\n\n"
        prompt += (
            f"Current market regime multiplier: {regime:.2f}\n\n"
            f"Decision options:\n"
            f"- CLOSE_NOW: exit immediately, thesis is broken\n"
            f"- TIGHTEN_STOP: raise stop but hold position\n"
            f"- HOLD_MONITOR: no action, event is noise\n\n"
            f"Respond ONLY with valid JSON. No preamble, no markdown, "
            f"no explanation outside the JSON structure.\n"
            f'{{"decision": "CLOSE_NOW|TIGHTEN_STOP|HOLD_MONITOR", '
            f'"new_stop_price": null, "reasoning": "one sentence"}}'
        )

        raw = call_llm(prompt, max_tokens=150, temperature=0.1)
        decision_data = self._parse_decision(raw, current_price, stop_price, atr)

        decision   = decision_data["decision"]
        new_stop   = decision_data.get("new_stop_price")
        reasoning  = decision_data.get("reasoning", "")

        log.info(f"[defensive] {ticker}: decisión={decision} | {reasoning}")

        # ── Ejecutar decisión ─────────────────────────────────────────────────
        if decision == "CLOSE_NOW":
            result = broker.place_order(ticker, "SELL", qty, current_price)
            if result.get("status") in ("filled", "submitted"):
                from core import entry_tracker
                entry_tracker.remove_entry(ticker)
                self._notify_close(ticker, qty, current_price, avg_cost, reasoning)
                return "CLOSED"

        elif decision == "TIGHTEN_STOP" and new_stop:
            tightened = max(new_stop, current_price - 0.75 * atr)
            ok = broker.modify_stop(ticker, tightened)
            if ok:
                self._notify_tighten(ticker, stop_price, tightened, reasoning)
                return "STOP_TIGHTENED"

        # HOLD_MONITOR o fallback
        self._notify_monitor(ticker, event.trigger, reasoning)
        return "MONITORING"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_days_held(self, ticker: str) -> int:
        try:
            from core import entry_tracker
            entries = entry_tracker.load()
            if ticker in entries:
                entry_date = datetime.strptime(
                    entries[ticker]["entry_date"], "%Y-%m-%d"
                ).date()
                return (date.today() - entry_date).days
        except Exception:
            pass
        return 0

    def _get_current_stop(self, broker, ticker: str, fallback: float) -> float:
        try:
            local = getattr(broker, "_local_state", {}).get(ticker, {})
            return float(local.get("stop_price", fallback * 0.93))
        except Exception:
            return fallback * 0.93

    def _get_atr(self, ticker: str, current_price: float) -> float:
        try:
            from core.config import DATA_DIR
            import pandas as pd
            csv = DATA_DIR / f"{ticker}.csv"
            if csv.exists():
                df   = pd.read_csv(csv, usecols=["High", "Low", "Close"])
                prev = df["Close"].shift(1)
                tr   = pd.concat([
                    df["High"] - df["Low"],
                    (df["High"] - prev).abs(),
                    (df["Low"]  - prev).abs(),
                ], axis=1).max(axis=1)
                atr = float(tr.rolling(14).mean().iloc[-1])
                if atr > 0:
                    return atr
        except Exception:
            pass
        return current_price * 0.035

    def _parse_decision(
        self, raw: str | None, current: float, stop: float, atr: float
    ) -> dict:
        if raw:
            try:
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    data = json.loads(m.group())
                    return {
                        "decision":       data.get("decision", "HOLD_MONITOR"),
                        "new_stop_price": data.get("new_stop_price"),
                        "reasoning":      data.get("reasoning", ""),
                    }
            except Exception:
                pass
        # Fallback: tighten stop 0.75×ATR si LLM no disponible
        return {
            "decision":       "TIGHTEN_STOP",
            "new_stop_price": round(current - 0.75 * atr, 4),
            "reasoning":      "LLM unavailable — conservative stop tighten",
        }

    def _notify_close(self, ticker, qty, price, avg_cost, reasoning):
        try:
            pnl     = (price - avg_cost) * qty
            pnl_pct = (price - avg_cost) / avg_cost * 100
            from notifications.telegram_notifier import notify_stop_hit
            # Reutilizamos notify_stop_hit para cierre defensivo
            msg = (
                f"🛡 *CIERRE DEFENSIVO* — {ticker}\n"
                f"Vendidas: {qty} @ ${price:.2f}\n"
                f"PnL: ${pnl:+,.0f} ({pnl_pct:+.1f}%)\n"
                f"Motivo: {reasoning}"
            )
            from scheduler.notifier import send_notification
            send_notification(msg)
        except Exception:
            pass

    def _notify_tighten(self, ticker, old_stop, new_stop, reasoning):
        try:
            msg = (
                f"🔧 *STOP AJUSTADO* — {ticker}\n"
                f"Stop: ${old_stop:.2f} → ${new_stop:.2f}\n"
                f"Motivo: {reasoning}"
            )
            from scheduler.notifier import send_notification
            send_notification(msg)
        except Exception:
            pass

    def _notify_monitor(self, ticker, trigger, reasoning):
        try:
            msg = (
                f"👁 *MONITORIZANDO* — {ticker}\n"
                f"Evento: {trigger}\n"
                f"Acción: ninguna — {reasoning}"
            )
            from scheduler.notifier import send_notification
            send_notification(msg)
        except Exception:
            pass
