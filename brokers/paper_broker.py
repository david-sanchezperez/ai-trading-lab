"""
PaperBroker: wraps core/portfolio_sim.py para BrokerMode.PAPER_LOCAL.

Mantiene la simulación actual intacta. No requiere IB Gateway.
Los stops/TPs se gestionan dinámicamente en trailing_stop_job via entry_tracker.
"""

import logging
from datetime import datetime

import core.portfolio_sim as sim
from core import entry_tracker
from brokers.base_broker import BaseBroker

log = logging.getLogger(__name__)


class PaperBroker(BaseBroker):
    """Simulación local pura. Siempre disponible como fallback."""

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def get_price(self, ticker: str) -> float | None:
        from core.config import DATA_DIR
        import pandas as pd
        csv_path = DATA_DIR / f"{ticker}.csv"
        if not csv_path.exists():
            return None
        try:
            df = pd.read_csv(csv_path, usecols=["Close"])
            return float(df["Close"].iloc[-1]) if not df.empty else None
        except Exception:
            return None

    def _state(self) -> dict:
        return sim.load()

    def get_cash(self) -> float:
        return self._state().get("cash", 0.0)

    def get_portfolio_value(self) -> float:
        state = self._state()
        value = state.get("cash", 0.0)
        for ticker, pos in state.get("positions", {}).items():
            price = self.get_price(ticker) or pos["avg_price"]
            value += pos["quantity"] * price
        return round(value, 2)

    def get_positions(self) -> dict:
        return self._state().get("positions", {})

    def place_bracket_order(self, ticker, action, qty, limit_price, stop_price, tp_price) -> dict:
        """
        Ejecuta la entrada y delega stop/TP a entry_tracker.
        El ATR se aproxima como (limit_price - stop_price) / 2.
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        state = sim.load()

        if action == "BUY":
            result = sim.buy(state, ticker, limit_price, qty, date_str)
        else:
            full_qty = state.get("positions", {}).get(ticker, {}).get("quantity", qty)
            result = sim.sell(state, ticker, limit_price, full_qty, date_str)

        if result["status"] != "filled":
            return {"status": result["status"], "reason": result.get("reason"), "order_ids": {}}

        sim.save(result["state"])

        if action == "BUY":
            atr_approx = round((limit_price - stop_price) / 2.0, 4) if stop_price < limit_price else round(limit_price * 0.035, 4)
            entry_tracker.record_entry(ticker, limit_price, atr_approx, qty, date_str)
        elif action == "SELL":
            entry_tracker.remove_entry(ticker)

        return {
            "status":    "filled",
            "order_ids": {"entry": None, "stop": None, "tp": None},
            "trade":     result["trade"],
        }

    def place_order(self, ticker, action, qty, price) -> dict:
        date_str = datetime.now().strftime("%Y-%m-%d")
        state = sim.load()
        if action == "BUY":
            result = sim.buy(state, ticker, price, qty, date_str)
        else:
            result = sim.sell(state, ticker, price, qty, date_str)
        if result["status"] == "filled":
            sim.save(result["state"])
            return {"status": "filled", "trade": result["trade"]}
        return {"status": result["status"], "reason": result.get("reason")}

    def modify_stop(self, ticker: str, new_stop: float) -> bool:
        # PAPER_LOCAL: el stop se calcula dinámicamente en trailing_stop_job.
        # No hay orden persistente que modificar.
        return True

    def cancel_order(self, order_id: int) -> bool:
        return True
