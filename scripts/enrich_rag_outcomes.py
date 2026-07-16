"""
Enriquece el RAG con 3 años de situaciones históricas + outcomes reales.

Para cada ticker del universo operativo:
  1. Descarga / carga 3 años de OHLCV (data/history/)
  2. Genera señal técnica en cada fila
  3. Calcula el retorno real a 5 y 10 días
  4. Upsert en ChromaDB con outcome_5d y outcome_10d

Resultado: el critic recibe precedentes con evidencia real, ej.:
  "MRVL BUY | RSI=38 (oversold) | uptrend → +4.2% 5d, +7.1% 10d [WIN]"

Se puede re-ejecutar de forma idempotente (upsert por ticker_date).
Tras la carga inicial, update_today() en populate_rag.py gestiona
las actualizaciones incrementales.
"""

import math
from datetime import date
from pathlib import Path

import pandas as pd

from core.config import DATA_DIR
from core.data_loader import TICKERS_FLAT, get_ticker_metadata, fetch_data
from core.indicators import add_indicators, add_relative_strength
from agents.technical_agent import generate_signal
from core.rag_store import store_situation

HISTORY_DIR   = DATA_DIR.parent / "history"
HISTORY_PERIOD = "3y"
HISTORY_MAX_AGE = 7   # días antes de re-descargar
BURN_IN       = 60    # filas mínimas antes de generar señales
FORWARD_5D    = 5
FORWARD_10D   = 10


def _history_path(ticker: str) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR / f"{ticker}.csv"


def _needs_refresh(path: Path) -> bool:
    if not path.exists():
        return True
    age = (date.today() - date.fromtimestamp(path.stat().st_mtime)).days
    return age > HISTORY_MAX_AGE


def _load_history(ticker: str, spy_df: pd.DataFrame | None) -> pd.DataFrame | None:
    path = _history_path(ticker)
    if _needs_refresh(path):
        try:
            df = fetch_data(ticker, period=HISTORY_PERIOD)
            if df is None or df.empty:
                return None
            df.to_csv(path, index=False)
        except Exception as e:
            print(f"  descarga fallida {ticker}: {e}")
            if not path.exists():
                return None
            df = pd.read_csv(path)
    else:
        df = pd.read_csv(path)

    df["Date"] = pd.to_datetime(df["Date"])
    df = add_indicators(df)
    df = add_relative_strength(df, spy_df)
    return df


def _load_spy_history() -> pd.DataFrame | None:
    path = _history_path("SPY")
    if _needs_refresh(path):
        try:
            df = fetch_data("SPY", period=HISTORY_PERIOD)
            if df is None or df.empty:
                return None
            df.to_csv(path, index=False)
            return df
        except Exception:
            return None
    return pd.read_csv(path)


def process_ticker(ticker: str, spy_df: pd.DataFrame | None) -> int:
    df = _load_history(ticker, spy_df)
    if df is None or df.empty:
        return 0

    meta = get_ticker_metadata(ticker)
    stored = 0
    max_i = len(df) - FORWARD_10D - 1

    for i in range(BURN_IN, max_i):
        row = df.iloc[i]
        if any(pd.isna(row.get(c)) for c in ["RSI", "SMA_20", "SMA_50"]):
            continue

        signal_result = generate_signal(df.iloc[: i + 1])
        signal = signal_result["signal"]

        price_now = float(df.iloc[i]["Close"])
        price_5d  = float(df.iloc[i + FORWARD_5D]["Close"])
        price_10d = float(df.iloc[i + FORWARD_10D]["Close"])

        outcome_5d  = round((price_5d  - price_now) / price_now * 100, 2)
        outcome_10d = round((price_10d - price_now) / price_now * 100, 2)

        # Etiqueta legacy para compatibilidad con código existente
        if outcome_5d >= 2.0:
            outcome_label = "bullish"
        elif outcome_5d <= -2.0:
            outcome_label = "bearish"
        else:
            outcome_label = "neutral"

        indicators = {
            "rsi":          float(row["RSI"]),
            "sma20":        float(row["SMA_20"]),
            "sma50":        float(row["SMA_50"]),
            "momentum":     float(row["Momentum"]) if not pd.isna(row.get("Momentum")) else 0.0,
            "confidence":   signal_result["confidence"],
            "pct_52w":      float(row.get("pct_52w_range", 0.5)),
            "rs_spy":       float(row.get("RS_SPY", 0.0)),
            "volume_ratio": float(row.get("volume_ratio", 1.0)),
        }

        store_situation(
            ticker=ticker,
            date=row["Date"],
            indicators=indicators,
            signal=signal,
            outcome=outcome_label,
            outcome_5d=outcome_5d,
            outcome_10d=outcome_10d,
            extra_metadata={
                "thesis": meta["thesis"],
                "risk":   meta["risk"],
                "type":   meta["type"],
                "role":   meta.get("role", "exploration"),
            },
        )
        stored += 1

    return stored


def run():
    print("=" * 60)
    print("  Enriqueciendo RAG con 3 años de histórico + outcomes")
    print("=" * 60)

    spy_df = _load_spy_history()
    if spy_df is None:
        print("ERROR: no se pudo cargar SPY — abortando")
        return

    total = 0
    for ticker in TICKERS_FLAT:
        print(f"  {ticker}...", end=" ", flush=True)
        try:
            n = process_ticker(ticker, spy_df)
            total += n
            print(f"{n} situaciones")
        except Exception as e:
            print(f"ERROR: {e}")

    print()
    print(f"Total: {total} situaciones almacenadas con outcomes en ChromaDB")
    print("El critic ahora dispone de evidencia histórica real.")


if __name__ == "__main__":
    run()
