import json
import os
import uuid
from datetime import date
from collections import defaultdict

FISCAL_PATH = "data/fiscal_log.json"

_EMPTY = {"operaciones": []}


def load_fiscal() -> dict:
    if not os.path.isfile(FISCAL_PATH):
        return _EMPTY.copy()
    with open(FISCAL_PATH, "r") as f:
        return json.load(f)


def save_fiscal(data: dict):
    os.makedirs(os.path.dirname(FISCAL_PATH), exist_ok=True)
    with open(FISCAL_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_eurusd_rate() -> float:
    try:
        import yfinance as yf
        df = yf.download("EURUSD=X", period="1d", auto_adjust=True, progress=False)
        if df.empty:
            return 1.08
        close = df["Close"]
        # yfinance returns MultiIndex → Close is a DataFrame
        val = float(close.iloc[-1, 0] if close.ndim == 2 else close.iloc[-1])
        return val if val > 0 else 1.08
    except Exception:
        return 1.08


def add_operacion(ticker: str, accion: str, cantidad: float,
                  precio_usd: float, origen: str = "manual",
                  notas: str = "") -> dict:
    eurusd     = get_eurusd_rate()
    precio_eur = precio_usd / eurusd
    total_eur  = precio_eur * cantidad

    op = {
        "id":         str(uuid.uuid4()),
        "fecha":      date.today().isoformat(),
        "ticker":     ticker.upper(),
        "accion":     accion.upper(),        # COMPRA / VENTA
        "cantidad":   int(cantidad),
        "precio_usd": precio_usd,
        "eurusd":     eurusd,
        "precio_eur": round(precio_eur, 4),
        "total_eur":  round(total_eur, 4),
        "origen":     origen,
        "notas":      notas,
    }

    data = load_fiscal()
    data["operaciones"].append(op)
    save_fiscal(data)
    return op


def get_resumen_fiscal() -> dict:
    data = load_fiscal()
    ops  = data.get("operaciones", [])

    resumen = defaultdict(lambda: {
        "total_comprado_eur": 0.0,
        "total_vendido_eur":  0.0,
        "qty_comprada":       0.0,
        "qty_vendida":        0.0,
    })

    for op in ops:
        t = op["ticker"]
        if op["accion"] == "COMPRA":
            resumen[t]["total_comprado_eur"] += op["total_eur"]
            resumen[t]["qty_comprada"]       += op["cantidad"]
        elif op["accion"] == "VENTA":
            resumen[t]["total_vendido_eur"]  += op["total_eur"]
            resumen[t]["qty_vendida"]        += op["cantidad"]

    result = {}
    for ticker, r in resumen.items():
        qty_neta    = r["qty_comprada"] - r["qty_vendida"]
        coste_medio = (r["total_comprado_eur"] / r["qty_comprada"]
                       if r["qty_comprada"] > 0 else 0.0)
        resultado   = r["total_vendido_eur"] - (r["qty_vendida"] * coste_medio)
        result[ticker] = {
            "total_comprado_eur":      round(r["total_comprado_eur"], 2),
            "total_vendido_eur":       round(r["total_vendido_eur"],  2),
            "qty_comprada":            r["qty_comprada"],
            "qty_vendida":             r["qty_vendida"],
            "qty_en_cartera":          round(qty_neta, 6),
            "coste_medio_eur":         round(coste_medio, 4),
            "resultado_realizado_eur": round(resultado, 2),
        }

    total_resultado = sum(v["resultado_realizado_eur"] for v in result.values())
    return {
        "por_ticker":          result,
        "total_operaciones":   len(ops),
        "resultado_total_eur": round(total_resultado, 2),
    }
