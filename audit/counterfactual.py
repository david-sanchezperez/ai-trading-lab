"""
Resolución de outcomes contrafactuales para el audit trail del analista crítico.

Contexto: el audit trail (logs/decision_audit/) registra ~21 evaluaciones/día.
La mayoría no acaban en trade (la señal era HOLD, o el critic la desafió, o los
guardrails la vetaron). Para medir si el critic añade valor, necesitamos saber
qué habría pasado si la señal propuesta se hubiera seguido.

Estrategia por evaluación:
  1. Si existe un trade ejecutado ese día para ese ticker (ledger DuckDB), usar los
     outcomes ya resueltos por OutcomeTracker (win/return T+1/T+5/T+20).
  2. Si no hay trade, calcular el retorno contrafactual:
     - proposal.signal == "BUY":  return = (future - signal_price) / signal_price
     - proposal.signal == "SELL": return = (signal_price - future) / signal_price
     - proposal.signal == "HOLD": skip (sin dirección implícita, no medible)

Almacenamiento: data/audit_outcomes.json
  dict {eval_id → {t1: {return, win, price, ts_resolved, source}, t5: ..., t20: ...}}

  source puede ser:
    "ledger"           — del DuckDB, trade real ejecutado
    "counterfactual"   — calculado aquí, sin trade
    "ledger_missing"   — el ledger no tiene outcomes resueltos aún

Resolución perezosa: resolve_all() solo resuelve horizontes que aún no están en el
dict. Seguro para ejecutar múltiples veces (idempotente).

No imports del pipeline de trading — solo ledger y yfinance vía get_price_on_date.
"""

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from core.config import PROJECT_ROOT
from analytics.prediction_ledger import get_price_on_date

log = logging.getLogger(__name__)

OUTCOMES_PATH = PROJECT_ROOT / "data" / "audit_outcomes.json"
HORIZONS = [
    ("t1",  1),
    ("t5",  5),
    ("t20", 20),
]
MIN_BUSINESS_DAYS_BUFFER = 2  # días extra de margen sobre el horizonte nominal

_lock = threading.Lock()


# ── Almacenamiento ────────────────────────────────────────────────────────────

def _load_outcomes() -> dict:
    if OUTCOMES_PATH.exists():
        try:
            return json.loads(OUTCOMES_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning(f"[counterfactual] No se pudo leer {OUTCOMES_PATH}: {exc}")
    return {}


def _save_outcomes(outcomes: dict) -> None:
    OUTCOMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTCOMES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUTCOMES_PATH)


def load_outcomes() -> dict:
    """Devuelve el dict completo de outcomes resueltos (eval_id → horizontes)."""
    with _lock:
        return _load_outcomes()


# ── Cálculo del retorno contrafactual ────────────────────────────────────────

def _counterfactual_return(
    signal: Optional[str],
    signal_price: float,
    future_price: float,
) -> Optional[float]:
    """
    Retorno desde la perspectiva de haber seguido la señal propuesta.
    None si signal == "HOLD" (sin dirección implícita) o datos inválidos.
    """
    if not signal or not signal_price or signal_price <= 0:
        return None
    if signal == "BUY":
        return (future_price - signal_price) / signal_price
    if signal == "SELL":
        return (signal_price - future_price) / signal_price
    # HOLD — no hay apuesta direccional; el caller descarta este eval para análisis
    return None


def _eval_ts_to_date(ts_str: str) -> Optional[date]:
    """Parsea el timestamp ISO8601 del audit trail a date."""
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _is_resolvable(eval_date: date, days: int, today: Optional[date] = None) -> bool:
    """True si han pasado suficientes días naturales (+ buffer) desde la evaluación."""
    today = today or date.today()
    return (today - eval_date).days >= days + MIN_BUSINESS_DAYS_BUFFER


# ── Resolución desde el ledger DuckDB ────────────────────────────────────────

def _fetch_ledger_outcomes(ticker: str, eval_date_str: str) -> Optional[dict]:
    """
    Busca en predictions el trade ejecutado ese día para ese ticker.
    Devuelve dict de horizontes o None si no hay trade ese día.
    """
    try:
        from analytics.prediction_ledger import DB_PATH
        import duckdb

        join_key_prefix = f"{ticker}_{eval_date_str.replace('-', '')}"
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            row = con.execute(
                """SELECT
                       return_t1, win_t1, price_t1, resolved_t1,
                       return_t5, win_t5, price_t5, resolved_t5,
                       return_t20, win_t20, price_t20, resolved_t20
                   FROM predictions
                   WHERE id LIKE ?
                     AND action = 'BUY'
                   ORDER BY timestamp
                   LIMIT 1""",
                [f"{join_key_prefix}%"],
            ).fetchone()
        finally:
            con.close()

        if row is None:
            return None

        (ret1, win1, p1, res1,
         ret5, win5, p5, res5,
         ret20, win20, p20, res20) = row

        result = {}
        if res1:
            result["t1"] = {"return": ret1, "win": bool(win1), "price": p1,
                            "ts_resolved": None, "source": "ledger"}
        if res5:
            result["t5"] = {"return": ret5, "win": bool(win5), "price": p5,
                            "ts_resolved": None, "source": "ledger"}
        if res20:
            result["t20"] = {"return": ret20, "win": bool(win20), "price": p20,
                              "ts_resolved": None, "source": "ledger"}
        return result if result else {"_ledger_found": True}

    except Exception as exc:
        log.debug(f"[counterfactual] Ledger lookup {ticker}/{eval_date_str}: {exc}")
        return None


