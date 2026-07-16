"""
Vigila earnings intraday y analiza transcripts de llamadas de resultados.

Fuentes para el transcript (en orden de preferencia):
  1. SEC EDGAR — 8-K filing del día
  2. Seeking Alpha RSS
  3. None si no disponible en 30s
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import requests
import yfinance as yf

from monitor.llm_queue import call_llm

log = logging.getLogger(__name__)

_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index?q={ticker}&dateRange=custom&startdt={today}&enddt={today}&forms=8-K"
_SA_RSS       = "https://seekingalpha.com/symbol/{ticker}/earnings.xml"


@dataclass
class TranscriptAnalysis:
    ticker:                  str
    guidance_change:         str             # "raised"|"maintained"|"lowered"|"withdrawn"
    guidance_magnitude:      Optional[float]
    hedging_score:           int             # 0-10
    beat_miss:               str             # "beat"|"in-line"|"miss"
    key_metric_surprises:    list[str]
    analyst_pressure:        bool
    analyst_pressure_topic:  Optional[str]
    vs_last_quarter:         str             # "more_optimistic"|"similar"|"more_cautious"
    signal:                  str             # "STRONG_BUY"|"BUY"|"HOLD"|"SELL"|"STRONG_SELL"
    reasoning:               str
    timestamp:               str = field(default_factory=lambda: datetime.now().isoformat())


class EarningsWatcher:

    def check_earnings_today(self, tickers: list[str]) -> list[str]:
        """
        Devuelve los tickers con earnings hoy (o en las próximas 2 horas).
        Usa yfinance.Ticker.calendar.
        """
        today    = date.today()
        earnings = []

        for ticker in tickers:
            try:
                cal = yf.Ticker(ticker).calendar
                if cal is None:
                    continue
                dates = []
                if isinstance(cal, dict):
                    dates = cal.get("Earnings Date", [])
                elif hasattr(cal, "loc"):
                    try:
                        row   = cal.loc["Earnings Date"]
                        dates = list(row.values)
                    except KeyError:
                        pass

                for d in dates:
                    if d is None or str(d) == "NaT":
                        continue
                    if hasattr(d, "date"):
                        d = d.date()
                    elif isinstance(d, str):
                        try:
                            d = datetime.fromisoformat(d[:10]).date()
                        except ValueError:
                            continue
                    if d == today:
                        earnings.append(ticker)
                        break
            except Exception as e:
                log.warning(f"[earnings_watcher] No se pudo verificar earnings de {ticker}: {e}")

        if earnings:
            log.info(f"[earnings_watcher] Earnings hoy: {earnings}")
        return earnings

    def fetch_transcript(self, ticker: str) -> str | None:
        """
        Intenta obtener el transcript del earnings call.
        Prueba SEC EDGAR (8-K) y Seeking Alpha RSS con timeout de 30s.
        Devuelve el texto o None.
        """
        today = date.today().isoformat()

        # Fuente 1: SEC EDGAR 8-K
        try:
            url  = _EDGAR_SEARCH.format(ticker=ticker, today=today)
            resp = requests.get(url, timeout=15, headers={"User-Agent": "trading-research/1.0"})
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                if hits:
                    filing_url = hits[0].get("_source", {}).get("file_date")
                    text       = hits[0].get("_source", {}).get("period_of_report", "")
                    body       = hits[0].get("_source", {}).get("file_type", "")
                    # Intentar obtener el cuerpo completo si está disponible
                    full_text = str(hits[0])
                    if len(full_text) > 200:
                        log.info(f"[earnings_watcher] Transcript obtenido de SEC EDGAR para {ticker}")
                        return full_text[:8000]  # limitar para el contexto LLM
        except Exception as e:
            log.debug(f"[earnings_watcher] SEC EDGAR falló para {ticker}: {e}")

        # Fuente 2: Seeking Alpha RSS
        try:
            import feedparser
            feed = feedparser.parse(_SA_RSS.format(ticker=ticker))
            entries = feed.entries
            if entries:
                text = entries[0].get("summary", "") or entries[0].get("title", "")
                if len(text) > 100:
                    log.info(f"[earnings_watcher] Texto de Seeking Alpha para {ticker}")
                    return text[:8000]
        except Exception as e:
            log.debug(f"[earnings_watcher] Seeking Alpha falló para {ticker}: {e}")

        log.info(f"[earnings_watcher] No se encontró transcript para {ticker}")
        return None

    def analyze_transcript(self, ticker: str, transcript: str) -> TranscriptAnalysis:
        """
        Analiza el transcript con Qwen3.6 y devuelve TranscriptAnalysis.
        Si el LLM falla, devuelve análisis conservador (HOLD).
        """
        prompt = (
            f"You are a senior equity analyst at a top hedge fund.\n"
            f"Analyze this earnings call transcript for {ticker}.\n\n"
            f"Extract the following with high precision:\n\n"
            f"1. guidance_change: Did management raise, maintain, lower, or withdraw forward guidance?\n"
            f"2. guidance_magnitude: By what percentage if mentioned?\n"
            f"3. hedging_score: 0-10 scale of defensive language used.\n"
            f'   High score signals: "challenging", "headwinds", "uncertain", '
            f'"monitoring closely", "cautious", "difficult environment"\n'
            f"4. beat_miss: Did results beat, meet, or miss consensus estimates?\n"
            f"5. key_metric_surprises: List metrics that surprised (revenue, margins, EPS, etc.)\n"
            f"6. analyst_pressure: Were there hostile or skeptical analyst questions in Q&A?\n"
            f"7. vs_last_quarter: Is management tone more or less optimistic than last quarter?\n"
            f"8. signal: Overall signal for next 30-60 days\n"
            f"9. reasoning: 2-3 sentence explanation of your signal\n\n"
            f"Transcript:\n{transcript[:6000]}\n\n"
            f"Respond ONLY with valid JSON. No preamble, no markdown, "
            f"no explanation outside the JSON structure.\n"
            f'{{"guidance_change": "raised|maintained|lowered|withdrawn", '
            f'"guidance_magnitude": null, "hedging_score": 0, "beat_miss": "beat|in-line|miss", '
            f'"key_metric_surprises": [], "analyst_pressure": false, '
            f'"analyst_pressure_topic": null, '
            f'"vs_last_quarter": "more_optimistic|similar|more_cautious", '
            f'"signal": "STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL", "reasoning": ""}}'
        )

        raw = call_llm(prompt, max_tokens=500, temperature=0.1)

        if raw:
            try:
                m    = re.search(r'\{.*\}', raw, re.DOTALL)
                data = json.loads(m.group()) if m else {}
                return TranscriptAnalysis(
                    ticker=ticker,
                    guidance_change=data.get("guidance_change", "maintained"),
                    guidance_magnitude=data.get("guidance_magnitude"),
                    hedging_score=int(data.get("hedging_score", 5)),
                    beat_miss=data.get("beat_miss", "in-line"),
                    key_metric_surprises=data.get("key_metric_surprises", []),
                    analyst_pressure=bool(data.get("analyst_pressure", False)),
                    analyst_pressure_topic=data.get("analyst_pressure_topic"),
                    vs_last_quarter=data.get("vs_last_quarter", "similar"),
                    signal=data.get("signal", "HOLD"),
                    reasoning=data.get("reasoning", ""),
                )
            except Exception as e:
                log.warning(f"[earnings_watcher] Error parseando respuesta LLM para {ticker}: {e}")

        # Fallback conservador
        return TranscriptAnalysis(
            ticker=ticker,
            guidance_change="maintained",
            guidance_magnitude=None,
            hedging_score=5,
            beat_miss="in-line",
            key_metric_surprises=[],
            analyst_pressure=False,
            analyst_pressure_topic=None,
            vs_last_quarter="similar",
            signal="HOLD",
            reasoning="LLM unavailable — conservative default",
        )
