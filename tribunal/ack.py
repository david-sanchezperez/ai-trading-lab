"""
tribunal/ack.py — matching léxico para el campo `acknowledged`.

Mecanismo exacto (§3.1):
  1. Normalización: Unicode NFKD → eliminar diacríticos → casefold() → tokens \\w+.
  2. Match de término: subsecuencia CONSECUTIVA de tokens del rationale.
     "52-week" → tokens ["52","week"] → matchea "52 week high" pero no "52 items week".
  3. Veredicto: AND sobre grupos, OR dentro de grupo.
     acknowledged=True ⟺ para CADA grupo, al menos un término matchea.

Limitaciones conocidas y documentadas (§3.2):
  - Falso positivo: negación ("not oversold") se confunde con acknowledgment.
  - Falso negativo: paráfrasis no enumeradas y menciones solo numéricas.
  - Sin stemming, sin ventana de proximidad, sin semántica.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tribunal.contracts import AckSpec


def _normalize(text: str) -> list[str]:
    """
    Normaliza texto a lista de tokens lowercase sin diacríticos.
    "RSI is Oversold, 52-week high!" → ["rsi","is","oversold","52","week","high"]
    """
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.findall(r"\w+", ascii_only.casefold())


def _normalize_term(term: str) -> list[str]:
    """Normaliza un término de AckSpec a lista de tokens (mismo pipeline que rationale)."""
    return _normalize(term)


def _matches_term(term_tokens: list[str], rationale_tokens: list[str]) -> bool:
    """
    True si term_tokens aparece como subsecuencia CONSECUTIVA en rationale_tokens.
    Ejemplo: ["52","week"] matchea [...,"52","week","high",...].
    """
    if not term_tokens:
        return True  # término vacío siempre matchea (vacuous truth)
    n = len(term_tokens)
    for i in range(len(rationale_tokens) - n + 1):
        if rationale_tokens[i : i + n] == term_tokens:
            return True
    return False


def check_acknowledged(
    spec: "AckSpec | None",
    rationale: str,
) -> tuple[bool, tuple[str, ...]]:
    """
    Comprueba si el rationale reconoce la contradicción descrita por spec.

    Devuelve (acknowledged: bool, acknowledged_by: tuple[str, ...]).
    acknowledged_by: términos que matchearon (uno por grupo como mínimo).

    Rationale vacío o spec=None → (False, ()).
    """
    if spec is None or not rationale:
        return False, ()

    rationale_tokens = _normalize(rationale)
    matched_terms: list[str] = []

    for group in spec.groups:
        group_matched = False
        for term in group:
            term_tokens = _normalize_term(term)
            if _matches_term(term_tokens, rationale_tokens):
                matched_terms.append(term)
                group_matched = True
                break  # OR dentro del grupo: primer match es suficiente
        if not group_matched:
            # AND entre grupos: si un grupo no matchea, acknowledged=False
            return False, ()

    return True, tuple(matched_terms)
