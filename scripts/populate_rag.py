from datetime import date

import pandas as pd

from core.config import DATA_DIR
from core.indicators import add_indicators, add_relative_strength
from agents.technical_agent import generate_signal
from core.rag_store import store_situation, _get_collection, COLLECTION_NAME
from core.data_loader import TICKERS_FLAT, get_ticker_metadata, fetch_data, save_data

# Fecha de incorporación al universo para tickers añadidos en Sprint 13.
# Los tickers originales no tienen fecha de entrada (None = desde siempre).
_UNIVERSE_ENTRY_DATES: dict[str, date] = {
    "VST":  date(2026, 5, 25),
    "COST": date(2026, 5, 25),
    "APP":  date(2026, 5, 25),
    "AXON": date(2026, 5, 25),
    "PANW": date(2026, 5, 25),
}


def reset_collection():
    collection, _ = _get_collection()
    from core.rag_store import _client
    _client.delete_collection(COLLECTION_NAME)
    # Forzar reinit en próxima llamada
    import core.rag_store as rag
    rag._collection = None
    print("Collection deleted — will be recreated on first store.")


def calculate_outcome(df, i):
    if i + 5 >= len(df):
        return None
    price_now = df.iloc[i]["Close"]
    price_future = df.iloc[i + 5]["Close"]
    change = (price_future - price_now) / price_now
    if change >= 0.02:
        return "bullish"
    elif change <= -0.02:
        return "bearish"
    else:
        return "neutral"


def _load_spy() -> pd.DataFrame | None:
    path = DATA_DIR / "SPY.csv"
    return pd.read_csv(path) if path.exists() else None


def process_ticker(ticker, spy_df=None):
    path = DATA_DIR / f"{ticker}.csv"
    if not path.exists():
        print(f"  SKIP {ticker} — CSV not found")
        return 0

    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = add_indicators(df)
    df = add_relative_strength(df, spy_df)
    meta = get_ticker_metadata(ticker)
    stored = 0

    for i in range(60, len(df)):
        row = df.iloc[i]

        if pd.isna(row["RSI"]) or pd.isna(row["SMA_20"]) or pd.isna(row["SMA_50"]):
            continue

        outcome = calculate_outcome(df, i)
        if outcome is None:
            continue

        df_slice = df.iloc[:i + 1].copy()
        signal_result = generate_signal(df_slice)
        signal = signal_result["signal"]

        indicators = {
            "rsi":          float(row["RSI"]),
            "sma20":        float(row["SMA_20"]),
            "sma50":        float(row["SMA_50"]),
            "momentum":     float(row["Momentum"]) if not pd.isna(row["Momentum"]) else 0.0,
            "confidence":   signal_result["confidence"],
            "pct_52w":      float(row.get("pct_52w_range", 0.5)),
            "rs_spy":       float(row.get("RS_SPY", 0.0)),
            "volume_ratio": float(row.get("volume_ratio", 1.0)),
        }

        store_situation(
            ticker=ticker,
            date=row.get("Date", str(i)),
            indicators=indicators,
            signal=signal,
            outcome=outcome,
            status="active",
            universe_entry_date=_UNIVERSE_ENTRY_DATES.get(ticker),
            extra_metadata={
                "thesis": meta["thesis"],
                "risk": meta["risk"],
                "type": meta["type"],
                "role": meta.get("role", "exploration"),
            },
        )
        stored += 1

    return stored


