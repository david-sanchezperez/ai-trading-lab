"""
Portfolio tracker para cuenta IBKR.
Convierte valores USD → EUR usando tasa live (fallback: 1.10).
"""

import logging
from ib_insync import IB

from config.broker_config import IBKR_ACCOUNT
from brokers.ibkr.market_data import get_eurusd_rate

log = logging.getLogger(__name__)


def get_account_summary(ib: IB) -> dict:
    """
    Resumen de cuenta en USD y EUR.

    Returns:
        cash_usd, cash_eur, net_liq_usd, net_liq_eur, eurusd_rate
    """
    items = {item.tag: item.value for item in ib.accountSummary(IBKR_ACCOUNT)}
    eurusd = get_eurusd_rate(ib)

    cash_usd    = float(items.get("TotalCashValue", 0))
    net_liq_usd = float(items.get("NetLiquidation", 0))

    return {
        "cash_usd":    round(cash_usd, 2),
        "cash_eur":    round(cash_usd / eurusd, 2),
        "net_liq_usd": round(net_liq_usd, 2),
        "net_liq_eur": round(net_liq_usd / eurusd, 2),
        "eurusd_rate": eurusd,
    }


def get_open_positions(ib: IB) -> dict:
    """
    Posiciones abiertas en acciones USD.

    Returns:
        {ticker: {"quantity": int, "avg_price": float}}
    """
    result = {}
    for pos in ib.positions(IBKR_ACCOUNT):
        contract = pos.contract
        if contract.secType == "STK" and contract.currency == "USD":
            result[contract.symbol] = {
                "quantity":  int(pos.position),
                "avg_price": round(float(pos.avgCost), 4),
            }
    return result
