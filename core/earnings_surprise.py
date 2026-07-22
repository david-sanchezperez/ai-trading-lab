"""
PEAD — Post-Earnings Announcement Drift signal.

Lógica:
  - Descarga earnings_history de yfinance (hasta 4 trimestres)
  - Calcula contribución al score basada en surprisePercent del último trimestre
  - Aplica decay temporal: señal se atenúa tras 60 días del anuncio
  - Bonifica consistencia: 2+ trimestres consecutivos de sorpresa positiva
  - Resultado: [-0.15, +0.15]

Cache: data/earnings_cache.json (TTL 24h, un registro por ticker).
"""

import json
import logging
from datetime import datetime, timedelta

import yfinance as yf

from core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "earnings_cache.json"
CACHE_TTL_HOURS = 24

# Thresholds
STRONG_SURPRISE   = 0.10   # >10%  → señal fuerte
MODERATE_SURPRISE = 0.05   # >5%   → señal moderada
WEAK_SURPRISE     = 0.02   # >2%   → señal débil
PEAD_WINDOW_DAYS  = 60     # señal activa hasta 60 días del anuncio
MAX_SCORE         = 0.15


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, default=str))
    except Exception as e:
        log.warning(f"[earnings_surprise] No se pudo guardar caché: {e}")


def _is_fresh(entry: dict) -> bool:
    try:
        ts = datetime.fromisoformat(entry["timestamp"])
        return datetime.now() - ts < timedelta(hours=CACHE_TTL_HOURS)
    except Exception:
        return False


# ── Core ──────────────────────────────────────────────────────────────────────

def _compute_pead(ticker: str) -> float:
    """
    Calcula contribución PEAD para un ticker.
    Devuelve float en [-0.15, +0.15]. 0.0 si no hay datos.
    """
    try:
        eh = yf.Ticker(ticker).earnings_history
    except Exception as e:
        log.warning(f"[earnings_surprise] {ticker}: error descargando earnings: {e}")
        return 0.0

    if eh is None or eh.empty or "surprisePercent" not in eh.columns:
        return 0.0

    eh = eh.dropna(subset=["surprisePercent"])
    if eh.empty:
        return 0.0

    # Último trimestre
    last_row   = eh.iloc[-1]
    surprise   = float(last_row["surprisePercent"])
    quarter_dt = last_row.name

    # Fecha real del anuncio: intentar earnings_dates (más preciso) antes de aproximar.
    announce_dt = None
    try:
        ed = yf.Ticker(ticker).earnings_dates
        if ed is not None and not ed.empty:
            # earnings_dates incluye fechas pasadas y futuras; coger la más reciente pasada
            now_ts   = datetime.now()
            past     = ed[ed.index <= now_ts].copy()
            if not past.empty:
                announce_dt = past.index[0].to_pydatetime()  # más reciente primero
    except Exception:
        pass

    if announce_dt is None:
        # Fallback: fin de trimestre + 35 días (mediana histórica de reporting lag)
        try:
            announce_dt = (quarter_dt + timedelta(days=35)).to_pydatetime()
        except Exception:
            announce_dt = datetime.now()

    days_since = max((datetime.now() - announce_dt).days, 0)

    if days_since > PEAD_WINDOW_DAYS:
        return 0.0

    # Decay lineal: 1.0 el día del anuncio → 0.0 en el día 60
    decay = 1.0 - (days_since / PEAD_WINDOW_DAYS)

    # Score base según magnitud de sorpresa
    if abs(surprise) >= STRONG_SURPRISE:
        base = 0.15
    elif abs(surprise) >= MODERATE_SURPRISE:
        base = 0.10
    elif abs(surprise) >= WEAK_SURPRISE:
        base = 0.05
    else:
        base = 0.0

    direction = 1.0 if surprise > 0 else -1.0

    # Bonus por consistencia (2+ trimestres con surprise del mismo signo)
    if len(eh) >= 2:
        prev_surprise = float(eh.iloc[-2]["surprisePercent"])
        if (prev_surprise > 0) == (surprise > 0):
            base = min(base * 1.3, MAX_SCORE)

    score = round(direction * base * decay, 4)
    score = max(-MAX_SCORE, min(MAX_SCORE, score))
    return score


def get_pead_contribution(ticker: str) -> float:
    """
    Devuelve contribución PEAD al score de decisión.
    Usa caché de 24h. Nunca lanza excepción.
    """
    cache = _load_cache()
    entry = cache.get(ticker, {})

    if entry and _is_fresh(entry):
        return entry.get("score", 0.0)

    score = _compute_pead(ticker)
    cache[ticker] = {"score": score, "timestamp": datetime.now().isoformat()}
    _save_cache(cache)

    log.debug(f"[earnings_surprise] {ticker}: pead={score:+.4f}")
    return score


def prefetch_all(tickers: list[str]) -> dict[str, float]:
    """Precarga PEAD para todos los tickers (llamar desde pre_market)."""
    cache  = _load_cache()
    now    = datetime.now()
    result = {}

    for ticker in tickers:
        entry = cache.get(ticker, {})
        if entry and _is_fresh(entry):
            result[ticker] = entry.get("score", 0.0)
            continue
        score = _compute_pead(ticker)
        cache[ticker] = {"score": score, "timestamp": now.isoformat()}
        result[ticker] = score

    _save_cache(cache)
    return result
