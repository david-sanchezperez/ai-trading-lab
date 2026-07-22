"""
Tests de `_execute_ibkr` (graph/trading_graph.py) — foco en el manejo de
posiciones cortas involuntarias:

  1. Una señal SELL sobre un ticker ya en corto no debe profundizar el corto
     (bug: antes se re-vendía `positions[ticker]["quantity"]`, que ya era
     negativo, extendiendo la posición corta cada vez que el ciclo repetía
     la señal SELL).
  2. Una señal BUY sobre un ticker en corto debe cubrir la posición con una
     orden de mercado simple, no abrir un bracket largo nuevo (la lógica de
     cap de exposición asume quantity positiva).

Usa un broker doble en memoria — no toca IB Gateway.
"""

from graph import trading_graph as tg


class FakeBroker:
    def __init__(self, positions=None, cash=100_000.0, portfolio_value=250_000.0):
        self.positions = positions or {}
        self.cash = cash
        self.portfolio_value = portfolio_value
        self.orders = []          # place_order calls: (ticker, action, qty, price)
        self.bracket_orders = []  # place_bracket_order calls

    def ensure_connected(self):
        return True

    def connect(self):
        return True

    def disconnect(self):
        pass

    def get_price(self, ticker):
        return 100.0

    def get_cash(self):
        return self.cash

    def get_portfolio_value(self):
        return self.portfolio_value

    def get_positions(self):
        return self.positions

    def get_pending_buy_tickers(self):
        return set()

    def place_order(self, ticker, action, qty, price):
        self.orders.append((ticker, action, qty, price))
        return {"status": "filled", "trade": {"action": action, "ticker": ticker, "price": price, "quantity": qty}}

    def place_bracket_order(self, ticker, action, qty, limit_price, stop_price, tp_price):
        self.bracket_orders.append((ticker, action, qty, limit_price, stop_price, tp_price))
        return {
            "status": "submitted",
            "order_ids": {"entry": 1, "stop": 2, "tp": 3},
            "trade": {"action": action, "ticker": ticker, "price": limit_price, "quantity": qty},
        }

    def modify_stop(self, ticker, new_stop):
        return True

    def cancel_order(self, order_id):
        return True


def _make_state(ticker, action, price=100.0):
    return {
        "ticker": ticker,
        "df": None,
        "technical_result": {
            "signal": action, "confidence": 0.75, "rsi": 50.0,
            "price": price, "atr_14": 3.5, "rs_spy": 0.0,
            "volume_ratio": 1.0, "buy_votes": 3, "sell_votes": 3,
        },
        "sentiment_result": {"sentiment": 0.0, "confidence": 0.5, "headlines": 5},
        "decision": {"action": action, "score": 0.9, "regime_adjustment": 1.0},
        "critic_result": {"approved": True},
        "portfolio_sim": None,
        "regime_adjustment": 1.0,
        "intraday_context": None,
    }


def _run(broker, ticker, action, price=100.0):
    tg.set_session_broker(broker)
    try:
        return tg._execute_ibkr(_make_state(ticker, action, price))
    finally:
        tg.clear_session_broker()


def test_sell_on_short_position_is_skipped_not_deepened():
    """No debe volver a vender: ya está en corto, no hay nada que vender."""
    broker = FakeBroker(positions={"ANET": {"quantity": -302, "avg_price": 185.89}})
    result = _run(broker, "ANET", "SELL")

    assert broker.orders == []  # ninguna orden SELL adicional
    assert result["execution_result"]["action"] == "HOLD"
    assert result["execution_result"]["trade"]["status"] == "skipped"


def test_sell_on_flat_position_is_skipped():
    broker = FakeBroker(positions={})
    result = _run(broker, "ANET", "SELL")

    assert broker.orders == []
    assert result["execution_result"]["trade"]["status"] == "skipped"


def test_sell_on_long_position_sells_full_quantity():
    broker = FakeBroker(positions={"ANET": {"quantity": 120, "avg_price": 142.85}})
    result = _run(broker, "ANET", "SELL")

    assert broker.orders == [("ANET", "SELL", 120, 100.0)]
    assert result["execution_result"]["action"] == "SELL"


def test_buy_on_short_position_covers_via_market_order():
    """BUY sobre un ticker en corto debe cubrir, no abrir un bracket largo nuevo."""
    broker = FakeBroker(positions={"ASML": {"quantity": -11, "avg_price": 1799.13}})
    result = _run(broker, "ASML", "BUY", price=1800.0)

    assert broker.orders == [("ASML", "BUY", 11, 1800.0)]
    assert broker.bracket_orders == []  # no debe abrir un bracket largo
    assert result["execution_result"]["action"] == "COVER"
    assert result["execution_result"]["trade"]["status"] == "filled"


def test_buy_on_flat_position_opens_bracket_order():
    broker = FakeBroker(positions={}, cash=200_000.0, portfolio_value=250_000.0)
    result = _run(broker, "AMD", "BUY", price=100.0)

    assert broker.orders == []
    assert len(broker.bracket_orders) == 1
    assert broker.bracket_orders[0][0] == "AMD"
    assert broker.bracket_orders[0][1] == "BUY"
