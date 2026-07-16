"""
Clasificador de régimen macro — multiplicador continuo para critic_threshold.

Inputs:
  - VIX percentil en ventana 30d       → stress de volatilidad
  - SPY momentum 20d                   → dirección del mercado
  - US10Y rate-of-change 10d (^TNX)    → presión de yields

Output: multiplicador en [0.8, 1.3] sobre el umbral base del Critic Agent.
  - 1.0  → régimen neutral
  - >1.0 → régimen stress (señales más fuertes necesarias para actuar)
  - <1.0 → régimen tranquilo (sistema más activo)

Fallback a 1.0 si algún dato falla — silencioso con warning en log.
"""

import logging
import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)


def compute_regime_adjustment() -> float:
    """
    Computa el multiplicador de régimen macro.
    Retorna float en [0.8, 1.3]. Fallback a 1.0 si algún componente falla.
    """
    vix_adj = _safe_component(_vix_component, "^VIX")
    spy_adj = _safe_component(_spy_component, "SPY")
    us10y_adj = _safe_component(_us10y_component, "^TNX")

    raw = 1.0 + vix_adj + spy_adj + us10y_adj
    multiplier = float(np.clip(raw, 0.8, 1.3))

    logger.info(
        f"[regime] vix_adj={vix_adj:+.3f} spy_adj={spy_adj:+.3f} "
        f"us10y_adj={us10y_adj:+.3f} → multiplier={multiplier:.3f}"
    )
    return multiplier


def _safe_component(fn, ticker_name: str) -> float:
    """Ejecuta un componente con fallback a 0.0 si falla."""
    try:
        return fn()
    except Exception as e:
        logger.warning(f"[regime] {ticker_name} failed: {e} — using neutral (0.0)")
        return 0.0


def _vix_component() -> float:
    """
    VIX percentil en ventana 30d → contribución en [-0.15, +0.15].
    VIX en percentil alto (stress) → sube el multiplicador.
    Por qué percentil y no valor absoluto: VIX=20 era 'alto' en 2017,
    'tranquilo' en 2022. Contexto reciente es más informativo.
    """
    df = yf.download("^VIX", period="40d", interval="1d", progress=False)
    closes = _extract_closes(df, "^VIX", min_rows=5)

    window = closes.tail(30)
    current = float(closes.iloc[-1])
    percentile = float((window < current).mean())

    # percentile=0.5 → 0.0 | percentile=1.0 → +0.15 | percentile=0.0 → -0.15
    return (percentile - 0.5) * 0.30


def _spy_component() -> float:
    """
    SPY momentum 20d → contribución en [-0.15, +0.15].
    Momentum negativo (mercado cayendo) → sube el multiplicador (risk-off).
    """
    df = yf.download("SPY", period="30d", interval="1d", progress=False)
    closes = _extract_closes(df, "SPY", min_rows=21)

    momentum_20d = float((closes.iloc[-1] - closes.iloc[-21]) / closes.iloc[-21])
    # Invertir: momentum negativo → contribución positiva → umbral más alto
    return float(np.clip(-momentum_20d, -0.15, 0.15))


def _us10y_component() -> float:
    """
    US 10Y yield rate-of-change 10d (^TNX) → contribución en [-0.15, +0.15].
    Yields subiendo rápido → presión sobre valoraciones growth → sube umbral.
    """
    df = yf.download("^TNX", period="20d", interval="1d", progress=False)
    closes = _extract_closes(df, "^TNX", min_rows=11)

    roc_10d = float((closes.iloc[-1] - closes.iloc[-11]) / closes.iloc[-11])
    return float(np.clip(roc_10d * 1.5, -0.15, 0.15))


def _extract_closes(df, ticker_name: str, min_rows: int):
    """Extrae la serie de closes de un DataFrame yfinance. Lanza ValueError si hay pocos datos."""
    if isinstance(df.columns, __import__("pandas").MultiIndex):
        df.columns = [col[0] for col in df.columns]
    closes = df["Close"].dropna()
    if len(closes) < min_rows:
        raise ValueError(f"only {len(closes)} rows for {ticker_name}, need {min_rows}")
    return closes
