"""
Clasifica eventos (precio, noticias, earnings) en tipos accionables.

DEFENSIVE   — ticker en posición abierta, evento negativo urgente
OPPORTUNISTIC — ticker fuera del portfolio, evento positivo excepcional
ENRICH_EOD  — material pero no urgente, enriquece el ciclo de las 20:30
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from monitor.watchers.price_watcher import PriceAlert
from monitor.watchers.news_poller import NewsEvent
from monitor.watchers.earnings_watcher import TranscriptAnalysis

log = logging.getLogger(__name__)


@dataclass
class ClassifiedEvent:
    type:                str          # "DEFENSIVE"|"OPPORTUNISTIC"|"ENRICH_EOD"
    ticker:              str
    urgency:             str          # "immediate"|"monitor"|"eod_context"
    trigger:             str
    recommended_action:  str
    confidence:          float
    evidence:            dict
    timestamp:           str = field(default_factory=lambda: datetime.now().isoformat())


class EventClassifier:

    def classify(
        self,
        price_alerts:   list[PriceAlert],
        news_events:    list[NewsEvent],
        transcript:     Optional[TranscriptAnalysis],
        open_positions: dict,
    ) -> list[ClassifiedEvent]:

        events: list[ClassifiedEvent] = []
        all_tickers = set(
            [a.ticker for a in price_alerts]
            + [n.ticker for n in news_events]
            + ([transcript.ticker] if transcript else [])
        )

        for ticker in all_tickers:
            t_price  = [a for a in price_alerts if a.ticker == ticker]
            t_news   = [n for n in news_events  if n.ticker == ticker]
            t_script = transcript if (transcript and transcript.ticker == ticker) else None

            in_portfolio = ticker in open_positions

            # ── DEFENSIVE triggers (solo si tenemos posición) ─────────────────
            if in_portfolio:
                def_trigger   = None
                def_evidence  = {}
                def_confidence = 0.0

                # A: caída > 1.5×ATR
                bad_price = next(
                    (a for a in t_price
                     if a.direction == "DOWN" and a.atr_multiple >= 1.5),
                    None,
                )
                if bad_price:
                    def_trigger    = f"Intraday drop {bad_price.move_pct:+.2%} ({bad_price.atr_multiple:.1f}×ATR)"
                    def_evidence   = {"price_alert": bad_price.__dict__}
                    def_confidence = min(1.0, bad_price.atr_multiple / 3.0)

                # B: noticia negativa material y urgente
                bad_news = next(
                    (n for n in t_news
                     if n.is_material and n.impact == "negative" and n.urgency == "act_today"),
                    None,
                )
                if bad_news and (def_confidence < 0.7):
                    def_trigger    = f"Material negative news: {bad_news.headline[:80]}"
                    def_evidence   = {"news_event": bad_news.__dict__}
                    def_confidence = bad_news.finbert_confidence

                # C: transcript SELL/STRONG_SELL
                if t_script and t_script.signal in ("SELL", "STRONG_SELL"):
                    def_trigger    = f"Earnings signal: {t_script.signal}"
                    def_evidence   = {"transcript": t_script.__dict__}
                    def_confidence = 0.85 if t_script.signal == "STRONG_SELL" else 0.70

                if def_trigger:
                    events.append(ClassifiedEvent(
                        type="DEFENSIVE",
                        ticker=ticker,
                        urgency="immediate",
                        trigger=def_trigger,
                        recommended_action="Evaluate position closure or stop tightening",
                        confidence=round(def_confidence, 3),
                        evidence=def_evidence,
                    ))
                    log.info(f"[classifier] DEFENSIVE event: {ticker} — {def_trigger}")
                    continue  # no evaluar oportunista si ya es defensivo

            # ── OPPORTUNISTIC triggers (solo si NO tenemos posición) ──────────
            if not in_portfolio:
                opp_trigger   = None
                opp_evidence  = {}
                opp_confidence = 0.0

                # A: precio sube + noticia positiva material
                good_price = next(
                    (a for a in t_price if a.direction == "UP" and a.atr_multiple >= 1.0),
                    None,
                )
                good_news = next(
                    (n for n in t_news if n.is_material and n.impact == "positive"),
                    None,
                )
                if good_price and good_news:
                    opp_trigger    = (
                        f"Price surge {good_price.move_pct:+.2%} "
                        f"({good_price.atr_multiple:.1f}×ATR) + positive news"
                    )
                    opp_evidence   = {
                        "price_alert": good_price.__dict__,
                        "news_event":  good_news.__dict__,
                    }
                    opp_confidence = min(1.0, (good_price.atr_multiple + good_news.finbert_confidence) / 2)

                # B: transcript STRONG_BUY
                if t_script and t_script.signal == "STRONG_BUY":
                    opp_trigger    = f"Earnings transcript: STRONG_BUY"
                    opp_evidence   = {"transcript": t_script.__dict__}
                    opp_confidence = 0.90

                if opp_trigger:
                    events.append(ClassifiedEvent(
                        type="OPPORTUNISTIC",
                        ticker=ticker,
                        urgency="immediate",
                        trigger=opp_trigger,
                        recommended_action="Evaluate intraday entry if technical score > 1.30",
                        confidence=round(opp_confidence, 3),
                        evidence=opp_evidence,
                    ))
                    log.info(f"[classifier] OPPORTUNISTIC event: {ticker} — {opp_trigger}")
                    continue

            # ── ENRICH_EOD — cualquier evento material no urgente ─────────────
            has_material = (
                any(n.is_material for n in t_news)
                or (t_script is not None)
                or any(a.atr_multiple >= 0.5 for a in t_price)
            )
            if has_material:
                events.append(ClassifiedEvent(
                    type="ENRICH_EOD",
                    ticker=ticker,
                    urgency="eod_context",
                    trigger="Material event for EOD context",
                    recommended_action="Include in 20:30 cycle analysis",
                    confidence=0.5,
                    evidence={
                        "price_alerts": [a.__dict__ for a in t_price],
                        "news_events":  [n.__dict__ for n in t_news],
                        "transcript":   t_script.__dict__ if t_script else None,
                    },
                ))

        return events
