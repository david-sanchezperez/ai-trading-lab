"""
Sentimiento de StockTwits para tickers con alto seguimiento retail.

API pública sin clave — funciona para tickers con comunidad activa.
Útil para small caps y tickers especulativos donde el retail mueve el precio.
Para large caps institucionales (ASML, AVGO, META) el retail tiene poco impacto.
"""

import requests

# Tickers donde el retail importa: exploration + AMD (muy seguido en redes)
STOCKTWITS_ELIGIBLE = {"BEAM", "COHR", "WOLF", "RXRX", "CRSP", "MU", "CRWV", "AMD"}

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; trading-research/1.0)"}


def get_stocktwits_sentiment(ticker: str) -> dict | None:
    """
    Obtiene el sentimiento de StockTwits para un ticker elegible.

    Devuelve dict con:
      bullish_pct   — fracción de mensajes con etiqueta Bullish
      bearish_pct   — fracción de mensajes con etiqueta Bearish
      labeled       — número de mensajes con etiqueta (los sin etiqueta se ignoran)
      score         — +bullish_pct / -bearish_pct normalizado a [-1, +1]
    O None si el ticker no es eligible, no hay datos suficientes o falla la API.
    """
    if ticker not in STOCKTWITS_ELIGIBLE:
        return None

    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
        r = requests.get(url, timeout=10, headers=_HEADERS)
        if r.status_code != 200:
            return None

        messages = r.json().get("messages", [])
        if not messages:
            return None

        bullish = sum(
            1 for m in messages
            if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish"
        )
        bearish = sum(
            1 for m in messages
            if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish"
        )
        total = bullish + bearish

        if total < 3:
            return None

        bullish_pct = round(bullish / total, 3)
        bearish_pct = round(bearish / total, 3)
        score = round(bullish_pct - bearish_pct, 3)  # [-1, +1]

        return {
            "bullish_pct": bullish_pct,
            "bearish_pct": bearish_pct,
            "labeled":     total,
            "score":       score,
        }
    except Exception:
        return None
