"""
Tests de `DefensiveAction.evaluate_and_act` (monitor/actions/defensive_action.py).

Foco: una posición corta (quantity<=0) nunca debe llegar a la rama CLOSE_NOW,
que ejecutaría `broker.place_order(ticker, "SELL", qty, ...)` con qty ya
negativo — el mismo bug de profundizar cortos involuntarios arreglado en
`graph/trading_graph.py._execute_ibkr` (ver tests/test_execution_ibkr.py).
"""

from datetime import date, timedelta
from unittest.mock import patch

from monitor.actions.defensive_action import DefensiveAction
from monitor.classifiers.event_classifier import ClassifiedEvent


class FakeBroker:
    def __init__(self, price=100.0):
        self.price = price
        self.orders = []

    def get_price(self, ticker):
        return self.price

    def place_order(self, ticker, action, qty, price):
        self.orders.append((ticker, action, qty, price))
        return {"status": "filled", "trade": {"action": action, "ticker": ticker, "price": price, "quantity": qty}}

    def modify_stop(self, ticker, new_stop):
        return True


def _event(ticker="ANET"):
    return ClassifiedEvent(
        type="DEFENSIVE", ticker=ticker, urgency="immediate",
        trigger="price move -1.5x ATR", recommended_action="review",
        confidence=0.85, evidence={}, timestamp=date.today().isoformat(),
    )


def _old_entry_date():
    return (date.today() - timedelta(days=5)).isoformat()


def test_short_position_is_never_closed_via_sell():
    """Posición corta (quantity<0): no debe llamar a place_order en absoluto."""
    broker = FakeBroker()
    position = {"quantity": -302, "avg_price": 185.89}

    with patch("monitor.actions.defensive_action.call_llm", return_value='{"decision": "CLOSE_NOW", "reasoning": "test"}'):
        result = DefensiveAction().evaluate_and_act(_event(), position, broker, rag_context="")

    assert broker.orders == []
    assert result == "MONITORING"


def test_flat_position_is_never_closed_via_sell():
    broker = FakeBroker()
    position = {"quantity": 0, "avg_price": 185.89}

    with patch("monitor.actions.defensive_action.call_llm", return_value='{"decision": "CLOSE_NOW", "reasoning": "test"}'):
        result = DefensiveAction().evaluate_and_act(_event(), position, broker, rag_context="")

    assert broker.orders == []
    assert result == "MONITORING"


def test_long_position_close_now_still_works():
    """Regresión: una posición larga normal sigue pudiendo cerrarse."""
    broker = FakeBroker()
    position = {"quantity": 120, "avg_price": 142.85}

    with (
        patch("monitor.actions.defensive_action.call_llm", return_value='{"decision": "CLOSE_NOW", "reasoning": "test"}'),
        patch.object(DefensiveAction, "_get_days_held", return_value=5),
    ):
        result = DefensiveAction().evaluate_and_act(_event(), position, broker, rag_context="")

    assert broker.orders == [("ANET", "SELL", 120, 100.0)]
    assert result == "CLOSED"