# ── Resolvedor principal ──────────────────────────────────────────────────────

def resolve_all(
    evaluations: Optional[list] = None,
    today: Optional[date] = None,
) -> dict:
    """
    Resuelve outcomes para todas las evaluaciones del audit trail que:
      - aún no tienen el horizonte resuelto en OUTCOMES_PATH
      - tienen signal_price disponible
      - son suficientemente antiguas (horizonte + buffer)

    Devuelve el dict completo de outcomes actualizado.
    `evaluations` puede pasarse directamente (para tests); si es None carga del disco.
    `today` puede inyectarse para tests.
    """
    from audit.decision_audit import iter_evaluations

    if evaluations is None:
        evaluations = list(iter_evaluations())

    today = today or date.today()
    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with _lock:
        outcomes = _load_outcomes()
        changed = False

        for ev in evaluations:
            eval_id      = ev.get("eval_id")
            ticker       = ev.get("ticker", "UNKNOWN")
            ts_str       = ev.get("ts", "")
            signal       = (ev.get("proposal") or {}).get("signal")
            signal_price = (ev.get("linkage") or {}).get("signal_price")
            eval_date_str = ts_str[:10]  # "YYYY-MM-DD"

            if not eval_id or not signal_price:
                continue

            eval_date = _eval_ts_to_date(ts_str)
            if eval_date is None:
                continue

            # Inicializar entrada si no existe
            if eval_id not in outcomes:
                outcomes[eval_id] = {}

            entry = outcomes[eval_id]

            # Intentar el ledger una vez (si no lo hemos chequeado aún y no hay
            # horizontes de fuente counterfactual que impidan el lookup)
            ledger_checked = entry.get("_ledger_checked", False)
            ledger_data: Optional[dict] = None
            if not ledger_checked:
                ledger_data = _fetch_ledger_outcomes(ticker, eval_date_str)
                entry["_ledger_checked"] = True
                if ledger_data:
                    # Fusionar horizontes del ledger (sin sobreescribir los
                    # ya resueltos como counterfactual)
                    for k, v in ledger_data.items():
                        if not k.startswith("_") and k not in entry:
                            entry[k] = v
                            changed = True
                    if ledger_data.get("_ledger_found"):
                        # Trade encontrado pero outcomes pendientes — marcar y esperar
                        entry["_ledger_pending"] = True
                changed = True

            # Para cada horizonte aún no resuelto, intentar resolución
            for horizon, days in HORIZONS:
                if horizon in entry:
                    continue  # ya resuelto

                if not _is_resolvable(eval_date, days, today):
                    continue  # demasiado reciente

                # Si hay un trade en el ledger pero sin outcomes resueltos aún,
                # no calcular contrafactual — esperamos a que OutcomeTracker lo resuelva
                if entry.get("_ledger_pending"):
                    continue

                # HOLD no tiene dirección implícita → skip
                if signal == "HOLD":
                    continue

                target_date = eval_date + timedelta(days=days)
                future_price = get_price_on_date(ticker, target_date)
                if future_price is None:
                    continue

                ret = _counterfactual_return(signal, signal_price, future_price)
                if ret is None:
                    continue

                entry[horizon] = {
                    "return":      round(ret, 6),
                    "win":         ret > 0,
                    "price":       future_price,
                    "ts_resolved": ts_now,
                    "source":      "counterfactual",
                }
                changed = True

        if changed:
            _save_outcomes(outcomes)

    return outcomes


# ── Vista enriquecida ─────────────────────────────────────────────────────────

def get_enriched_evaluations(
    evaluations: Optional[list] = None,
    outcomes: Optional[dict] = None,
    resolve: bool = True,
    today: Optional[date] = None,
) -> list[dict]:
    """
    Devuelve lista de evaluaciones con el bloque `outcomes` fusionado.

    Cada elemento = registro original del audit trail + clave `outcomes`:
      {
        "t1":  {"return": float, "win": bool, "price": float, "source": str} | None,
        "t5":  ...,
        "t20": ...,
        "any_resolved": bool,
      }

    `resolve=True` dispara resolución antes de cargar (recomendado en scripts
    interactivos). `resolve=False` solo lee lo que ya hay en disco (tests rápidos).
    """
    from audit.decision_audit import iter_evaluations

    if evaluations is None:
        evaluations = list(iter_evaluations())

    if resolve:
        outcomes = resolve_all(evaluations, today=today)
    elif outcomes is None:
        outcomes = load_outcomes()

    result = []
    for ev in evaluations:
        ev_id = ev.get("eval_id")
        raw   = outcomes.get(ev_id, {}) if ev_id else {}

        ev_outcomes: dict[str, Optional[dict]] = {}
        for h, _ in HORIZONS:
            v = raw.get(h)
            ev_outcomes[h] = {
                "return": v["return"],
                "win":    v["win"],
                "price":  v["price"],
                "source": v.get("source"),
            } if v else None

        ev_outcomes["any_resolved"] = any(ev_outcomes[h] for h, _ in HORIZONS)

        record = dict(ev)
        record["outcomes"] = ev_outcomes
        result.append(record)

    return result
