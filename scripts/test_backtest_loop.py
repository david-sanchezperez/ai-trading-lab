import pandas as pd

from core.indicators import add_indicators
from core.portfolio import Portfolio
from agents.technical_agent import generate_signal
from agents.decision_agent import make_decision
from core.sentiment_store import save_sentiment


MAX_POSITION_QTY = 10
BUY_QTY = 2
SELL_QTY = 2

TAKE_PROFIT = 0.03   # +3%
STOP_LOSS = -0.02    # -2%


def main():
    ticker = "NVDA"

    df = pd.read_csv(f"data/raw/{ticker}.csv")

    portfolio = Portfolio(initial_cash=10000)

    last_action = None
    trade_count = 0

    for i in range(60, len(df)):
        df_slice = df.iloc[:i].copy()
        df_slice = add_indicators(df_slice)

        # 🔹 señal técnica
        technical = generate_signal(df_slice)

        # 🔥 sentimiento heurístico basado en precio
        recent_close = df_slice.iloc[-1]["Close"]
        prev_close = df_slice.iloc[-2]["Close"]

        change = (recent_close - prev_close) / prev_close

        # 🔥 sentimiento contrarian
        if change > 0.01:
            sentiment = {"sentiment": -0.5, "confidence": 0.5}
        elif change < -0.01:
            sentiment = {"sentiment": 0.5, "confidence": 0.5}
        else:
            sentiment = {"sentiment": 0.0, "confidence": 0.2}

        # 🔹 guardar sentimiento (memory layer)
        date = df_slice.iloc[-1]["Date"]
        save_sentiment(date, ticker, sentiment["sentiment"], sentiment["confidence"])

        # 🔹 decisión
        decision = make_decision(technical, sentiment)

        price = float(df_slice.iloc[-1]["Close"])
        current_qty = portfolio.positions.get(ticker, {}).get("quantity", 0)

        position = portfolio.positions.get(ticker)
        
        if position:
            avg_price = position["avg_price"]
            pnl_pct = (price - avg_price) / avg_price
        
            if pnl_pct >= TAKE_PROFIT:
                portfolio.sell(ticker, price, SELL_QTY)
                last_action = "SELL"
                trade_count += 1
                continue
        
            if pnl_pct <= STOP_LOSS:
                portfolio.sell(ticker, price, SELL_QTY)
                last_action = "SELL"
                trade_count += 1
                continue

        # 🔹 ejecución
        if decision["action"] == "BUY":
            if current_qty < MAX_POSITION_QTY and last_action != "BUY":
                portfolio.buy(ticker, price, BUY_QTY)
                last_action = "BUY"
                trade_count += 1

        elif decision["action"] == "SELL":
            if current_qty >= SELL_QTY:
                portfolio.sell(ticker, price, SELL_QTY)
                last_action = "SELL"
                trade_count += 1

        else:
            last_action = "HOLD"

    final_price = float(df.iloc[-1]["Close"])

    final_value = portfolio.total_value({ticker: final_price})
    pnl = final_value - 10000

    # 🔹 baseline (buy & hold)
    first_price = float(df.iloc[60]["Close"])
    shares = 10000 / first_price
    hold_value = shares * final_price
    hold_pnl = hold_value - 10000

    print("\n=== RESULTADOS ===")
    print(f"Final value: {round(final_value,2)}")
    print(f"PnL: {round(pnl,2)}")
    print(f"Trades: {trade_count}")

    print("\n=== BASELINE (BUY & HOLD) ===")
    print(f"Final value: {round(hold_value,2)}")
    print(f"PnL: {round(hold_pnl,2)}")

    print("\n=== PORTFOLIO ===")
    print(portfolio.summary({ticker: final_price}))


if __name__ == "__main__":
    main()
