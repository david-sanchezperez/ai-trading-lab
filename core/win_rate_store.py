"""
Carga win_rates.json y expone get_win_rate_contribution() para decision_agent.

La contribución al score sigue la misma lógica que los otros componentes:
  - Positiva → refuerza la señal actual
  - Negativa → la debilita
  - Magnitud  → proporcional al edge histórico y al tamaño de muestra

Peso máximo: 0.40 (cuando n ≥ 30 y edge histórico es máximo).
Se escala por min(1, n/30) para no dar peso pleno a buckets con pocas muestras.
"""

import json
from core.config import DATA_DIR

WIN_RATES_PATH = DATA_DIR / "win_rates.json"
MAX_WEIGHT     = 0.40
MIN_SAMPLES    = 5    # ignorar buckets con < 5 muestras
FULL_WEIGHT_AT = 30   # peso pleno a partir de 30 muestras

_cache: dict | None = None
_cache_mtime: float = 0.0


def _load() -> dict:
    global _cache, _cache_mtime
    if WIN_RATES_PATH.exists():
        mtime = WIN_RATES_PATH.stat().st_mtime
        if _cache is None or mtime > _cache_mtime:
            with open(WIN_RATES_PATH) as f:
                _cache = json.load(f)
            _cache_mtime = mtime
    elif _cache is None:
        _cache = {}
    return _cache


def _rsi_zone(rsi: float) -> str:
    if rsi < 35:
        return "oversold"
    if rsi < 60:
        return "neutral"
    return "overbought"


def _setup_key(signal: str, trend_up: bool, rsi: float) -> str:
    trend = "uptrend" if trend_up else "downtrend"
    return f"{signal}_{trend}_{_rsi_zone(rsi)}"


def get_win_rate_contribution(
    ticker: str,
    signal: str,
    rsi: float,
    trend_up: bool,
) -> tuple[float, dict]:
    """
    Devuelve (contribution, debug_info).

    contribution: float añadido al score en decision_agent.
      Rango aproximado: [-0.40, +0.40]
      Positivo → refuerza la señal; Negativo → la debilita.

    debug_info: dict con win_rate, n, avg_return para logging/UI.
    """
    data = _load()
    ticker_data = data.get(ticker, {})

    key = _setup_key(signal, trend_up, rsi)
    bucket = ticker_data.get(key)

    empty = {"win_rate": None, "n": 0, "avg_return": None, "key": key}

    if not bucket or bucket["n"] < MIN_SAMPLES:
        return 0.0, empty

    n         = bucket["n"]
    win_rate  = bucket["win_rate"]
    avg_ret   = bucket["avg_return"]

    # Edge: cuánto se desvía la win_rate del 50% aleatorio
    # win_rate=0.70 → edge=+0.40; win_rate=0.30 → edge=-0.40
    edge = (win_rate - 0.5) * 2   # [-1, +1]

    # Escala por tamaño de muestra (confianza estadística)
    sample_weight = min(1.0, n / FULL_WEIGHT_AT)

    # Dirección: el edge ya está calculado para la señal actual
    # (win_rate de BUY = % que subió, de SELL = % que bajó)
    # → no hace falta multiplicar por tech_signal
    contribution = edge * MAX_WEIGHT * sample_weight

    debug = {
        "win_rate":   win_rate,
        "n":          n,
        "avg_return": avg_ret,
        "key":        key,
        "edge":       round(edge, 3),
        "weight":     round(sample_weight, 2),
        "contribution": round(contribution, 4),
    }

    return round(contribution, 4), debug
