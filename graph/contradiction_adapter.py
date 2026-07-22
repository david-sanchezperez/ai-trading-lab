"""
graph/contradiction_adapter.py — adaptador lab ↔ tribunal.

Construye Proposal + EvidenceSet desde TradingState e invoca tribunal.evaluate().
Devuelve un dict JSON-safe para poblar critic_result["contradiction_judge"].

Responsabilidades:
  - Mapear campos de TradingState a los tipos del tribunal (sin defaults falsos).
  - No sintetizar señales ausentes: si "rsi" no está en technical_result, la
    señal "rsi" no aparece en EvidenceSet → las reglas RSI van a skipped.
  - Calcular scenario_hint para que scenario_from_verdict() funcione sin necesidad
    de acceder a TRADING_RULES desde el llamador.
  - Medir duración de evaluate() para el audit trail.

No modifica el TradingState ni tiene efectos secundarios.
"""

from __future__ import annotations

import time
import logging

log = logging.getLogger(__name__)

_FALLBACK_SCENARIO   = "Aligned signals — verify coherence"
_FALLBACK_KQUESTION  = "Are all indicators pointing in the same direction?"


def _build_proposal(state: dict) -> "Proposal":
    from tribunal.contracts import Proposal
    tech = state.get("technical_result") or {}
    return Proposal(
        action=tech.get("signal", "HOLD"),
        rationale="",          # técnico no razona en texto (D6)
        confidence=tech.get("confidence"),
        source="technical_agent",
    )


def _build_evidence(state: dict) -> "EvidenceSet":
    """
    Mapea technical_result y sentiment_result a EvidenceSet.
    Solo añade una señal si la clave existe Y el valor no es None.
    NO inventa defaults (§2.5): "rsi" ausente → skipped en reglas RSI.
    """
    from tribunal.contracts import EvidenceSet, Signal

    tech = state.get("technical_result") or {}
    sent = state.get("sentiment_result") or {}

    signals: dict[str, Signal | None] = {}

    # ── Señales técnicas ─────────────────────────────────────────────────────
    # rsi: el critic usa `technical.get("rsi", 50)` pero el adaptador NO añade
    # el default 50 — señal ausente ≠ RSI=50 (§2.5).
    _maybe_float(signals, "rsi",          tech, "rsi")
    _maybe_bool (signals, "trend_up",     tech, "trend_up")
    _maybe_float(signals, "rs_spy",       tech, "rs_spy")
    _maybe_float(signals, "pct_52w_range", tech, "pct_52w_range")
    # Extra para details (no usados en condiciones de reglas v1):
    _maybe_float(signals, "volume_ratio", tech, "volume_ratio")
    _maybe_int  (signals, "buy_votes",    tech, "buy_votes")
    _maybe_int  (signals, "sell_votes",   tech, "sell_votes")

    # ── Señales de sentimiento ───────────────────────────────────────────────
    # sentiment_result=None → señal "sentiment" ausente → R5/R6 a skipped (T5).
    _maybe_float(signals, "sentiment", sent, "sentiment")

    return EvidenceSet(signals)


def _maybe_float(
    out: dict,
    key: str,
    source: dict,
    field: str,
) -> None:
    from tribunal.contracts import Signal
    raw = source.get(field)
    if raw is not None:
        try:
            out[key] = Signal(key, float(raw), source="technical_agent")
        except (TypeError, ValueError):
            pass  # valor incompatible → señal ausente


def _maybe_bool(out: dict, key: str, source: dict, field: str) -> None:
    from tribunal.contracts import Signal
    raw = source.get(field)
    if raw is not None:
        out[key] = Signal(key, bool(raw), source="technical_agent")


def _maybe_int(out: dict, key: str, source: dict, field: str) -> None:
    from tribunal.contracts import Signal
    raw = source.get(field)
    if raw is not None:
        try:
            out[key] = Signal(key, int(raw), source="technical_agent")
        except (TypeError, ValueError):
            pass


def run_contradiction_judge(state: dict) -> dict:
    """
    Ejecuta el juez de contradicción sobre un TradingState.
    Devuelve un dict JSON-safe (poblado en critic_result["contradiction_judge"]).

    Incluye scenario_hint: el escenario que modo ACTIVE usaría (regla de menor
    priority entre las que matchearon), para que scenario_from_verdict() no
    necesite acceder a TRADING_RULES.

    Nunca lanza — el llamador ya tiene un try/except fail-open (D9).
    """
    from config.tribunal_config import CONTRADICTION_JUDGE_MODE
    from config.contradiction_rules import TRADING_RULES
    from tribunal.engine import evaluate

    t0 = time.monotonic()

    proposal = _build_proposal(state)
    evidence = _build_evidence(state)
    verdict  = evaluate(proposal, evidence, TRADING_RULES)

    duration_ms = round((time.monotonic() - t0) * 1000, 2)

    # ── Scenario hint: primera regla matcheada por priority ──────────────────
    rule_map = {r.id: r for r in TRADING_RULES}
    scenario_hint: dict | None = None
    for rule_id in verdict.matched_rule_ids:   # ya ordenadas por priority asc
        rule = rule_map.get(rule_id)
        if rule and rule.scenario:
            scenario_hint = {
                "rule_id":      rule_id,
                "scenario":     rule.scenario,
                "key_question": rule.key_question,
            }
            break

    return {
        "mode":             str(CONTRADICTION_JUDGE_MODE),
        "engine_version":   verdict.engine_version,
        "rules_version":    verdict.rules_version,
        "rationale_present": bool(proposal.rationale),
        "contradictions":   [c.to_dict() for c in verdict.contradictions],
        "matched_rule_ids": list(verdict.matched_rule_ids),
        "skipped":          [s.to_dict() for s in verdict.skipped],
        "evaluated_rules":  verdict.evaluated_rules,
        "scenario_hint":    scenario_hint,
        "duration_ms":      duration_ms,
        "error":            None,
    }


def scenario_from_verdict(judge_block: dict) -> tuple[str, str]:
    """
    Extrae (scenario, key_question) del bloque del juez para modo ACTIVE.
    Reproduce exactamente la elección del if/elif por priority (§4.1).
    Devuelve el fallback "Aligned signals" si no hay hint disponible.
    """
    hint = judge_block.get("scenario_hint") or {}
    scenario     = hint.get("scenario")     or _FALLBACK_SCENARIO
    key_question = hint.get("key_question") or _FALLBACK_KQUESTION
    return scenario, key_question
