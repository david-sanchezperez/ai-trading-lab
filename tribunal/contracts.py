"""
tribunal/contracts.py — tipos de datos del Juez de Contradicción.

Principios:
- Frozen dataclasses: inmutables y JSON-safe mediante to_dict()/from_dict().
- Cero imports fuera de stdlib.
- Evolución aditiva: campos nuevos con default; nunca renombrar/retipar.
- engine_version y rules_version viajan en cada veredicto.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import Any, Mapping, Sequence


# ── Tipos escalares ────────────────────────────────────────────────────────────

SignalRef = str   # "proposal.action" | "signal.<name>"

ENGINE_VERSION = "contradiction-judge/1.0.0"


# ── Entradas ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Proposal:
    """Lo que el agente propone hacer y por qué (opaco al core)."""
    action: str
    rationale: str = ""
    confidence: float | None = None
    source: str | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action":     self.action,
            "rationale":  self.rationale,
            "confidence": self.confidence,
            "source":     self.source,
            "meta":       dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        return cls(
            action=d["action"],
            rationale=d.get("rationale", ""),
            confidence=d.get("confidence"),
            source=d.get("source"),
            meta=dict(d.get("meta") or {}),
        )


@dataclass(frozen=True)
class Signal:
    """Una lectura de evidencia con nombre. El core no interpreta el valor."""
    name: str
    value: float | int | str | bool
    source: str | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name":   self.name,
            "value":  self.value,
            "source": self.source,
            "meta":   dict(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Signal":
        return cls(
            name=d["name"],
            value=d["value"],
            source=d.get("source"),
            meta=dict(d.get("meta") or {}),
        )


class EvidenceSet:
    """Conjunto de señales de evidencia indexadas por nombre."""

    def __init__(self, signals: dict[str, Signal | None]) -> None:
        self._signals: dict[str, Signal] = {
            k: v for k, v in signals.items() if v is not None
        }

    def get(self, name: str) -> Signal | None:
        return self._signals.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._signals

    def names(self) -> list[str]:
        return list(self._signals.keys())

    def to_dict(self) -> dict:
        return {k: v.to_dict() for k, v in self._signals.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceSet":
        return cls({k: Signal.from_dict(v) for k, v in d.items()})

    def __repr__(self) -> str:
        return f"EvidenceSet({list(self._signals.keys())})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EvidenceSet):
            return NotImplemented
        return self._signals == other._signals


# ── Condiciones ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Cmp:
    """Comparación simple: signal <op> value."""
    signal: str
    op: str   # "lt" | "le" | "gt" | "ge" | "eq" | "ne" | "in"
    value: float | int | str | bool | tuple


@dataclass(frozen=True)
class All:
    """Conjunción: todas las cláusulas deben ser True. All(()) = True."""
    clauses: tuple[Condition, ...]


@dataclass(frozen=True)
class Any_:
    """Disyunción: al menos una cláusula True. Any_(()) = False."""
    clauses: tuple[Condition, ...]


@dataclass(frozen=True)
class Not_:
    """Negación."""
    clause: Condition


@dataclass(frozen=True)
class Always:
    """Siempre True. Para reglas kind=SCENARIO de fallback."""


Condition = Cmp | All | Any_ | Not_ | Always


def condition_to_dict(c: Condition) -> dict:
    if isinstance(c, Cmp):
        # tuple values (for "in" op) → list for JSON
        value = list(c.value) if isinstance(c.value, tuple) else c.value
        return {"_type": "Cmp", "signal": c.signal, "op": c.op, "value": value}
    if isinstance(c, All):
        return {"_type": "All", "clauses": [condition_to_dict(x) for x in c.clauses]}
    if isinstance(c, Any_):
        return {"_type": "Any_", "clauses": [condition_to_dict(x) for x in c.clauses]}
    if isinstance(c, Not_):
        return {"_type": "Not_", "clause": condition_to_dict(c.clause)}
    if isinstance(c, Always):
        return {"_type": "Always"}
    raise TypeError(f"Unknown condition type: {type(c)}")


def condition_from_dict(d: dict) -> Condition:
    t = d["_type"]
    if t == "Cmp":
        value = d["value"]
        # "in" op stores a list → restore as tuple
        if d["op"] == "in" and isinstance(value, list):
            value = tuple(value)
        return Cmp(d["signal"], d["op"], value)
    if t == "All":
        return All(tuple(condition_from_dict(x) for x in d["clauses"]))
    if t == "Any_":
        return Any_(tuple(condition_from_dict(x) for x in d["clauses"]))
    if t == "Not_":
        return Not_(condition_from_dict(d["clause"]))
    if t == "Always":
        return Always()
    raise ValueError(f"Unknown condition type tag: {t!r}")


# ── Salidas ───────────────────────────────────────────────────────────────────

class SeverityLevel(StrEnum):
    LOW    = "low"     # severity < 0.40
    MEDIUM = "medium"  # 0.40 ≤ severity < 0.70
    HIGH   = "high"    # severity ≥ 0.70


def severity_to_level(severity: float) -> SeverityLevel:
    if severity >= 0.70:
        return SeverityLevel.HIGH
    if severity >= 0.40:
        return SeverityLevel.MEDIUM
    return SeverityLevel.LOW


@dataclass(frozen=True)
class ConflictDirection:
    a_claims: str
    b_claims: str

    def to_dict(self) -> dict:
        return {"a_claims": self.a_claims, "b_claims": self.b_claims}

    @classmethod
    def from_dict(cls, d: dict) -> "ConflictDirection":
        return cls(a_claims=d["a_claims"], b_claims=d["b_claims"])


@dataclass(frozen=True)
class Contradiction:
    rule_id:        str
    pair:           tuple[SignalRef, SignalRef]
    direction:      ConflictDirection
    severity:       float                     # [0.0, 1.0]
    level:          SeverityLevel             # derivada de severity
    acknowledged:   bool
    acknowledged_by: tuple[str, ...]
    contributing:   tuple[SignalRef, ...]
    details:        dict                      # valores observados + umbrales

    def __post_init__(self) -> None:
        expected = severity_to_level(self.severity)
        if self.level != expected:
            raise ValueError(
                f"Contradiction.level={self.level!r} inconsistent with "
                f"severity={self.severity} (expected {expected!r})"
            )

    def to_dict(self) -> dict:
        return {
            "rule_id":        self.rule_id,
            "pair":           list(self.pair),
            "direction":      self.direction.to_dict(),
            "severity":       self.severity,
            "level":          str(self.level),
            "acknowledged":   self.acknowledged,
            "acknowledged_by": list(self.acknowledged_by),
            "contributing":   list(self.contributing),
            "details":        dict(self.details),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Contradiction":
        severity = d["severity"]
        return cls(
            rule_id=d["rule_id"],
            pair=tuple(d["pair"]),
            direction=ConflictDirection.from_dict(d["direction"]),
            severity=severity,
            level=severity_to_level(severity),   # recompute, don't trust stored value
            acknowledged=d["acknowledged"],
            acknowledged_by=tuple(d.get("acknowledged_by") or []),
            contributing=tuple(d.get("contributing") or []),
            details=dict(d.get("details") or {}),
        )


@dataclass(frozen=True)
class SkippedRule:
    rule_id: str
    reason:  str      # "missing_signal:<name>" | "type_mismatch:<name>"

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> "SkippedRule":
        return cls(rule_id=d["rule_id"], reason=d["reason"])


@dataclass(frozen=True)
class ContradictionVerdict:
    contradictions:   tuple[Contradiction, ...]  # severity desc, rule_id asc
    matched_rule_ids: tuple[str, ...]             # CONTRADICTION+SCENARIO, priority asc
    skipped:          tuple[SkippedRule, ...]
    evaluated_rules:  int
    engine_version:   str
    rules_version:    str

    def to_dict(self) -> dict:
        return {
            "contradictions":   [c.to_dict() for c in self.contradictions],
            "matched_rule_ids": list(self.matched_rule_ids),
            "skipped":          [s.to_dict() for s in self.skipped],
            "evaluated_rules":  self.evaluated_rules,
            "engine_version":   self.engine_version,
            "rules_version":    self.rules_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContradictionVerdict":
        return cls(
            contradictions=tuple(Contradiction.from_dict(c) for c in d.get("contradictions", [])),
            matched_rule_ids=tuple(d.get("matched_rule_ids", [])),
            skipped=tuple(SkippedRule.from_dict(s) for s in d.get("skipped", [])),
            evaluated_rules=d.get("evaluated_rules", 0),
            engine_version=d.get("engine_version", ENGINE_VERSION),
            rules_version=d.get("rules_version", ""),
        )


# ── Reglas ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SeverityScale:
    """Escalado lineal de severidad según magnitud de una señal."""
    signal:  str
    at_base: float   # valor de la señal en el umbral → severity = spec.base
    at_max:  float   # valor donde satura → severity = 1.0

    def to_dict(self) -> dict:
        return {"signal": self.signal, "at_base": self.at_base, "at_max": self.at_max}

    @classmethod
    def from_dict(cls, d: dict) -> "SeverityScale":
        return cls(signal=d["signal"], at_base=d["at_base"], at_max=d["at_max"])


@dataclass(frozen=True)
class SeveritySpec:
    base:  float
    scale: SeverityScale | None = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.base <= 1.0):
            raise ValueError(f"SeveritySpec.base={self.base} must be in [0, 1]")

    def to_dict(self) -> dict:
        return {
            "base":  self.base,
            "scale": self.scale.to_dict() if self.scale else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SeveritySpec":
        scale = SeverityScale.from_dict(d["scale"]) if d.get("scale") else None
        return cls(base=d["base"], scale=scale)


@dataclass(frozen=True)
class AckSpec:
    """
    Especificación para acknowledged matching léxico.
    acknowledged=True ⟺ para CADA grupo, al menos un término matchea el rationale.
    """
    groups: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict:
        return {"groups": [list(g) for g in self.groups]}

    @classmethod
    def from_dict(cls, d: dict) -> "AckSpec":
        return cls(groups=tuple(tuple(g) for g in d.get("groups", [])))


class RuleKind(StrEnum):
    CONTRADICTION = "contradiction"  # emite Contradiction si matchea
    SCENARIO      = "scenario"       # no emite Contradiction; solo marca escenario


@dataclass(frozen=True)
class ContradictionRule:
    id:          str
    kind:        RuleKind
    applies_to:  tuple[str, ...]       # acciones; ("*",) = todas
    condition:   Condition
    # Obligatorios si kind=CONTRADICTION:
    pair:        tuple[SignalRef, SignalRef] | None = None
    direction:   ConflictDirection | None = None
    severity:    SeveritySpec | None = None
    # Opcionales:
    contributing: tuple[SignalRef, ...]  = ()
    ack:         AckSpec | None = None
    priority:    int = 100
    scenario:    str | None = None
    key_question: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind == RuleKind.CONTRADICTION:
            missing = [
                f for f in ("pair", "direction", "severity")
                if getattr(self, f) is None
            ]
            if missing:
                raise ValueError(
                    f"Rule {self.id!r} (CONTRADICTION) requires: {', '.join(missing)}"
                )

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "kind":         str(self.kind),
            "applies_to":   list(self.applies_to),
            "condition":    condition_to_dict(self.condition),
            "pair":         list(self.pair) if self.pair else None,
            "direction":    self.direction.to_dict() if self.direction else None,
            "severity":     self.severity.to_dict() if self.severity else None,
            "contributing": list(self.contributing),
            "ack":          self.ack.to_dict() if self.ack else None,
            "priority":     self.priority,
            "scenario":     self.scenario,
            "key_question": self.key_question,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContradictionRule":
        pair_raw = d.get("pair")
        dir_raw  = d.get("direction")
        sev_raw  = d.get("severity")
        ack_raw  = d.get("ack")
        return cls(
            id=d["id"],
            kind=RuleKind(d["kind"]),
            applies_to=tuple(d["applies_to"]),
            condition=condition_from_dict(d["condition"]),
            pair=tuple(pair_raw) if pair_raw else None,
            direction=ConflictDirection.from_dict(dir_raw) if dir_raw else None,
            severity=SeveritySpec.from_dict(sev_raw) if sev_raw else None,
            contributing=tuple(d.get("contributing") or []),
            ack=AckSpec.from_dict(ack_raw) if ack_raw else None,
            priority=d.get("priority", 100),
            scenario=d.get("scenario"),
            key_question=d.get("key_question"),
            description=d.get("description", ""),
        )
