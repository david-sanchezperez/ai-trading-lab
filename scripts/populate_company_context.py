"""
Pobla la colección ChromaDB 'company_context' con el contexto fundamental
de cada ticker: descripción del negocio, sector, industria y tesis temática.

Fuente: yfinance ticker.info (gratuito, sin API key).
Se puede re-ejecutar de forma idempotente (upsert por ticker ID).

El critic usa este contexto para razonar con conocimiento de lo que hace
cada empresa, no solo sus indicadores técnicos.
"""

import time

import yfinance as yf

from core.data_loader import TICKERS_FLAT, get_ticker_metadata
from core.rag_store import store_company_context


def build_context_text(ticker: str, info: dict, meta: dict) -> str:
    """Construye un texto de contexto rico para el ticker."""
    name        = info.get("longName", ticker)
    summary     = info.get("longBusinessSummary", "")
    sector      = info.get("sector", "Unknown")
    industry    = info.get("industry", "Unknown")
    market_cap  = info.get("marketCap", 0)
    employees   = info.get("fullTimeEmployees", 0)
    thesis      = meta.get("thesis", "unknown")
    risk        = meta.get("risk", "medium")

    cap_str = ""
    if market_cap > 1e12:
        cap_str = f"${market_cap/1e12:.1f}T market cap"
    elif market_cap > 1e9:
        cap_str = f"${market_cap/1e9:.1f}B market cap"

    emp_str = f"{employees:,} employees" if employees else ""

    header = f"{name} ({ticker}) — {sector} / {industry}"
    if cap_str:
        header += f" | {cap_str}"
    if emp_str:
        header += f" | {emp_str}"

    context = f"{header}\nThesis: {thesis} | Risk: {risk}\n"
    if summary:
        # Truncar a 400 caracteres para mantener el embedding focado
        context += summary[:400] + ("..." if len(summary) > 400 else "")

    return context.strip()


def run():
    print("=" * 60)
    print("  Poblando company_context en ChromaDB")
    print("=" * 60)

    stored = 0
    errors = []

    for ticker in TICKERS_FLAT:
        print(f"  {ticker}...", end=" ", flush=True)
        try:
            info = yf.Ticker(ticker).info
            if not info or "longName" not in info:
                print("sin datos")
                errors.append(ticker)
                continue

            meta = get_ticker_metadata(ticker)
            text = build_context_text(ticker, info, meta)

            store_company_context(
                ticker=ticker,
                text=text,
                metadata={
                    "sector":   info.get("sector", ""),
                    "industry": info.get("industry", ""),
                    "thesis":   meta.get("thesis", ""),
                    "risk":     meta.get("risk", ""),
                },
            )
            stored += 1
            print("OK")
            time.sleep(0.3)  # evitar rate-limiting de yfinance

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append(ticker)

    print()
    print(f"Almacenados: {stored} / {len(TICKERS_FLAT)} tickers")
    if errors:
        print(f"Errores: {errors}")


if __name__ == "__main__":
    run()
