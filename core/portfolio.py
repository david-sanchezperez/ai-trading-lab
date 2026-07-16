class Portfolio:
    def __init__(self, initial_cash=10000):
        self.cash = initial_cash
        self.positions = {}
        self.history = []

    def buy(self, ticker, price, quantity):
        cost = price * quantity
        if cost > self.cash:
            return {"status": "rejected", "reason": "Not enough cash"}
        self.cash -= cost
        if ticker not in self.positions:
            self.positions[ticker] = {"quantity": 0, "avg_price": 0}
        current_qty = self.positions[ticker]["quantity"]
        current_avg = self.positions[ticker]["avg_price"]
        new_qty = current_qty + quantity
        new_avg = ((current_qty * current_avg) + (quantity * price)) / new_qty
        self.positions[ticker]["quantity"] = new_qty
        self.positions[ticker]["avg_price"] = new_avg
        trade = {"action": "BUY", "ticker": ticker, "price": price, "quantity": quantity}
        self.history.append(trade)
        return {"status": "filled", "trade": trade}

    def sell(self, ticker, price, quantity):
        if ticker not in self.positions or self.positions[ticker]["quantity"] < quantity:
            return {"status": "rejected", "reason": "Not enough shares"}
        self.positions[ticker]["quantity"] -= quantity
        proceeds = price * quantity
        self.cash += proceeds
        trade = {"action": "SELL", "ticker": ticker, "price": price, "quantity": quantity}
        self.history.append(trade)
        if self.positions[ticker]["quantity"] == 0:
            del self.positions[ticker]
        return {"status": "filled", "trade": trade}

    def total_value(self, market_prices):
        value = self.cash
        for ticker, pos in self.positions.items():
            if ticker in market_prices:
                value += pos["quantity"] * market_prices[ticker]
        return value

    def summary(self, market_prices):
        return {
            "cash": round(self.cash, 2),
            "positions": self.positions,
            "total_value": round(self.total_value(market_prices), 2),
            "history": self.history
        }
