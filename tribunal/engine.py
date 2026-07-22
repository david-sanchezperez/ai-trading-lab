"""
tribunal/engine.py — motor de evaluación del Juez de Contradicción.

evaluate() es una función pura: sin I/O, sin red, sin reloj, sin estado global.
Misma entrada → misma salida, byte a byte (incluido el orden de las listas).

Complejidad: O(reglas × cláusulas), < 1 ms por evaluación en el ruleset trading.
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

from tribunal.contracts import (
    Contradiction,
    ContradictionRule,
    ContradictionVerdict,
    EvidenceSet,
    Proposal,
    RuleKind,
    SkippedRule,
    SeverityScale,
    SeveritySpec,
    Signal,
    ENGINE_VERSION,
    severity_to_level,
)
from tribunal.conditions import _eval, collect_details
from tribunal.ack import check_acknowledged


# ── Severidad ─────────────────────────────────────────────────────────────────

def _compute_severity(
    spec: SeveritySpec,
    evidence: EvidenceSet,
) -> float:
    """
    Calcula la severidad de una contradicción.

    Sin scale → spec.base exacto (E11).
    Con scale → interpolación lineal entre (at_base→base) y (at_max→1.0),
    clamp a [base, 1.0] (E12).
    scale signal ausente → spec.base (E14).
    at_base == at_max → 1.0 al matchear (E13, sin división por cero).
    """
    if spec.scale is None:
        return spec.base

    scale: SeverityScale = spec.scale
    sig = evidence.get(scale.signal)
    if sig is None:
        return spec.base  # E14: scale signal ausente → usar base

    try:
        observed = float(sig.value)
    except (TypeError, ValueError):
        return spec.base  # tipo incompatible → degradar a base

    denom = scale.at_max - scale.at_base
    if denom == 0.0:
        return 1.0  # E13: degenerado → saturación

    t = (observed - scale.at_base) / denom
    severity = spec.base + t * (1.0 - spec.base)
    return max(spec.base, min(1.0, severity))


# ── rules_version ─────────────────────────────────────────────────────────────

def compute_rules_version(rules: Sequence[ContradictionRule]) -> str:
    """
    Hash sha256[:12] del ruleset en forma canónica.
    Mismo ruleset → mismo hash (E10).
    El orden de declaración no afecta al hash (se ordena por id).
    """
    canonical = [
        r.to_dict()
        for r in sorted(rules, key=lambda r: r.id)
    ]
    serialized = json.dumps(canonical, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


# ── Evaluación principal ───────────────────────────────────────────────────────

def evaluate(
    proposal: Proposal,
    evidence: EvidenceSet,
    rules: Sequence[ContradictionRule],
) -> ContradictionVerdict:
    """
    Evalúa todas las reglas aplicables y devuelve un ContradictionVerdict.

    Semántica (§2.3):
    - Se evalúan TODAS las reglas cuyo applies_to contiene la acción (o "*").
      No hay cortocircuito entre reglas.
    - Regla con señal ausente/tipo incompatible → SkippedRule (no error).
    - Contradicciones ordenadas: severity desc, tie-break rule_id asc.
    - matched_rule_ids: reglas que matchearon (CONTRADICTION + SCENARIO),
      ordenadas por priority asc.
    """
    action = proposal.action
    contradictions: list[Contradiction] = []
    skipped: list[SkippedRule] = []
    matched: list[ContradictionRule] = []  # todas las que matchearon (any kind)
    evaluated = 0

    for rule in rules:
        # Filtrar por applies_to (E6)
        if action not in rule.applies_to and "*" not in rule.applies_to:
            continue

        evaluated += 1
        matched_result, skip_reason = _eval(rule.condition, evidence)

        if matched_result is None:
            # Señal ausente o tipo incompatible — regla a skipped
            skipped.append(SkippedRule(rule_id=rule.id, reason=skip_reason or "unknown"))
            continue

        if not matched_result:
            continue  # condición no cumplida — sin contradicción, sin skip

        # La condición matcheó
        matched.append(rule)

        if rule.kind == RuleKind.CONTRADICTION:
            # Calcular severidad
            severity = _compute_severity(rule.severity, evidence)  # type: ignore[arg-type]

            # Acknowledged
            ack, ack_by = check_acknowledged(rule.ack, proposal.rationale)

            # Details: valores observados + umbrales
            details = collect_details(rule.condition, evidence)

            contradictions.append(Contradiction(
                rule_id=rule.id,
                pair=rule.pair,  # type: ignore[arg-type]
                direction=rule.direction,  # type: ignore[arg-type]
                severity=round(severity, 6),
                level=severity_to_level(severity),
                acknowledged=ack,
                acknowledged_by=ack_by,
                contributing=rule.contributing,
                details=details,
            ))
        # kind=SCENARIO: ya está en matched, no emite Contradiction (D7)

    # Ordenar contradicciones: severity desc, tie-break rule_id asc (E7)
    contradictions.sort(key=lambda c: (-c.severity, c.rule_id))

    # matched_rule_ids: todas las reglas que matchearon, por priority asc (E9)
    matched.sort(key=lambda r: (r.priority, r.id))
    matched_rule_ids = tuple(r.id for r in matched)

    rules_version = compute_rules_version(rules)

    return ContradictionVerdict(
        contradictions=tuple(contradictions),
        matched_rule_ids=matched_rule_ids,
        skipped=tuple(skipped),
        evaluated_rules=evaluated,
        engine_version=ENGINE_VERSION,
        rules_version=rules_version,
    )
