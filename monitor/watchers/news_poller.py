"""
Monitoriza noticias de Yahoo Finance RSS durante las horas de mercado.

Filtra solo noticias de las últimas 2 horas para evitar re-procesar.
Clasifica con FinBERT; si abs(score) > 0.80, llama a Qwen3.6 para
determinar materialidad y urgencia.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import feedparser

from config.monitor_config import MONITOR_CONFIG
from monitor.llm_queue import call_llm

log = logging.getLogger(__name__)

_NEWS_CFG   = MONITOR_CONFIG["defensive"]
_CONFIDENCE = _NEWS_CFG["news_confidence_threshold"]


@dataclass
class NewsEvent:
    ticker:            str
    headline:          str
    finbert_score:     float
    finbert_confidence: float
    is_material:       bool
    impact:            str      # "positive" | "negative" | "neutral"
    urgency:           str      # "act_today" | "wait_eod" | "ignore"
    llm_reasoning:     str
    timestamp:         str = field(default_factory=lambda: datetime.now().isoformat())


class NewsPoller:

    def __init__(self):
        self._seen: set[str] = set()   # hashes de headlines ya procesados
        self._finbert = None

    def _get_finbert(self):
        if self._finbert is None:
            from core.news_fetcher import _load_finbert
            self._finbert = _load_finbert()
        return self._finbert

    def _score_headline(self, headline: str) -> tuple[float, float]:
        """Devuelve (sentiment_score, confidence) de FinBERT."""
        try:
            pipe = self._get_finbert()
            result = pipe(headline[:512])[0]
            label = result["label"].lower()
            conf  = float(result["score"])
            score = conf if label == "positive" else (-conf if label == "negative" else 0.0)
            return round(score, 4), round(conf, 4)
        except Exception as e:
            log.warning(f"[news_poller] Error FinBERT: {e}")
            return 0.0, 0.0

    def _analyze_with_llm(self, headline: str, ticker: str) -> dict:
        """
        Analiza el headline con Qwen3.6 para determinar materialidad y urgencia.
        Devuelve dict con is_material, impact, urgency, reasoning.
        Fallback a valores conservadores si el LLM falla.
        """
        prompt = (
            f'You are a senior financial analyst.\n'
            f'Analyze this headline for {ticker}:\n'
            f'"{headline}"\n\n'
            f'Determine:\n'
            f'1. Is this a material event that changes the investment thesis?\n'
            f'2. Expected price impact: positive / negative / neutral\n'
            f'3. Urgency: act_today / wait_eod / ignore\n\n'
            f'act_today: requires position change before market close\n'
            f'wait_eod: relevant for EOD cycle analysis\n'
            f'ignore: noise, already priced in, or irrelevant\n\n'
            f'Respond ONLY with valid JSON. No preamble, no markdown, '
            f'no explanation outside the JSON structure.\n'
            f'{{"is_material": true/false, "impact": "positive|negative|neutral", '
            f'"urgency": "act_today|wait_eod|ignore", "reasoning": "one sentence explanation"}}'
        )

        raw = call_llm(prompt, max_tokens=150, temperature=0.1)

        if raw:
            import json, re
            try:
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    data = json.loads(m.group())
                    return {
                        "is_material": bool(data.get("is_material", False)),
                        "impact":      data.get("impact",   "neutral"),
                        "urgency":     data.get("urgency",  "ignore"),
                        "reasoning":   data.get("reasoning", ""),
                    }
            except Exception:
                pass

        # Fallback: inferir desde score FinBERT
        return {"is_material": False, "impact": "neutral",
                "urgency": "ignore", "reasoning": "LLM unavailable — using FinBERT only"}

    def poll(self, tickers: list[str]) -> list[NewsEvent]:
        """
        Descarga RSS de Yahoo Finance para cada ticker.
        Solo procesa headlines de las últimas 2 horas.
        Evita duplicados via hash de headline.
        """
        events: list[NewsEvent] = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

        for ticker in tickers:
            try:
                url  = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
                feed = feedparser.parse(url)

                for entry in feed.entries[:10]:
                    headline = entry.get("title", "").strip()
                    if not headline:
                        continue

                    # Filtro de antigüedad
                    published = entry.get("published_parsed")
                    if published:
                        pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            continue

                    # Deduplicación
                    key = hashlib.md5(f"{ticker}:{headline}".encode()).hexdigest()
                    if key in self._seen:
                        continue
                    self._seen.add(key)

                    score, conf = self._score_headline(headline)

                    if abs(score) >= _CONFIDENCE:
                        llm_result = self._analyze_with_llm(headline, ticker)
                    else:
                        # Score bajo → ignorar, sin gasto de LLM
                        llm_result = {
                            "is_material": False,
                            "impact":      "positive" if score > 0 else ("negative" if score < 0 else "neutral"),
                            "urgency":     "ignore",
                            "reasoning":   f"FinBERT score {score:+.3f} below threshold",
                        }

                    events.append(NewsEvent(
                        ticker=ticker,
                        headline=headline,
                        finbert_score=score,
                        finbert_confidence=conf,
                        is_material=llm_result["is_material"],
                        impact=llm_result["impact"],
                        urgency=llm_result["urgency"],
                        llm_reasoning=llm_result["reasoning"],
                    ))

            except Exception as e:
                log.warning(f"[news_poller] Error procesando noticias de {ticker}: {e}")

        return events
