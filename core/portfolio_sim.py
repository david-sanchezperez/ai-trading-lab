"""
Portfolio simulado persistente.
Independiente del portfolio personal (core/portfolio.py).
El estado se serializa a JSON — nunca se pasa como objeto Python
a través de LangGraph, siempre como dict.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from core.config import PORTFOLIO_SIM_PATH, DATA_DIR, PROJECT_ROOT

INITIAL_CASH = 100_000.0

POSITIONS_TRACKING_PATH = PROJECT_ROOT / "data" / "positions_tracking.json"


# Constantes para trailing stops
TRAILING_STEPS = {
    "days_0_5": {"trailing_pct": 0.02, "name": "días 0-5"},
    "days_6_10": {"trailing_pct": 0.015, "name": "días 6-10"},
    "days_11_plus": {"trailing_pct": 0.01, "name": "días 11+"},
}
TAKE_PROFIT_LEVELS = [2, 3]  # +2×ATR, +3×ATR desde entry


def compute_quantity_atr(ticker, price, atr_14, portfolio_value=100_000.0, risk_per_trade=0.01):
    """
    Calcular cantidad basada en ATR para riesgo constante.
    
    Ejemplo:
        ATR = $3.00, portfolio = $100,000, risk_per_trade = 1%
        risk_amount = $1,000
        qty = $1,000 / $3.00 = 333 shares
    
    Args:
        ticker: ticker del activo
        price: precio actual
        atr_14: ATR(14) del ticker
        portfolio_value: valor total del portfolio (default: INITIAL_CASH)
        risk_per_trade: riesgo por trade como fracción del portfolio (default: 1%)
    
    Returns:
        quantity: número de shares a comprar (min 1, max 1000 para evitar over-concentration)
    """
    if atr_14 is None or atr_14 <= 0 or atr_14 == float('inf') or atr_14 != atr_14:
        # ATR no disponible: usar 3.5% del precio como proxy de volatilidad diaria.
        # Esto produce la misma fórmula que abajo pero con atr_proxy como denominador.
        atr_14 = price * 0.035 if price and price > 0 else 1.0
    
    risk_amount = risk_per_trade * portfolio_value
    qty = max(1, round(risk_amount / atr_14))
    
    # Limitar a 1000 shares máx para evitar over-concentration en un solo activo
    return min(qty, 1000)


def _empty_state() -> dict:
    return {
        "cash": INITIAL_CASH,
        "positions": {},
        "trades": [],
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
    }


def load() -> dict:
    """Carga el estado del portfolio simulado desde JSON.
    Si no existe, devuelve estado inicial con 10.000€ en cash."""
    if not PORTFOLIO_SIM_PATH.exists():
        return _empty_state()
    with open(PORTFOLIO_SIM_PATH) as f:
        return json.load(f)


def save(state: dict) -> None:
    """Persiste el estado del portfolio simulado a JSON."""
    PORTFOLIO_SIM_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(PORTFOLIO_SIM_PATH, "w") as f:
        json.dump(state, f, indent=2)


def buy(state: dict, ticker: str, price: float, quantity: int, date: str) -> dict:
    """
    Ejecuta una compra con sizing basado en ATR.
    
    Args:
        state: estado del portfolio
        ticker: ticker del activo
        price: precio de ejecución
        quantity: cantidad calculada (usar compute_quantity_atr para sizing ATR-based)
        date: fecha de la operación (YYYY-MM-DD)
    
    Returns:
        dict con status ('filled'/'rejected'), trade details, y estado actualizado
    """
    cost = round(price * quantity, 2)
    if cost > state["cash"]:
        return {
            "status": "rejected",
            "reason": f"Not enough cash ({state['cash']:.2f} < {cost:.2f})",
            "state": state,
        }

    state["cash"] = round(state["cash"] - cost, 2)

    if ticker not in state["positions"]:
        state["positions"][ticker] = {"quantity": 0, "avg_price": 0.0}

    pos = state["positions"][ticker]
    current_qty = pos["quantity"]
    current_avg = pos["avg_price"]
    new_qty = current_qty + quantity
    new_avg = ((current_qty * current_avg) + (quantity * price)) / new_qty

    state["positions"][ticker] = {
        "quantity": new_qty,
        "avg_price": round(new_avg, 4),
    }

    trade = {
        "action": "BUY",
        "ticker": ticker,
        "price": price,
        "quantity": quantity,
        "cost": cost,
        "date": date,
    }
    state["trades"].append(trade)

    return {"status": "filled", "trade": trade, "state": state}


def sell(state: dict, ticker: str, price: float, quantity: int, date: str) -> dict:
    """Ejecuta una venta. Devuelve dict con status y state actualizado."""
    pos = state["positions"].get(ticker)
    if not pos or pos["quantity"] < quantity:
        held = pos["quantity"] if pos else 0
        return {
            "status": "rejected",
            "reason": f"Not enough shares ({held} < {quantity})",
            "state": state,
        }

    proceeds = round(price * quantity, 2)
    state["cash"] = round(state["cash"] + proceeds, 2)
    state["positions"][ticker]["quantity"] -= quantity

    if state["positions"][ticker]["quantity"] == 0:
        del state["positions"][ticker]

    trade = {
        "action": "SELL",
        "ticker": ticker,
        "price": price,
        "quantity": quantity,
        "proceeds": proceeds,
        "date": date,
    }
    state["trades"].append(trade)

    return {"status": "filled", "trade": trade, "state": state}


def total_value(state: dict, market_prices: dict) -> float:
    """Valor total: cash + posiciones a precio de mercado."""
    value = state["cash"]
    for ticker, pos in state["positions"].items():
        price = market_prices.get(ticker, 0)
        value += pos["quantity"] * price
    return round(value, 2)


def summary(state: dict, market_prices: dict) -> dict:
    """Resumen del portfolio con PnL por posición."""
    positions_with_pnl = {}
    for ticker, pos in state["positions"].items():
        current_price = market_prices.get(ticker, 0)
        market_value = round(pos["quantity"] * current_price, 2)
        cost_basis = round(pos["quantity"] * pos["avg_price"], 2)
        pnl = round(market_value - cost_basis, 2)
        pnl_pct = round((pnl / cost_basis * 100) if cost_basis > 0 else 0, 2)
        positions_with_pnl[ticker] = {
            **pos,
            "current_price": current_price,
            "market_value": market_value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        }

    tv = total_value(state, market_prices)
    pnl_total = round(tv - INITIAL_CASH, 2)
    pnl_total_pct = round((pnl_total / INITIAL_CASH) * 100, 2)

    return {
        "cash": state["cash"],
        "positions": positions_with_pnl,
        "total_value": tv,
        "pnl_total": pnl_total,
        "pnl_total_pct": pnl_total_pct,
        "total_trades": len(state["trades"]),
        "last_updated": state.get("last_updated", ""),
    }
