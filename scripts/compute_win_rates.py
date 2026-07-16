"""
Computa win rates históricos por setup para cada ticker.

Setup bucket: {signal}_{trend}_{rsi_zone}
  - signal:   BUY | SELL | HOLD
  - trend:    uptrend | downtrend
  - rsi_zone: oversold (<35) | neutral (35-60) | overbought (>60)

"Win" = la señal fue direccionalmente correcta a 5 días:
  BUY  win → precio subió ≥ 2% en 5 días
  SELL win → precio bajó  ≥ 2% en 5 días
  HOLD win → precio se mantuvo dentro de ±2%

Recency weighting: situaciones más recientes pesan más.
  half_life = 365 días → dato de hace 1 año pesa 0.5x, hace 2 años 0.25x.

Historia: data/history/{ticker}.csv (3 años). Se descarga si no existe
o si tiene más de 7 días de antigüedad. Los CSV de señales activos
(data/raw/) siguen siendo de 1 año — no se mezclan.

Resultado guardado en data/win_rates.json.
Se ejecuta en pre_market tras descargar datos frescos.
"""

import json
import math
from datetime import date

import pandas as pd

from core.config import DATA_DIR
from core.data_loader import TICKERS_FLAT, fetch_data
from core.indicators import add_indicators, add_relative_strength
from agents.technical_agent import generate_signal

WIN_RATES_PATH  = DATA_DIR / "win_rates.json"
HISTORY_DIR     = DATA_DIR.parent / "data" / "history"
WIN_THRESHOLD   = 0.02    # ±2% para considerar "direccionalmente correcto"
FORWARD_DAYS    = 5       # horizonte de predicción
MIN_ROWS_BURN   = 60      # filas de burn-in antes de empezar
HISTORY_PERIOD  = "3y"    # período para descarga de historia larga
HISTORY_MAX_AGE = 7       # días antes de re-descargar historia


def _history_path(ticker: str):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return HISTORY_DIR / f"{ticker}.csv"


def _needs_refresh(path) -> bool:
    if not path.exists():
        return True
    age = (date.today() - date.fromtimestamp(path.stat().st_mtime)).days
    return age > HISTORY_MAX_AGE


def _load_history(ticker: str, spy_df: pd.DataFrame | None) -> pd.DataFrame | None:
    path = _history_path(ticker)
    if _needs_refresh(path):
        try:
            df = fetch_data(ticker, period=HISTORY_PERIOD)
            if df.empty:
                return None
            df.to_csv(path, index=False)
        except Exception as e:
            print(f"(descarga fallida {ticker}: {e})", end=" ")
            if not path.exists():
                return None
            df = pd.read_csv(path)
    else:
        df = pd.read_csv(path)

    df["Date"] = pd.to_datetime(df["Date"])
    df = add_indicators(df)
    df = add_relative_strength(df, spy_df)
    return df


def _recency_weight(row_date, today: date, half_life: int = 365) -> float:
    """Decaimiento exponencial. Dato de hace `half_life` días pesa 0.5."""
    try:
        d = pd.to_datetime(row_date).date()
        days_ago = (today - d).days
        return math.exp(-0.693 * days_ago / half_life)
    except Exception:
        return 1.0


def _rsi_zone(rsi: float) -> str:
    if rsi < 35:
        return "oversold"
    if rsi < 60:
        return "neutral"
    return "overbought"


def _setup_key(signal: str, trend_up: bool, rsi: float) -> str:
    trend = "uptrend" if trend_up else "downtrend"
    return f"{signal}_{trend}_{_rsi_zone(rsi)}"


def _is_win(signal: str, ret: float) -> bool:
    if signal == "BUY":
        return ret >= WIN_THRESHOLD
    if signal == "SELL":
        return ret <= -WIN_THRESHOLD
    return abs(ret) <= WIN_THRESHOLD


def process_ticker(ticker: str, spy_df: pd.DataFrame | None) -> dict:
    df = _load_history(ticker, spy_df)
    if df is None or df.empty:
        return {}

    today = date.today()
    # Acumuladores por bucket: weighted wins, total weight, weighted returns
    stats: dict[str, dict] = {}

    for i in range(MIN_ROWS_BURN, len(df) - FORWARD_DAYS):
        row = df.iloc[i]

        if pd.isna(row["RSI"]) or pd.isna(row["SMA_20"]) or pd.isna(row["SMA_50"]):
            continue

        single = df.iloc[i : i + 1]
        result   = generate_signal(single)
        signal   = result["signal"]
        rsi      = result["rsi"]
        trend_up = result["trend_up"]

        price_now    = float(df.iloc[i]["Close"])
        price_future = float(df.iloc[i + FORWARD_DAYS]["Close"])
        ret = (price_future - price_now) / price_now

        w = _recency_weight(row.get("Date", ""), today)
        key = _setup_key(signal, trend_up, rsi)

        if key not in stats:
            stats[key] = {"w_wins": 0.0, "w_total": 0.0, "w_ret": 0.0, "n": 0}

        stats[key]["w_wins"]  += w * float(_is_win(signal, ret))
        stats[key]["w_total"] += w
        stats[key]["w_ret"]   += w * ret
        stats[key]["n"]       += 1

    result_buckets = {}
    for key, s in stats.items():
        wt = s["w_total"]
        if wt == 0:
            continue
        result_buckets[key] = {
            "win_rate":   round(s["w_wins"] / wt, 3),
            "n":          s["n"],
            "avg_return": round(s["w_ret"] / wt, 4),
        }

    return result_buckets


def _load_spy_history() -> pd.DataFrame | None:
    path = _history_path("SPY")
    if _needs_refresh(path):
        try:
            df = fetch_data("SPY", period=HISTORY_PERIOD)
            if df.empty:
                return None
            df.to_csv(path, index=False)
            return df
        except Exception:
            return None
    return pd.read_csv(path)


def compute_and_save() -> dict:
    spy_df = _load_spy_history()

    all_results = {}
    for ticker in TICKERS_FLAT:
        print(f"  {ticker}...", end=" ", flush=True)
        try:
            buckets = process_ticker(ticker, spy_df)
            all_results[ticker] = buckets
            n_situations = sum(b["n"] for b in buckets.values())
            print(f"{len(buckets)} buckets, {n_situations} situaciones")
        except Exception as e:
            print(f"ERROR: {e}")
            all_results[ticker] = {}

    WIN_RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WIN_RATES_PATH, "w") as f:
        json.dump(all_results, f, indent=2)

    total = sum(s for t in all_results.values() for s in (b["n"] for b in t.values()))
    print(f"\nWin rates guardados en {WIN_RATES_PATH}")
    print(f"Total: {total} situaciones procesadas")
    return all_results


if __name__ == "__main__":
    print("Calculando win rates históricos (3 años, recency weighting)...")
    compute_and_save()
