"""
Tracking de entry points para trailing stops y take profit.
Persiste en data/entry_points.json.

Estructura por ticker:
  entry_price    — precio medio de entrada (weighted avg si hay añadidos)
  entry_date     — fecha de la primera entrada
  atr_entry      — ATR(14) en el momento del último añadido
  peak_price     — precio máximo visto desde la entrada
  quantity       — cantidad actual rastreada
  tp1_triggered  — True si ya se ejecutó el primer take profit (50%)
"""

import json
from datetime import date, datetime

from core.config import ENTRY_POINTS_PATH


def load() -> dict:
    if not ENTRY_POINTS_PATH.exists():
        return {}
    with open(ENTRY_POINTS_PATH) as f:
        return json.load(f)


def save(entries: dict) -> None:
    ENTRY_POINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ENTRY_POINTS_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def record_entry(ticker: str, price: float, atr_14: float, quantity: int, date_str: str) -> None:
    """
    Registra o actualiza un entry point.
    Si ya existe posición, recalcula avg_entry ponderado y actualiza ATR.
    """
    entries = load()
    atr = round(atr_14, 4) if atr_14 and atr_14 > 0 else round(price * 0.035, 4)

    if ticker in entries:
        ex = entries[ticker]
        old_qty = ex["quantity"]
        new_qty = old_qty + quantity
        new_avg = (ex["entry_price"] * old_qty + price * quantity) / new_qty
        entries[ticker] = {
            "entry_price":   round(new_avg, 4),
            "entry_date":    ex["entry_date"],
            "atr_entry":     atr,
            "peak_price":    round(max(ex["peak_price"], price), 4),
            "quantity":      new_qty,
            "tp1_triggered": ex.get("tp1_triggered", False),
        }
    else:
        entries[ticker] = {
            "entry_price":   round(price, 4),
            "entry_date":    date_str,
            "atr_entry":     atr,
            "peak_price":    round(price, 4),
            "quantity":      quantity,
            "tp1_triggered": False,
        }

    save(entries)


def update_peaks(prices: dict) -> None:
    """Actualiza peak_price para todas las posiciones con precios frescos."""
    entries = load()
    changed = False
    for ticker, price in prices.items():
        if ticker in entries and price > entries[ticker]["peak_price"]:
            entries[ticker]["peak_price"] = round(price, 4)
            changed = True
    if changed:
        save(entries)


def remove_entry(ticker: str) -> None:
    entries = load()
    if ticker in entries:
        del entries[ticker]
        save(entries)


def mark_tp1_triggered(ticker: str, remaining_qty: int) -> None:
    entries = load()
    if ticker in entries:
        entries[ticker]["tp1_triggered"] = True
        entries[ticker]["quantity"] = remaining_qty
        save(entries)


def get_stop_level(entry: dict, today: date) -> float:
    """
    Stop dinámico según días en posición:
      1-5 días:  entry_price - 2×ATR  (stop fijo, protección inicial)
      6-10 días: peak_price  - 1.5×ATR (trailing desde máximo)
      11+ días:  peak_price  - 1×ATR   (trailing ajustado)
    """
    try:
        entry_date = datetime.strptime(entry["entry_date"], "%Y-%m-%d").date()
    except (ValueError, KeyError):
        entry_date = today

    days_held = (today - entry_date).days
    atr = entry["atr_entry"]

    if days_held <= 5:
        return round(entry["entry_price"] - 2.0 * atr, 4)
    elif days_held <= 10:
        return round(entry["peak_price"] - 1.5 * atr, 4)
    else:
        return round(entry["peak_price"] - 1.0 * atr, 4)


def get_tp1_level(entry: dict) -> float:
    """Take profit 1 — entry_price + 2×ATR (vender 50%)"""
    return round(entry["entry_price"] + 2.0 * entry["atr_entry"], 4)


def get_tp2_level(entry: dict) -> float:
    """Take profit 2 — entry_price + 3×ATR (vender el 50% restante)"""
    return round(entry["entry_price"] + 3.0 * entry["atr_entry"], 4)
