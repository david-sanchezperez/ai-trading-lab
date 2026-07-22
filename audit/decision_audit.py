"""
Decision audit trail — logging estructurado de las evaluaciones del analista crítico.

Cada ciclo del grafo produce un registro JSONL por ticker con la unidad completa
"propuesta → evidencias → deliberación → veredicto → decisión final":

  - proposal:  señal del technical_agent + score preliminar (pre-critic)
  - evidence:  indicadores, sentimiento, precedentes RAG, señales auxiliares
  - critic:    escenario detectado, veredicto, razonamiento, fast path, errores
  - decision:  acción final, score post-penalización, threshold, régimen

Ficheros: logs/decision_audit/YYYY-MM-DD.jsonl (append-only, una línea por evaluación).

Cruce con el resultado real de la operación:
  - Trades ejecutados: `predictions.id` en data/ledger.duckdb tiene formato
    {ticker}_{YYYYMMDD_HHMM}; el campo `linkage.ledger_join_key` de cada registro
    ({ticker}_{YYYYMMDD}) permite el join por ticker + fecha del ciclo.
  - Evaluaciones sin trade: resolubles a posteriori contra el precio futuro usando
    `linkage.signal_price` + histórico de precios (mismo mecanismo que OutcomeTracker).

Diseño: el writer (_append_jsonl) es agnóstico del dominio; toda la extracción de
campos desde TradingState vive en _build_record, de modo que al extraer la futura
librería solo hay que sustituir el builder por adaptadores por dominio.

`log_evaluation()` nunca lanza excepciones — un fallo de auditoría no puede
interrumpir el pipeline de trading.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from core.config import LOGS_DIR

log = logging.getLogger(__name__)

AUDIT_DIR = LOGS_DIR / "decision_audit"
SCHEMA_VERSION = 1

_write_lock = threading.Lock()


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _append_jsonl(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _compact_precedents(rag_precedents: list, ticker: str) -> list[dict]:
    """Reduce los docs RAG a los campos relevantes para el audit trail."""
    out = []
    for doc in rag_precedents or []:
        if not isinstance(doc, dict):
            continue
        m = doc.get("metadata") or {}
        out.append({
            "date":        str(m.get("date", ""))[:10],
            "ticker":      m.get("ticker", ticker),
            "signal":      m.get("signal"),
            "similarity":  round(float(doc.get("similarity", 0.0)), 3),
            "outcome":     m.get("outcome"),
            "outcome_5d":  m.get("outcome_5d"),
            "outcome_10d": m.get("outcome_10d"),
        })
    return out


def _build_record(
    state: dict,
    decision: dict,
    final_score: float,
    effective_threshold: float,
) -> dict:
    from core.session_logger import get_session_logger

    now_local = datetime.now()
    ticker    = state.get("ticker", "UNKNOWN")
    tech      = state.get("technical_result") or {}
    sent      = state.get("sentiment_result") or {}
    critic    = state.get("critic_result") or {}
    wr        = decision.get("win_rate") or {}

    session    = get_session_logger()
    session_id = session.session_id if session else None

    stocktwits = sent.get("stocktwits")
    if isinstance(stocktwits, dict):
        stocktwits = {k: stocktwits.get(k) for k in ("bullish_pct", "bearish_pct", "labeled")}
    else:
        stocktwits = None

    return {
        "schema_version": SCHEMA_VERSION,
        "eval_id":        f"{ticker}_{now_local:%Y%m%d_%H%M%S}",
        "ts":             _utc_ts(),
        "session_id":     session_id,
        "ticker":         ticker,

        # Decisión propuesta que el critic evalúa
        "proposal": {
            "source":           "technical_agent",
            "signal":           tech.get("signal"),
            "confidence":       tech.get("confidence"),
            "score_pre_critic": decision.get("score"),
        },

        # Evidencias disponibles en el momento de la evaluación
        "evidence": {
            "technical": {
                k: tech.get(k)
                for k in (
                    "rsi", "price", "atr_14", "trend_up", "volume_ratio",
                    "pct_52w_range", "rs_spy", "dist_sma20",
                    "buy_votes", "sell_votes",
                )
            },
            "sentiment": {
                "score":      sent.get("sentiment"),
                "confidence": sent.get("confidence"),
                "headlines":  sent.get("headlines"),
                "stocktwits": stocktwits,
            },
            "historical_sentiment":       decision.get("historical_sentiment"),
            "win_rate":                   wr,
            "pead":                       decision.get("pead"),
            "insider":                    decision.get("insider"),
            "rag_precedents":             _compact_precedents(critic.get("rag_precedents"), ticker),
            "intraday_context_available": bool(state.get("intraday_context")),
        },

        # Deliberación del analista crítico
        "critic": {
            "engaged":          not critic.get("fast_path", False),
            "fast_path":        critic.get("fast_path", False),
            "fast_path_reason": critic.get("fast_path_reason"),
            "scenario":         critic.get("scenario"),
            "key_question":     critic.get("key_question"),
            "verdict":          critic.get("verdict"),
            "approved":         critic.get("approved"),
            "reasoning":        critic.get("reasoning"),
            "thinking":         (critic.get("thinking") or "")[:6000] or None,
            "error":            critic.get("error", False),
        },

        # Decisión final tras aplicar veredicto + régimen
        "decision": {
            "action":                 decision.get("action"),
            "score_final":            round(float(final_score), 4),
            "critic_penalty_applied": bool(decision.get("critic_override")),
            "threshold_used":         effective_threshold,
            "regime_adjustment":      decision.get("regime_adjustment"),
        },

        # Identificadores para cruzar con el resultado real
        "linkage": {
            "signal_price":    tech.get("price"),
            "ledger_join_key": f"{ticker}_{now_local:%Y%m%d}",
        },

        # Veredictos de los jueces (bloque preparado para Juez 1 y 3 futuros).
        # schema_version se mantiene en 1: cambio puramente aditivo (D10).
        # Registros anteriores sin "judges" siguen siendo legibles (I6).
        "judges": {
            "contradiction": critic.get("contradiction_judge"),
        },
    }


def log_evaluation(
    state: dict,
    decision: dict,
    final_score: float,
    effective_threshold: float,
) -> Optional[str]:
    """
    Registra una evaluación completa del analista. Devuelve el eval_id,
    o None si algo falló (nunca lanza).
    """
    try:
        record = _build_record(state, decision, final_score, effective_threshold)
        path = AUDIT_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"
        _append_jsonl(record, path)
        return record["eval_id"]
    except Exception as exc:
        log.warning(f"[decision_audit] No se pudo registrar la evaluación: {exc}")
        return None


def iter_evaluations(date_str: Optional[str] = None) -> Iterator[dict]:
    """
    Itera los registros de auditoría. date_str="YYYY-MM-DD" para un día
    concreto; None para todos los días disponibles en orden cronológico.
    """
    if date_str:
        paths = [AUDIT_DIR / f"{date_str}.jsonl"]
    else:
        paths = sorted(AUDIT_DIR.glob("*.jsonl"))
    for path in paths:
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning(f"[decision_audit] Línea corrupta en {path.name} — ignorada")
