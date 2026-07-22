"""
Insider Transactions Signal — Form 4 SEC EDGAR (edgartools).

Lógica:
  - Descarga Form 4 filings de los últimos 90 días
  - Solo cuenta compras de mercado abierto (common_stock_purchases)
  - Ignora ventas: insiders venden por muchos motivos (diversificación, impuestos)
  - Pondera por rol: CEO/CFO/President → 2.0×, resto → 1.0×
  - Bonifica cluster buying (≥3 insiders distintos comprando)
  - Resultado: [0, +0.20] — solo señal alcista

Cache: data/insider_cache.json (TTL 24h, un registro por ticker).

Requiere: pip install edgartools
"""

import json
import logging
from datetime import datetime, timedelta, date

from core.config import PROJECT_ROOT, EDGAR_IDENTITY

log = logging.getLogger(__name__)

CACHE_PATH      = PROJECT_ROOT / "data" / "cache" / "insider_cache.json"
CACHE_TTL_HOURS = 24
LOOKBACK_DAYS   = 90
MAX_SCORE       = 0.20

# Roles que pesan doble
_SENIOR_KEYWORDS = {"ceo", "cfo", "president", "chairman", "chief executive", "chief financial"}


def _is_senior(position: str) -> bool:
    p = (position or "").lower()
    return any(kw in p for kw in _SENIOR_KEYWORDS)


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
        log.warning(f"[insider_signal] No se pudo guardar caché: {e}")


def _is_fresh(entry: dict) -> bool:
    try:
        ts = datetime.fromisoformat(entry["timestamp"])
        return datetime.now() - ts < timedelta(hours=CACHE_TTL_HOURS)
    except Exception:
        return False


# ── Core ──────────────────────────────────────────────────────────────────────

def _compute_insider(ticker: str) -> float:
    """
    Calcula señal insider para un ticker.
    Devuelve float en [0, +0.20]. 0.0 si no hay compras o error.
    """
    try:
        from edgar import Company, set_identity
        set_identity(EDGAR_IDENTITY)
    except ImportError:
        log.warning("[insider_signal] edgartools no instalado — señal = 0")
        return 0.0

    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)

    try:
        company = Company(ticker)
        filings = company.get_filings(form="4").latest(20)
        if filings is None:
            return 0.0
    except Exception as e:
        log.warning(f"[insider_signal] {ticker}: error obteniendo filings: {e}")
        return 0.0

    buyer_names: set[str] = set()
    weighted_shares = 0.0
    total_buy_value = 0.0

    for filing in filings:
        try:
            if filing.filing_date < cutoff:
                continue
            obj     = filing.obj()
            buys    = obj.common_stock_purchases
            if buys.empty:
                continue

            weight = 2.0 if _is_senior(obj.position) else 1.0
            for _, row in buys.iterrows():
                shares = float(row.get("Shares", 0) or 0)
                price  = float(row.get("Price", 0) or 0)
                if shares <= 0:
                    continue
                weighted_shares += shares * weight
                total_buy_value += shares * price
                buyer_names.add(obj.insider_name or "unknown")

        except Exception as e:
            log.debug(f"[insider_signal] {ticker}: filing error: {e}")
            continue

    if not buyer_names:
        return 0.0

    n_buyers = len(buyer_names)

    # Score base proporcional al valor comprado (logarítmico, cap en $1M)
    if total_buy_value > 0:
        import math
        # $10k → 0.05,  $100k → 0.10,  $1M+ → 0.15
        base = min(0.15, math.log10(max(total_buy_value, 1)) / math.log10(1_000_000) * 0.15)
    else:
        # Solo shares (precio no disponible), señal mínima
        base = 0.05

    # Bonus cluster buying
    if n_buyers >= 3:
        base = min(base + 0.05, MAX_SCORE)
    elif n_buyers == 2:
        base = min(base + 0.02, MAX_SCORE)

    score = round(min(base, MAX_SCORE), 4)
    log.debug(f"[insider_signal] {ticker}: buyers={n_buyers} value=${total_buy_value:.0f} score={score:+.4f}")
    return score


def get_insider_contribution(ticker: str) -> float:
    """
    Devuelve contribución insider al score de decisión [0, +0.20].
    Usa caché de 24h. Nunca lanza excepción.
    """
    cache = _load_cache()
    entry = cache.get(ticker, {})

    if entry and _is_fresh(entry):
        return entry.get("score", 0.0)

    score = _compute_insider(ticker)
    cache[ticker] = {"score": score, "timestamp": datetime.now().isoformat()}
    _save_cache(cache)

    return score


def prefetch_all(tickers: list[str]) -> dict[str, float]:
    """Precarga señal insider para todos los tickers (llamar desde pre_market)."""
    cache  = _load_cache()
    now    = datetime.now()
    result = {}

    for ticker in tickers:
        entry = cache.get(ticker, {})
        if entry and _is_fresh(entry):
            result[ticker] = entry.get("score", 0.0)
            continue
        score = _compute_insider(ticker)
        cache[ticker] = {"score": score, "timestamp": now.isoformat()}
        result[ticker] = score

    _save_cache(cache)
    return result
