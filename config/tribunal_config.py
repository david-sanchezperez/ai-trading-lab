"""
Configuración del Juez de Contradicción. Mismo patrón que broker_config.py.

Modos:
  OFF    (default) — comportamiento actual intacto, el juez no se importa.
  SHADOW — el juez corre y se audita; CERO efecto en scenario/veredicto/score.
  ACTIVE — el juez selecciona scenario/key_question del prompt (por priority).

En v1 el juez nunca toca el score ni la penalización (decisión D8).
"""

from enum import StrEnum


class ContradictionJudgeMode(StrEnum):
    OFF    = "off"     # DEFAULT — comportamiento actual intacto
    SHADOW = "shadow"  # corre y audita; sin efecto operativo
    ACTIVE = "active"  # selecciona scenario/key_question del prompt


CONTRADICTION_JUDGE_MODE = ContradictionJudgeMode.OFF
