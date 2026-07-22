"""
tribunal/conditions.py — evaluación del AST de condiciones.

_eval() es pura: sin I/O, sin efectos secundarios.

Semántica de evaluación (E3, D13):
  - Señal ausente en CUALQUIER Cmp de la regla → regla completa a skipped.
  - No hay evaluación parcial: en Any_, se evalúan TODAS las cláusulas
    para detectar señales ausentes aunque ya se haya encontrado un True.
  - La primera razón de skip encontrada se propaga hacia arriba.

All(()) = True, Any_(()) = False (convención estándar documentada, E2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tribunal.contracts import (
        Condition, Cmp, All, Any_, Not_, Always, EvidenceSet, Signal
    )


def _compare(op: str, observed: object, threshold: object) -> bool:
    """Compara observed contra threshold con el operador dado.
    Lanza TypeError si los tipos son incompatibles con el operador.
    """
    if op == "lt":
        return observed < threshold  # type: ignore[operator]
    if op == "le":
        return observed <= threshold  # type: ignore[operator]
    if op == "gt":
        return observed > threshold  # type: ignore[operator]
    if op == "ge":
        return observed >= threshold  # type: ignore[operator]
    if op == "eq":
        return observed == threshold
    if op == "ne":
        return observed != threshold
    if op == "in":
        return observed in threshold  # type: ignore[operator]
    raise ValueError(f"Unknown op: {op!r}")


def _eval(
    condition: "Condition",
    evidence: "EvidenceSet",
) -> tuple[bool | None, str | None]:
    """
    Evalúa condition contra evidence.
    Devuelve (matched: bool|None, skip_reason: str|None).
    matched=None → regla no evaluable (skip_reason está poblado).
    """
    from tribunal.contracts import Cmp, All, Any_, Not_, Always

    if isinstance(condition, Always):
        return True, None

    if isinstance(condition, Cmp):
        sig = evidence.get(condition.signal)
        if sig is None:
            return None, f"missing_signal:{condition.signal}"
        try:
            return _compare(condition.op, sig.value, condition.value), None
        except TypeError:
            return None, f"type_mismatch:{condition.signal}"

    if isinstance(condition, All):
        # Short-circuit on False (no hay señal ausente → podemos cortar).
        # Si encontramos un skip, propagamos inmediatamente.
        for clause in condition.clauses:
            result, reason = _eval(clause, evidence)
            if result is None:
                return None, reason
            if not result:
                return False, None
        return True, None   # All(()) = True

    if isinstance(condition, Any_):
        # NO short-circuit en True: evaluar TODAS las cláusulas para detectar
        # señales ausentes (D13). Un skip en cualquier cláusula skipea la regla.
        any_true = False
        for clause in condition.clauses:
            result, reason = _eval(clause, evidence)
            if result is None:
                return None, reason
            if result:
                any_true = True
        return any_true, None   # Any_(()) = False

    if isinstance(condition, Not_):
        result, reason = _eval(condition.clause, evidence)
        if result is None:
            return None, reason
        return not result, None

    raise TypeError(f"Unknown condition type: {type(condition)!r}")


def collect_details(condition: "Condition", evidence: "EvidenceSet") -> dict:
    """
    Extrae valores observados y umbrales de todos los Cmp en la condición.
    Formato: {signal_name: observed_value, "threshold_<signal>": cmp_value, ...}
    """
    acc: dict = {}
    _collect_details_rec(condition, evidence, acc)
    return acc


def _collect_details_rec(
    condition: "Condition",
    evidence: "EvidenceSet",
    acc: dict,
) -> None:
    from tribunal.contracts import Cmp, All, Any_, Not_, Always

    if isinstance(condition, Cmp):
        sig = evidence.get(condition.signal)
        if sig is not None:
            acc[condition.signal] = sig.value
        acc[f"threshold_{condition.signal}"] = condition.value

    elif isinstance(condition, (All, Any_)):
        for clause in condition.clauses:
            _collect_details_rec(clause, evidence, acc)

    elif isinstance(condition, Not_):
        _collect_details_rec(condition.clause, evidence, acc)
    # Always: no hay señales que recolectar
