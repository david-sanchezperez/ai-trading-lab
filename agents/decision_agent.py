import logging
from datetime import datetime

from core.sentiment_store import get_recent_sentiment
from core.win_rate_store import get_win_rate_contribution
from core.earnings_surprise import get_pead_contribution
from core.insider_signal import get_insider_contribution

log = logging.getLogger(__name__)


def normalize_signal(signal: str) -> int:
    return {"BUY": 1, "HOLD": 0, "SELL": -1}.get(signal, 0)


def make_decision(technical_result: dict, sentiment_result: dict | None, ticker: str = "NVDA") -> dict:
    tech_signal = normalize_signal(technical_result.get("signal", "HOLD"))
    tech_conf   = float(technical_result.get("confidence", 0.0))

    if sentiment_result is None:
        log.warning(
            "sentiment_result missing — decision proceeding without sentiment signal",
            extra={"ticker": ticker, "timestamp": datetime.now().isoformat()},
        )
    sentiment = float((sentiment_result or {}).get("sentiment", 0.0))

    historical_sentiment = get_recent_sentiment(ticker)

    # Win rate contribution — calibración estadística histórica
    wr_contribution, wr_debug = get_win_rate_contribution(
        ticker   = ticker,
        signal   = technical_result.get("signal", "HOLD"),
        rsi      = float(technical_result.get("rsi", 50)),
        trend_up = bool(technical_result.get("trend_up", True)),
    )

    # PEAD — Post-Earnings Announcement Drift [-0.15, +0.15]
    pead_contribution   = get_pead_contribution(ticker)

    # Insider transactions — open-market purchases [0, +0.20]
    insider_contribution = get_insider_contribution(ticker)

    score  = tech_signal * tech_conf
    score += sentiment * 0.6
    score += historical_sentiment * 0.3
    score += wr_contribution
    score += pead_contribution
    score += insider_contribution

    return {
        "action":               "HOLD",   # decisión real la toma decision_node con régimen
        "confidence":           round(abs(score), 3),
        "score":                round(score, 3),
        "technical":            technical_result,
        "sentiment":            sentiment_result,
        "historical_sentiment": round(historical_sentiment, 3),
        "win_rate":             wr_debug,
        "pead":                 round(pead_contribution, 4),
        "insider":              round(insider_contribution, 4),
    }