def populate_new_tickers(tickers: list[str]) -> dict:
    """
    Descarga CSVs y popula RAG para tickers nuevos que no tenían histórico.
    No resetea la colección — solo añade situaciones nuevas via upsert.
    """
    results = {"stored": {}, "skipped": [], "errors": {}}
    spy_df = _load_spy()

    for ticker in tickers:
        print(f"Downloading data for {ticker}...")
        try:
            df = fetch_data(ticker, period="1y")
            if df.empty:
                print(f"  SKIP {ticker} — no data returned")
                results["skipped"].append(ticker)
                continue
            save_data(df, ticker)
            print(f"  CSV saved ({len(df)} rows)")
        except Exception as e:
            print(f"  ERROR downloading {ticker}: {e}")
            results["errors"][ticker] = str(e)
            continue

        print(f"Processing RAG for {ticker}...")
        try:
            n = process_ticker(ticker, spy_df)
            results["stored"][ticker] = n
            print(f"  {n} situations stored")
        except Exception as e:
            print(f"  ERROR processing {ticker}: {e}")
            results["errors"][ticker] = str(e)

    print("\nSummary:")
    print(f"  Stored: {sum(results['stored'].values())} situations across {len(results['stored'])} tickers")
    print(f"  Skipped: {results['skipped']}")
    print(f"  Errors: {list(results['errors'].keys())}")
    return results


def update_today():
    """
    Actualización incremental: procesa solo la última fila de cada ticker.
    Usa upsert — si la situación ya existe, la sobreescribe sin duplicar.
    Llamado por el scheduler cada día en pre-market.
    """
    total = 0
    skipped = 0
    spy_df = _load_spy()

    for ticker in TICKERS_FLAT:
        path = DATA_DIR / f"{ticker}.csv"
        if not path.exists():
            skipped += 1
            continue

        df = pd.read_csv(path)
        df["Date"] = pd.to_datetime(df["Date"])
        df = add_indicators(df)
        df = add_relative_strength(df, spy_df)

        valid = df.dropna(subset=["RSI", "SMA_20", "SMA_50"])
        if valid.empty:
            skipped += 1
            continue

        row = valid.iloc[-1]
        meta = get_ticker_metadata(ticker)

        signal_result = generate_signal(df.iloc[-1:])
        signal = signal_result["signal"]

        indicators = {
            "rsi":          float(row["RSI"]),
            "sma20":        float(row["SMA_20"]),
            "sma50":        float(row["SMA_50"]),
            "momentum":     float(row["Momentum"]) if not pd.isna(row["Momentum"]) else 0.0,
            "confidence":   signal_result["confidence"],
            "pct_52w":      float(row.get("pct_52w_range", 0.5)),
            "rs_spy":       float(row.get("RS_SPY", 0.0)),
            "volume_ratio": float(row.get("volume_ratio", 1.0)),
        }

        date = row.get("Date", "unknown")

        store_situation(
            ticker=ticker,
            date=date,
            indicators=indicators,
            signal=signal,
            outcome="unknown",  # outcome real no disponible hasta T+5
            status="active",
            universe_entry_date=_UNIVERSE_ENTRY_DATES.get(ticker),
            extra_metadata={
                "thesis": meta["thesis"],
                "risk": meta["risk"],
                "type": meta["type"],
                "role": meta.get("role", "exploration"),
            },
        )
        total += 1

    print(f"RAG update: {total} situaciones actualizadas, {skipped} skipped")
    return total


def main():
    print("Resetting ChromaDB collection...")
    reset_collection()

    spy_df = _load_spy()
    total = 0
    for ticker in TICKERS_FLAT:
        print(f"Processing {ticker}...")
        n = process_ticker(ticker, spy_df)
        total += n
        print(f"  {n} situations stored")

    print(f"\nStored {total} situations")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--new-only":
        # Popula solo los tickers nuevos sin resetear la colección
        NEW_TICKERS = [
            "MRVL", "ORCL", "CRWV", "COHR", "MU",
            "CRM", "RXRX", "CRSP", "BEAM", "WOLF",
        ]
        print(f"Populating {len(NEW_TICKERS)} new tickers...")
        populate_new_tickers(NEW_TICKERS)
    else:
        # Reset completo (comportamiento original)
        print("Resetting ChromaDB collection...")
        reset_collection()

        total = 0
        for ticker in TICKERS_FLAT:
            print(f"Processing {ticker}...")
            n = process_ticker(ticker)
            total += n
            print(f"  {n} situations stored")

        print(f"\nStored {total} situations")
