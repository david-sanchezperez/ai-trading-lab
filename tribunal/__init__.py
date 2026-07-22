"""
tribunal — core del Juez de Contradicción (y futuros jueces).

Restricción dura: este paquete solo importa stdlib. Cero dependencias del lab.
El test I8 (test_critic_judge_integration.py) lo verifica inspeccionando los ASTs.

API pública estable: los símbolos exportados aquí son el contrato de la librería.
"""

from tribunal.contracts import (
    Proposal,
    Signal,
    EvidenceSet,
    ConflictDirection,
    SeverityLevel,
    SeverityScale,
    SeveritySpec,
    AckSpec,
    RuleKind,
    ContradictionRule,
    Contradiction,
    SkippedRule,
    ContradictionVerdict,
    # Condition types
    Cmp,
    All,
    Any_,
    Not_,
    Always,
    # Helpers
    condition_to_dict,
    condition_from_dict,
)
from tribunal.engine import evaluate, compute_rules_version

__all__ = [
    "Proposal", "Signal", "EvidenceSet",
    "ConflictDirection", "SeverityLevel", "SeverityScale", "SeveritySpec",
    "AckSpec", "RuleKind", "ContradictionRule",
    "Contradiction", "SkippedRule", "ContradictionVerdict",
    "Cmp", "All", "Any_", "Not_", "Always",
    "condition_to_dict", "condition_from_dict",
    "evaluate", "compute_rules_version",
]
