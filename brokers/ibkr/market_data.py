"""
Datos de mercado en tiempo real vía ib_insync.
"""

import logging
from ib_insync import IB, Stock

log = logging.getLogger(__name__)


def get_live_price(ib: IB, ticker: str) -> float | None:
    """
    Último precio de cierre vía yfinance (fuente principal).
    IBKR solo ejecuta órdenes — los precios vienen de yfinance.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
    except Exception as e:
        log.warning(f"[market_data] get_live_price({ticker}) yfinance error: {e}")
    return None


def get_eurusd_rate(ib: IB) -> float:
    """Tasa EUR/USD para conversión de cartera. Fallback conservador: 1.10."""
    try:
        from ib_insync import Forex
        contract = Forex("EURUSD")
        [ticker_data] = ib.reqTickers(contract)
        rate = ticker_data.marketPrice()
        if rate and rate > 0:
            return float(rate)
    except Exception:
        pass
    return 1.10
