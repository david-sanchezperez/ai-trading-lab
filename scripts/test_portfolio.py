from core.portfolio import Portfolio

def main():
    portfolio = Portfolio(initial_cash=10000)

    print(portfolio.buy("NVDA", 175.64, 10))
    print(portfolio.buy("AMD", 105.20, 5))
    print(portfolio.sell("NVDA", 182.00, 4))

    prices = {
        "NVDA": 182.00,
        "AMD": 108.50
    }

    print("\n=== PORTFOLIO SUMMARY ===")
    print(portfolio.summary(prices))

if __name__ == "__main__":
    main()
