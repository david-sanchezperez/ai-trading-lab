# DESIGN_JUDGE2 — Juez de Contradicción (diseño, sin implementación)

Fase 2 del plan de ARCHITECTURE_NOTES.md §4: extracción del **Juez 2 —
Contradicción** como módulo determinista (sin LLM), primer componente de la
futura librería open-source de auditoría de decisiones de agentes ("tribunal").

Este documento es SOLO diseño. Los snippets son ilustrativos, no código de
producción.

**Principios heredados de §2/§4**:
- El *patrón* generaliza (detectar pares de señales en conflicto que la
  propuesta ignora); los umbrales y la semántica de las señales son dominio.
- Hoy solo se desafía el primer escenario que matchea (if/elif). El juez
  evalúa **todas** las reglas presentes — matriz señal-vs-señal.
- Veredicto estructurado por contradicción, no binario global.
- Las 7 reglas actuales de `critic_node` deben poder expresarse en el formato
  declarativo como "adaptador trading" (validación del diseño, §2.4 abajo).

---

## 1. Contrato de datos (API pública de la librería)

### 1.1 Principios

1. **Cero acoplamiento a trading en el core.** El core no conoce "RSI",
   "BUY" ni "sentiment": recibe una acción opaca (`str`) y señales tipadas
   genéricas (`Signal`). Toda semántica de dominio vive en la configuración
   de reglas y en el adaptador.
2. **Inmutable y serializable.** Todos los tipos son dataclasses `frozen=True`
   con `to_dict()`/`from_dict()` JSON-safe. Un veredicto debe poder viajar al
   audit trail JSONL sin transformación.
3. **Puro y determinista.** `evaluate()` es una función pura: sin I/O, sin
   red, sin reloj, sin estado global. Misma entrada → misma salida, byte a
   byte (incluido el orden de las listas).
4. **Evolución aditiva.** Campos nuevos siempre opcionales con default; nunca
   se renombra ni se cambia el tipo de un campo publicado. `engine_version`
   (semver) y `rules_version` (hash del ruleset) viajan en cada veredicto
   para reproducibilidad.

### 1.2 Tipos de entrada

```python
# tribunal/contracts.py — ilustrativo

@dataclass(frozen=True)
class Proposal:
    """Lo que el agente propone hacer y por qué."""
    action: str                          # opaco para el core ("BUY", "approve_loan", …)
    rationale: str = ""                  # texto libre del agente; base del match de acknowledged
    confidence: float | None = None      # confianza declarada, si existe
    source: str | None = None            # quién propone ("technical_agent")
    meta: Mapping[str, Any] = field(default_factory=dict)  # escape hatch, no lo lee el core

@dataclass(frozen=True)
class Signal:
    """Una lectura de evidencia con nombre. El core no interpreta el valor."""
    name: str                            # "rsi", "sentiment", "trend_up", …
    value: float | int | str | bool
    observed_at: datetime | None = None  # frescura — la usará el Juez 1, aquí solo viaja
    source: str | None = None            # "finbert", "yfinance", …
    meta: Mapping[str, Any] = field(default_factory=dict)

# El conjunto de evidencia es un mapping name → Signal.
# Clase fina en vez de dict pelado para poder añadir helpers sin romper API.
class EvidenceSet:
    def get(self, name: str) -> Signal | None: ...
    def __contains__(self, name: str) -> bool: ...
```

Notas:
- `Proposal.action` es un `str` opaco a propósito: un enum cerraría la API a
  dominios con acciones distintas. Las reglas declaran a qué acciones aplican.
- `Signal.value` admite escalares y booleanos; series temporales o estructuras
  complejas se reducen a escalares **en el adaptador** (p.ej. `trend_up: bool`
  ya resume SMA20 vs SMA50). Mantener el core sobre escalares es lo que hace
  las reglas declarativas y auditables.
- Señales ausentes son legales: el core las trata como "regla no evaluable"
  (ver §2.3), nunca como error. Juzgar la *completitud* de la evidencia es
  trabajo del Juez 1, no de este.

### 1.3 Tipos de salida

Contrato pedido: `{contradictions: [{pair, direction, severity, acknowledged}]}`,
enriquecido con trazabilidad:

```python
SignalRef = str   # "proposal.action" | "signal.<name>"  — refs estables, JSON-friendly

@dataclass(frozen=True)
class ConflictDirection:
    """Qué afirma cada lado del par. Labels declarados en la regla (dominio),
    el core solo los transporta."""
    a_claims: str                        # p.ej. "bullish"
    b_claims: str                        # p.ej. "overextended"

class SeverityLevel(StrEnum):
    LOW = "low"        # severity < 0.40
    MEDIUM = "medium"  # 0.40 ≤ severity < 0.70
    HIGH = "high"      # severity ≥ 0.70

@dataclass(frozen=True)
class Contradiction:
    rule_id: str                         # regla que la detectó
    pair: tuple[SignalRef, SignalRef]    # (a, b); a es normalmente proposal.action
    direction: ConflictDirection
    severity: float                      # [0.0, 1.0], ver §2.2
    level: SeverityLevel                 # derivada de severity (denormalizada por legibilidad)
    acknowledged: bool                   # ¿el rationale de la propuesta ya lo contempla? (§3)
    acknowledged_by: tuple[str, ...]     # términos que matchearon (vacío si no acknowledged)
    contributing: tuple[SignalRef, ...]  # señales secundarias de condiciones compuestas
    details: Mapping[str, Any]           # valores observados y umbrales, para el audit trail

@dataclass(frozen=True)
class SkippedRule:
    rule_id: str
    reason: str                          # "missing_signal:rsi" | "type_mismatch:trend_up" | …

@dataclass(frozen=True)
class ContradictionVerdict:
    contradictions: tuple[Contradiction, ...]  # orden: severity desc, tie-break rule_id asc
    matched_rule_ids: tuple[str, ...]    # TODAS las reglas que matchearon, incl. kind="scenario" (§2.1)
    skipped: tuple[SkippedRule, ...]     # reglas no evaluables — observabilidad, no error
    evaluated_rules: int
    engine_version: str                  # "contradiction-judge/1.0.0"
    rules_version: str                   # hash sha256[:12] del ruleset canónico
```

### 1.4 Punto de entrada

```python
def evaluate(
    proposal: Proposal,
    evidence: EvidenceSet,
    rules: Sequence[ContradictionRule],
) -> ContradictionVerdict: ...
```

Una única función pura. Sin singleton, sin config global: quien llama inyecta
las reglas. El lab tendrá un wrapper-adaptador (§4.2) que construye
`Proposal`/`EvidenceSet` desde `TradingState` y pasa el ruleset de trading.

---

## 2. Motor de reglas

### 2.1 Declaración de reglas

Formato canónico v1: **dataclasses Python** (tipadas, validables por mypy,
sin parser). Un loader TOML/YAML es una capa v2 encima del mismo modelo
(ver decisión D2 en §6).

```python
class RuleKind(StrEnum):
    CONTRADICTION = "contradiction"   # emite Contradiction si matchea
    SCENARIO = "scenario"             # no emite contradicción; marca un escenario
                                      # de deliberación (HOLD, "aligned") — lo usa
                                      # el adaptador para generar el prompt del LLM

@dataclass(frozen=True)
class ContradictionRule:
    id: str                              # slug único, estable (clave de analítica)
    kind: RuleKind
    applies_to: tuple[str, ...]          # acciones de la propuesta; ("*",) = todas
    condition: Condition                 # AST, ver §2.2
    pair: tuple[SignalRef, SignalRef] | None      # obligatorio si kind=CONTRADICTION
    direction: ConflictDirection | None
    severity: SeveritySpec | None
    contributing: tuple[SignalRef, ...] = ()
    ack: AckSpec | None = None           # §3; None ⇒ acknowledged siempre False
    priority: int = 100                  # menor = antes; reproduce el orden if/elif
                                         # actual al elegir el escenario del prompt
    # Contenido para el prompt del critic (dominio; strings de config, el core no los interpreta)
    scenario: str | None = None
    key_question: str | None = None
    description: str = ""
```

### 2.2 Condiciones y severidad

**Condiciones** — AST tipado mínimo, sin `eval` ni strings:

```python
Condition = Cmp | All | Any_ | Not_ | Always

@dataclass(frozen=True)
class Cmp:
    signal: str                          # nombre de señal en EvidenceSet
    op: Literal["lt", "le", "gt", "ge", "eq", "ne", "in"]
    value: float | int | str | bool | tuple

@dataclass(frozen=True)
class All:  clauses: tuple[Condition, ...]
@dataclass(frozen=True)
class Any_: clauses: tuple[Condition, ...]
@dataclass(frozen=True)
class Not_: clause: Condition
@dataclass(frozen=True)
class Always: pass                       # para reglas kind=SCENARIO de fallback
```

**Severidad** — declarada por regla, con escalado lineal opcional por
magnitud (cuanto más lejos del umbral, más grave):

```python
@dataclass(frozen=True)
class SeverityScale:
    signal: str          # señal cuya magnitud modula la severidad
    at_base: float       # valor de la señal en el umbral → severity = base
    at_max: float        # valor donde satura → severity = 1.0
                         # interpolación lineal, clamp a [base, 1.0]
                         # at_max < at_base es válido (escala descendente, p.ej. RSI oversold)

@dataclass(frozen=True)
class SeveritySpec:
    base: float                          # [0,1] — severidad si no hay scale
    scale: SeverityScale | None = None
```

Ejemplo: `SeveritySpec(base=0.6, scale=SeverityScale("rsi", at_base=35, at_max=20))`
→ RSI 34 ≈ 0.63, RSI 27 ≈ 0.81, RSI ≤ 20 → 1.0.

La asignación de severidad v1 es **declarada** (juicio del autor del
adaptador, valores tunables en config). La calibración empírica de
severidades con outcomes del audit trail es v2 (§7) — exactamente el
"camino incremental" de ARCHITECTURE_NOTES §4.

### 2.3 Semántica de evaluación

1. **Se evalúan TODAS las reglas** cuyo `applies_to` contiene la acción de la
   propuesta (o `"*"`). No hay cortocircuito ni primera-que-matchea: la
   "matriz señal-vs-señal" se materializa como evaluación exhaustiva del
   ruleset (ver decisión D3 sobre por qué no una matriz autogenerada).
2. Una regla cuya condición referencia una **señal ausente** en el
   `EvidenceSet` (o con tipo incompatible con el operador) **no matchea y no
   lanza**: se registra en `skipped` con el motivo. La ausencia de evidencia
   no es una contradicción (es territorio del Juez 1), pero sí debe ser
   observable en el audit trail.
3. Comparaciones **estrictas o no según declare la regla** — el adaptador
   trading replica exactamente los bordes del código actual (`rsi < 35`,
   `rsi > 65`, `rs_spy < -0.03`, `pct_52w > 0.82`, `|sentiment| > 0.3`,
   todos estrictos).
4. Las contradicciones resultantes se ordenan por `severity` desc, tie-break
   `rule_id` asc — orden total determinista.
5. `matched_rule_ids` incluye también las reglas `kind=SCENARIO` que
   matchearon, ordenadas por `priority` asc: el primer elemento reproduce
   la elección del if/elif actual para generar el escenario del prompt
   (fidelidad validable por test, §5.4).
6. Complejidad O(reglas × cláusulas), sin I/O: presupuesto < 1 ms por
   evaluación (guard de test en §5.5). El fast path del critic ya no es
   excusa para no ejecutar el juez: corre siempre que el flag lo activa,
   incluidas evaluaciones fast-path (evita sesgo de selección en el dataset,
   coherente con el supuesto 3 de ARCHITECTURE_NOTES).

### 2.4 Validación del formato: las 7 reglas actuales como adaptador trading

Traducción 1:1 de la tabla if/elif de `critic_node`
(`graph/trading_graph.py:330-360`). `priority` preserva el orden original;
`scenario`/`key_question` son los strings actuales, sin cambios.

Señales que publica el adaptador (todas ya presentes en `technical_result` /
`sentiment_result`): `rsi`, `trend_up`, `rs_spy`, `pct_52w_range`,
`sentiment`, más las no usadas por reglas pero útiles en `details`
(`volume_ratio`, `buy_votes`, `sell_votes`).

```python
TRADING_RULES = (
    # R1 — orden if/elif #1
    ContradictionRule(
        id="rsi_oversold_vs_sell", kind=CONTRADICTION, applies_to=("SELL",),
        condition=Cmp("rsi", "lt", 35.0),
        pair=("proposal.action", "signal.rsi"),
        direction=ConflictDirection(a_claims="bearish", b_claims="bullish_reversion"),
        severity=SeveritySpec(base=0.60, scale=SeverityScale("rsi", at_base=35, at_max=20)),
        ack=AckSpec(groups=(("rsi", "oversold"),)),
        scenario="RSI in oversold zone but SELL signal — potential contradiction",
        key_question="Is selling justified when RSI signals oversold conditions?",
        priority=10,
    ),
    # R2 — #2
    ContradictionRule(
        id="rsi_overbought_vs_buy", kind=CONTRADICTION, applies_to=("BUY",),
        condition=Cmp("rsi", "gt", 65.0),
        pair=("proposal.action", "signal.rsi"),
        direction=ConflictDirection(a_claims="bullish", b_claims="overextended"),
        severity=SeveritySpec(base=0.60, scale=SeverityScale("rsi", at_base=65, at_max=80)),
        ack=AckSpec(groups=(("rsi", "overbought"),)),
        scenario="RSI in overbought zone but BUY signal — potential overextension",
        key_question="Is buying justified when RSI signals overbought conditions?",
        priority=20,
    ),
    # R3 — #3 (condición compuesta; el "falling knife" del critic)
    ContradictionRule(
        id="buy_against_trend_weak_rs", kind=CONTRADICTION, applies_to=("BUY",),
        condition=All((Cmp("trend_up", "eq", False), Cmp("rs_spy", "lt", -0.03))),
        pair=("proposal.action", "signal.trend_up"),
        contributing=("signal.rs_spy",),
        direction=ConflictDirection(a_claims="bullish", b_claims="bearish_regime"),
        severity=SeveritySpec(base=0.80),   # el escenario de mayor riesgo del sistema
        ack=AckSpec(groups=(("downtrend", "bearish trend"), ("underperform", "relative weakness", "lagging"))),
        scenario="BUY against bearish trend with relative weakness vs SPY",
        key_question="Is it prudent to buy when the ticker underperforms the market in a downtrend?",
        priority=30,
    ),
    # R4 — #4
    ContradictionRule(
        id="buy_near_52w_high", kind=CONTRADICTION, applies_to=("BUY",),
        condition=Cmp("pct_52w_range", "gt", 0.82),
        pair=("proposal.action", "signal.pct_52w_range"),
        direction=ConflictDirection(a_claims="bullish", b_claims="overextended"),
        severity=SeveritySpec(base=0.50, scale=SeverityScale("pct_52w_range", at_base=0.82, at_max=0.97)),
        ack=AckSpec(groups=(("52-week", "52w", "yearly high", "annual high", "near highs"),)),
        scenario="BUY near 52-week highs — potential overextension",
        key_question="Is the price too extended near its 52-week highs?",
        priority=40,
    ),
    # R5 — #5
    ContradictionRule(
        id="buy_vs_negative_sentiment", kind=CONTRADICTION, applies_to=("BUY",),
        condition=Cmp("sentiment", "lt", -0.3),
        pair=("proposal.action", "signal.sentiment"),
        direction=ConflictDirection(a_claims="bullish", b_claims="bearish"),
        severity=SeveritySpec(base=0.55, scale=SeverityScale("sentiment", at_base=-0.3, at_max=-0.8)),
        ack=AckSpec(groups=(("sentiment", "news", "headlines"), ("negative", "bearish", "pessimistic"))),
        scenario="BUY signal but negative sentiment — divergence",
        key_question="Does negative sentiment invalidate the bullish signal?",
        priority=50,
    ),
    # R6 — #6
    ContradictionRule(
        id="sell_vs_positive_sentiment", kind=CONTRADICTION, applies_to=("SELL",),
        condition=Cmp("sentiment", "gt", 0.3),
        pair=("proposal.action", "signal.sentiment"),
        direction=ConflictDirection(a_claims="bearish", b_claims="bullish"),
        severity=SeveritySpec(base=0.55, scale=SeverityScale("sentiment", at_base=0.3, at_max=0.8)),
        ack=AckSpec(groups=(("sentiment", "news", "headlines"), ("positive", "bullish", "optimistic"))),
        scenario="SELL signal but positive sentiment — divergence",
        key_question="Does positive sentiment invalidate the bearish signal?",
        priority=60,
    ),
    # R7 — #7: HOLD no es una contradicción — es un disparador de deliberación.
    # kind=SCENARIO lo expresa en el mismo formato sin contaminar el contrato
    # de salida (no emite Contradiction; solo scenario/key_question para el prompt).
    ContradictionRule(
        id="hold_review", kind=SCENARIO, applies_to=("HOLD",),
        condition=Always(),
        scenario="HOLD signal — evaluate if there is reason to act",
        key_question="Is there a clear reason to act, or is holding the correct decision?",
        priority=70,
    ),
    # Fallback — el `else` actual ("aligned signals"), mismo formato:
    ContradictionRule(
        id="aligned_signals_review", kind=SCENARIO, applies_to=("*",),
        condition=Always(),
        scenario="Aligned signals — verify coherence",
        key_question="Are all indicators pointing in the same direction?",
        priority=999,
    ),
)
```

**Ganancia inmediata que valida el diseño**: con el código actual, un BUY con
RSI 72, pct_52w 0.90 y sentiment −0.45 solo desafía R2 (primera que matchea).
El juez reporta R2 + R4 + R5 simultáneamente, cada una con su severidad —
exactamente la carencia señalada en §4 de ARCHITECTURE_NOTES.

### 2.5 Notas de fidelidad con el comportamiento actual

- **Bordes estrictos**: todos los umbrales replican las desigualdades
  estrictas del código actual (RSI 35/65 — nótese que el critic usa 65,
  no el 60/70 del technical_agent; el adaptador replica al critic).
- **RSI ausente**: el código actual hace `technical.get("rsi", 50)` → 50 no
  matchea ninguna rama. En el juez, señal ausente ⇒ regla `skipped`. Mismo
  resultado observable (cero contradicciones RSI), mejor trazabilidad — el
  adaptador NO inventa el default 50.
- **`trend_up`**: el critic lo recalcula del df (SMA20 > SMA50 última fila);
  `technical_result["trend_up"]` es el mismo valor. El adaptador usa el del
  technical_result (evita pasar el DataFrame).
- **Solapamiento con el falling-knife del technical_agent**
  (`agents/technical_agent.py:119-128`): esos filtros mutan la señal *aguas
  arriba* (penalizan confidence, pueden degradar a HOLD) y usan umbral
  `rs_spy < 0.0`, no `< -0.03`. NO se duplican como reglas del juez en v1:
  R3 ya cubre el patrón con el umbral del critic, y la evaluación exhaustiva
  hace inocuo el solape si en el futuro se quisieran añadir como reglas de
  observabilidad (severidad informativa).

---

## 3. `acknowledged` v1 — match léxico contra el rationale

### 3.1 Mecanismo exacto

Por regla, un `AckSpec` con **grupos de términos** (semántica AND-de-grupos,
OR-dentro-de-grupo):

```python
@dataclass(frozen=True)
class AckSpec:
    groups: tuple[tuple[str, ...], ...]
    # acknowledged=True ⇔ para CADA grupo, al menos un término matchea el rationale
```

Pipeline determinista:

1. **Normalización** (rationale y términos, idéntica):
   Unicode NFKD → eliminar diacríticos → `casefold()` → tokenizar con `\w+`
   (números incluidos como tokens).
2. **Match de término**: un término es una secuencia de 1..n palabras; matchea
   si sus tokens aparecen como **subsecuencia consecutiva** de los tokens del
   rationale. Sin stemming, sin wildcards, sin ventana de proximidad en v1.
   ("52-week" normaliza a tokens `["52","week"]` y matchea "52 week high".)
3. **Veredicto**: AND sobre grupos, OR dentro de grupo. Rationale vacío o
   `AckSpec` ausente ⇒ `acknowledged=False`.
4. **Trazabilidad**: `acknowledged_by` lista los términos que matchearon
   (uno por grupo como mínimo), para poder auditar *por qué* se consideró
   reconocida.

La semántica de grupos existe para reducir falsos positivos: R5 exige
mencionar el *concepto* ("sentiment"/"news") Y su *polaridad*
("negative"/"bearish") — mencionar "news" a secas no basta.

### 3.2 Limitaciones documentadas (v1, asumidas)

Falsos positivos esperables:
- **Ceguera a la negación**: "RSI is *not* oversold" matchea `("rsi","oversold")`
  → acknowledged=True incorrecto. (Nota: "RSI is oversold but I proceed
  anyway" es un *true* positive — reconocer y descartar ES acknowledgment.)
- **Mención en otro contexto**: el rationale menciona "sentiment" hablando de
  otra cosa (p.ej. sentiment histórico) y el grupo de polaridad matchea por
  otra frase — sin ventana de proximidad no se distingue.

Falsos negativos esperables:
- **Paráfrasis y sinónimos no enumerados**: "the oscillator sits at 28" no
  menciona "rsi" ni "oversold". Mitigación: enumerar inflexiones en los
  grupos (coste de mantenimiento asumido).
- **Menciones puramente numéricas**: "RSI=28, still buying" matchea "rsi"
  pero no el grupo de concepto "oversold" → False siendo True.
- **Idioma**: el matching es literal; los rationales del lab serán en inglés
  (regla del proyecto: prompts LLM en inglés). Si un dominio consumidor usa
  otro idioma, sus AckSpec deben enumerar términos en ese idioma.
- **Sin stemming**: "pessimism" no matchea "pessimistic"; se enumeran ambas.

Estas limitaciones son el precio de un mecanismo determinista, barato y
100% testeable. La v2 natural (match semántico por embeddings, o verificación
por LLM) se descarta deliberadamente en v1 (decisión D4).

### 3.3 Situación en el lab: hoy no hay rationale

**Hecho relevante**: la propuesta actual (`technical_result`) no trae texto —
el technical_agent emite votos, no razonamiento. Por tanto, en la integración
inicial `Proposal.rationale = ""` y `acknowledged` será `False` en todas las
contradicciones del lab.

Decisión (D6): NO se sintetiza un pseudo-rationale desde los votos (matchear
léxicamente contra texto generado por plantilla sería circular: mediríamos la
plantilla, no al agente). El campo queda diseñado, implementado y testeado
para los consumidores cuyo proponente sí razona en texto (agentes LLM — el
target real de la librería), y para el propio lab cuando exista el Narrative
Agent o propuestas con tesis textual. El audit trail registrará
`rationale_present: false` para que la analítica posterior no confunda
"no reconocido" con "no evaluable".

---

## 4. Integración con el lab

### 4.1 Feature flag y modos

`config/tribunal_config.py` (nuevo, mismo patrón que `broker_config.py`):

```python
class ContradictionJudgeMode(StrEnum):
    OFF = "off"        # DEFAULT — comportamiento actual intacto, el juez no se importa
    SHADOW = "shadow"  # el juez corre y se audita; CERO efecto en scenario/veredicto/score
    ACTIVE = "active"  # el juez además selecciona scenario/key_question del prompt

CONTRADICTION_JUDGE_MODE = ContradictionJudgeMode.OFF
```

- **OFF (default)**: `critic_node` no cambia en nada. Ni un import en caliente.
- **SHADOW**: primer despliegue real. El juez se ejecuta sobre cada
  evaluación (también las fast-path), su veredicto va a `critic_result` y al
  audit trail, y **nada más**: el if/elif actual sigue eligiendo el
  escenario, el LLM sigue emitiendo el veredicto, `decision_node` no se toca.
  Coherente con la regla 4 del proyecto: funcionalidad → **medición** → mejora.
- **ACTIVE**: el escenario y la key question del prompt salen del juez —
  la regla matcheada de menor `priority` (sea CONTRADICTION o SCENARIO),
  reproduciendo exactamente la elección del if/elif actual (fidelidad total,
  coherente con D8 y validado por el test de equivalencia §5.4). La selección
  por máxima severidad queda como opción configurable futura (§7), a decidir
  con datos de la fase shadow. El veredicto sigue siendo del
  LLM y la penalización sigue siendo ×0.85 binaria: **en v1 el juez no toca
  el score** (decisión D8; la agregación por severidad es la
  `AggregationPolicy` futura de §4 de ARCHITECTURE_NOTES).

### 4.2 Puntos de contacto (4 ficheros, cambios aditivos)

1. **`tribunal/`** (nuevo paquete, core puro — futura librería):
   `contracts.py`, `conditions.py`, `engine.py`, `ack.py`. Restricción dura:
   solo stdlib, cero imports del lab (test de arquitectura §5.5).
2. **`graph/contradiction_adapter.py`** (nuevo, lado lab): construye
   `Proposal` + `EvidenceSet` desde `TradingState` (mismo mapeo que ya hace
   `audit/decision_audit._build_record` para el bloque `evidence`) e invoca
   `tribunal.evaluate()` con `TRADING_RULES`.
3. **`config/contradiction_rules.py`** (nuevo): el ruleset de §2.4.
4. **`graph/trading_graph.py` — `critic_node`** (~10 líneas aditivas):

```python
# al inicio de critic_node, antes del fast path:
judge_block = None
if CONTRADICTION_JUDGE_MODE != OFF:
    try:
        judge_block = run_contradiction_judge(state)   # adaptador; puro, <1 ms
    except Exception as exc:
        judge_block = {"error": f"{type(exc).__name__}: {exc}"}   # fail-open

# ambos returns (fast path y deliberación) añaden la clave aditiva:
critic_result["contradiction_judge"] = judge_block   # None si mode=OFF

# solo en ACTIVE, tras el bloque if/elif actual:
if CONTRADICTION_JUDGE_MODE == ACTIVE and judge_block and not judge_block.get("error"):
    scenario, key_question = scenario_from_verdict(judge_block)  # equivalente al if/elif
```

**Fail-open** (misma política que el critic): cualquier excepción del juez se
captura, se registra en el bloque y el pipeline continúa sin él. Un bug del
juez jamás bloquea el ciclo diario. La política es declarable por juez en la
librería (`fail_policy: open|closed`), pero el lab usa `open`.

`decision_node` y `execution_node`: **sin cambios** en v1.

### 4.3 Audit trail — campos aditivos

`audit/decision_audit._build_record` añade un bloque top-level `judges`
(preparado para los Jueces 1 y 3 futuros), leído de
`critic_result["contradiction_judge"]`. `schema_version` se mantiene en 1:
la política del audit trail es que los readers toleren campos ausentes/extra,
y este cambio es puramente aditivo (decisión D10).

```json
{
  "schema_version": 1,
  "eval_id": "AMD_20260704_203045",
  "...": "... (bloques existentes intactos: proposal/evidence/critic/decision/linkage)",
  "judges": {
    "contradiction": {
      "mode": "shadow",
      "engine_version": "contradiction-judge/1.0.0",
      "rules_version": "a3f9c2e81b04",
      "rationale_present": false,
      "contradictions": [
        {
          "rule_id": "rsi_overbought_vs_buy",
          "pair": ["proposal.action", "signal.rsi"],
          "direction": {"a_claims": "bullish", "b_claims": "overextended"},
          "severity": 0.79,
          "level": "high",
          "acknowledged": false,
          "acknowledged_by": [],
          "contributing": [],
          "details": {"rsi": 72.1, "threshold": 65.0}
        },
        {
          "rule_id": "buy_near_52w_high",
          "pair": ["proposal.action", "signal.pct_52w_range"],
          "direction": {"a_claims": "bullish", "b_claims": "overextended"},
          "severity": 0.63,
          "level": "medium",
          "acknowledged": false,
          "acknowledged_by": [],
          "contributing": [],
          "details": {"pct_52w_range": 0.90, "threshold": 0.82}
        }
      ],
      "matched_rule_ids": ["rsi_overbought_vs_buy", "buy_near_52w_high"],
      "skipped": [],
      "duration_ms": 0.4,
      "error": null
    }
  }
}
```

Esto habilita directamente la medición de la fase shadow: cruzar
`judges.contradiction.contradictions` con los outcomes contrafactuales
(`linkage`) responde "¿las contradicciones de severidad alta predicen peores
retornos que el veredicto binario del LLM?" — el paso 2 del camino
incremental de ARCHITECTURE_NOTES §4, sin escribir ni una regla más.

---

## 5. Plan de tests

Este módulo debe ser el mejor testeado del repo: es puro, determinista y sin
I/O — no hay excusa. Ficheros: `tests/test_tribunal_contracts.py`,
`test_tribunal_engine.py`, `test_tribunal_ack.py`,
`test_contradiction_adapter.py`, `test_critic_judge_integration.py`.

### 5.1 Contratos (`contracts.py`)

- C1. Todos los tipos son frozen: mutar un campo lanza `FrozenInstanceError`.
- C2. Round-trip `to_dict()` → `json.dumps` → `from_dict()` == original,
  para cada tipo y para un `ContradictionVerdict` completo anidado.
- C3. `to_dict()` produce solo tipos JSON-safe (str/int/float/bool/None/list/dict).
- C4. `from_dict()` tolera claves desconocidas (forward-compat) y aplica
  defaults en claves opcionales ausentes (backward-compat).
- C5. Validación en construcción: `severity` fuera de [0,1] lanza;
  `kind=CONTRADICTION` sin `pair`/`direction`/`severity` lanza;
  `SeverityLevel` derivado coincide con los cortes 0.40/0.70 (bordes exactos).

### 5.2 Motor (`engine.py`, `conditions.py`)

Condiciones:
- E1. Cada operador de `Cmp` (lt/le/gt/ge/eq/ne/in) con casos true/false y
  **valor exactamente en el borde** (estricto vs no estricto).
- E2. `All`/`Any_`/`Not_` anidados a ≥3 niveles; `All(())` = True,
  `Any_(())` = False (convención documentada); `Always` matchea siempre.
- E3. Señal ausente en cualquier `Cmp` de la regla ⇒ regla completa a
  `skipped` con `reason="missing_signal:<name>"`, aunque otras cláusulas
  fueran decidibles (no hay evaluación parcial).
- E4. Tipo incompatible (p.ej. `lt` sobre `str`) ⇒ `skipped` con
  `reason="type_mismatch:<name>"`, nunca excepción.

Evaluación exhaustiva:
- E5. Con N reglas que matchean simultáneamente, el veredicto contiene las N
  contradicciones (el caso BUY + RSI 72 + 52w 0.90 + sentiment −0.45 → 3).
- E6. `applies_to` filtra: regla de SELL no se evalúa para BUY (y no aparece
  en `skipped` — no aplicable ≠ no evaluable); `("*",)` aplica a todo.
- E7. Orden de salida: severity desc, tie-break rule_id asc; estable entre
  ejecuciones (determinismo byte a byte del `to_dict()`).
- E8. Ruleset vacío ⇒ veredicto vacío válido (no error).
- E9. `matched_rule_ids` incluye reglas SCENARIO ordenadas por priority;
  las SCENARIO nunca aparecen en `contradictions`.
- E10. `rules_version`: mismo ruleset ⇒ mismo hash; cambiar un umbral ⇒ hash
  distinto; el orden de declaración de reglas no afecta al hash (forma canónica).

Severidad:
- E11. Sin scale ⇒ severity == base exacta.
- E12. Escala ascendente y descendente (RSI oversold: at_base=35 > at_max=20):
  interpolación correcta en punto intermedio, clamp en ambos extremos.
- E13. Degenerada `at_base == at_max` ⇒ severity = 1.0 al matchear (documentado),
  sin división por cero.
- E14. Señal del scale ausente pero condición decidible ⇒ severity = base
  (el scale degrada con gracia, no invalida la regla).

Pureza:
- E15. `evaluate()` no muta proposal/evidence/rules (deepcopy antes/después).
- E16. Property-based (hypothesis): evidencia y reglas aleatorias bien
  formadas ⇒ nunca lanza; toda severity ∈ [0,1]; toda contradicción
  proviene de una regla CONTRADICTION cuyo applies_to contiene la acción.

### 5.3 Acknowledged (`ack.py`)

- A1. Término single-word: match exacto de token; "rsi" NO matchea "rsix"
  (no es substring matching).
- A2. Término multi-word: subsecuencia consecutiva ("52-week" matchea
  "near its 52 week high"; no matchea "52 items this week").
- A3. Case-insensitive y diacríticos ("SOBREVENDIDO" vs "sobrevendido",
  "análisis" vs "analisis").
- A4. Semántica de grupos: 2 grupos, solo uno matchea ⇒ False; ambos ⇒ True.
- A5. `acknowledged_by` contiene exactamente los términos matcheados.
- A6. Rationale vacío ⇒ False; AckSpec None ⇒ False; ambos con
  `acknowledged_by == ()`.
- A7. Puntuación y saltos de línea en el rationale no rompen la tokenización.
- A8. **Tests de caracterización de las limitaciones conocidas** (documentan,
  no aspiran): la negación "not oversold" da True (FP asumido);
  "oscillator at 28" da False (FN asumido). Si alguien "arregla" esto sin
  querer, el test le obliga a leer §3.2.

### 5.4 Adaptador trading (`graph/contradiction_adapter.py` + ruleset)

- T1. Cada una de las 6 reglas CONTRADICTION dispara con evidencia sintética
  que replica su rama del if/elif, y NO dispara justo en el borde
  (rsi=35.0 exacto ⇒ no matchea, como el `< 35` actual; ídem 65, −0.03,
  0.82, ±0.3).
- T2. **Test de equivalencia (golden)**: sobre una rejilla exhaustiva de
  entradas (signal ∈ {BUY,SELL,HOLD} × rsi ∈ {20,34.99,35,50,65,65.01,80} ×
  trend_up ∈ {T,F} × rs_spy ∈ {−0.05,−0.03,0,0.05} × pct_52w ∈ {0.5,0.82,0.9} ×
  sentiment ∈ {−0.5,−0.3,0,0.3,0.5}), el `scenario` seleccionado por
  `scenario_from_verdict()` es **idéntico** al del if/elif actual (copia
  literal del if/elif como fixture de referencia en el test).
- T3. Multi-contradicción: BUY + RSI 72 + pct_52w 0.90 + sentiment −0.45 ⇒
  exactamente {R2, R4, R5}, y el escenario ACTIVE elegido coincide con el
  actual (R2, por priority).
- T4. HOLD ⇒ cero contradicciones + `hold_review` como primer matched;
  señales alineadas (BUY, RSI 50, uptrend, rs_spy +0.05, 52w 0.5,
  sentiment +0.1) ⇒ cero contradicciones + fallback `aligned_signals_review`.
- T5. Mapeo TradingState → EvidenceSet: `technical_result` sin `rsi` ⇒ señal
  ausente (no default 50) ⇒ reglas RSI en `skipped`; `sentiment_result=None`
  ⇒ reglas de sentiment en `skipped`, las demás evalúan.
- T6. `Proposal` del lab: `rationale=""` ⇒ toda contradicción con
  `acknowledged=False` y `rationale_present=False` en el bloque de audit.

### 5.5 Integración con critic_node y audit

- I1. **Caracterización OFF**: con mode=OFF, `critic_result` es idéntico
  clave a clave al actual (sin `contradiction_judge` o a None) y el registro
  de audit no cambia salvo ausencia del bloque `judges`.
- I2. SHADOW: `critic_result["contradiction_judge"]` presente; `scenario`,
  `key_question`, `verdict` y `score` final idénticos a OFF para la misma
  entrada (cero efecto).
- I3. SHADOW con fast path: la evaluación fast-path también lleva el bloque
  del juez (el juez corre antes/independiente del fast path).
- I4. ACTIVE: scenario/key_question provienen del juez; para toda la rejilla
  de T2 el prompt resultante es idéntico al actual.
- I5. Fail-open: el adaptador lanzando (monkeypatch) ⇒ el ciclo completa,
  `contradiction_judge.error` poblado, veredicto LLM intacto.
- I6. Audit: el bloque `judges.contradiction` del JSONL hace round-trip y
  `iter_evaluations` lo devuelve; registros antiguos sin `judges` siguen
  siendo legibles.
- I7. Presupuesto: 1000 evaluaciones del ruleset trading < 1 s total
  (~<1 ms/eval) — guard aproximado, no benchmark estricto.
- I8. **Test de arquitectura**: `tribunal/` no importa nada fuera de stdlib
  (inspección de AST de imports) — la garantía de extraíble como librería.

---

## 6. Decisiones de diseño y alternativas descartadas

**D1 — AST de condiciones tipado, no strings ni callables.**
Descartado `eval`/mini-DSL de strings ("rsi < 35"): necesita parser, invita a
inyección en cuanto las reglas vengan de ficheros, y los errores se descubren
en runtime. Descartados callables Python como formato canónico: no
serializables ⇒ imposible hashear el ruleset (`rules_version`) ni volcar la
regla al audit trail; matan la auditabilidad, que es el producto. Descartada
dependencia tipo json-logic/rule-engine de terceros: peso injustificado para
7 reglas y API ajena. El AST de 5 nodos cubre las 7 reglas actuales con
margen; un `CustomCondition(callable)` como escape hatch explícitamente
no-serializable puede añadirse en v2 si un dominio lo exige.

**D2 — Reglas como dataclasses Python, loader de ficheros en v2.**
YAML/TOML como formato canónico v1 descartado: añade parser+validación por
delante del mismo modelo de datos, y la cultura del repo es config-en-Python
(`broker_config.py`, `monitor_config.py`), con mypy validando gratis. El
modelo se diseña serializable desde el día 1, así que el loader TOML de la
librería pública es una capa fina posterior, no un rediseño.

**D3 — "Matriz" = evaluación exhaustiva de reglas declaradas, no matriz
autogenerada señal×señal.** Se consideró generar automáticamente todos los
pares (señal_i, señal_j) y detectar conflictos por polaridad declarada por
señal. Descartado en v1: exige asignar semántica direccional a cada señal en
cada contexto (¿RSI 30 es "bullish"? solo si crees en mean-reversion), que es
exactamente el conocimiento de dominio que las reglas explícitas capturan
mejor y de forma auditable. El requisito real es "evaluar TODAS las
contradicciones presentes, no la primera" — y eso lo da la evaluación
exhaustiva. La matriz autogenerada queda como exploración v2+ sobre el
dataset del audit trail.

**D4 — `acknowledged` léxico, no embeddings ni LLM.** El mandato del módulo
es determinista y sin LLM. Embeddings introducirían dependencia de modelo,
no-determinismo entre versiones y umbrales de similitud que hay que calibrar
sin dataset. El match léxico es pobre (§3.2) pero es *predeciblemente* pobre,
sus fallos son enumerables y testeables, y `acknowledged_by` hace cada
decisión auditable. Un `AcknowledgementMatcher` como Protocol inyectable deja
la puerta abierta a un matcher semántico en v2 sin tocar el contrato.

**D5 — Severidad declarada con escalado lineal, no aprendida.** No hay aún
dataset para aprender pesos (el audit trail se está acumulando desde esta
fase). Valores iniciales por juicio del autor del adaptador, en config
tunable, con `rules_version` hasheado para poder correlacionar cambios de
severidades con outcomes. La calibración por regresión es v2 y ya está en
los pendientes del proyecto (calibrar pesos con outcomes).

**D6 — No sintetizar rationale desde los votos del technical_agent.**
Matchear términos contra un texto generado por plantilla desde las mismas
señales sería circular: `acknowledged` mediría la plantilla, no si el
*agente* consideró la contradicción. Preferible `False` honesto +
`rationale_present=false` en audit, y que el campo cobre valor con
propuestas LLM (Narrative Agent, o la librería en otros dominios).

**D7 — HOLD y "aligned" como `kind=SCENARIO`, no como contradicciones.**
"HOLD — evaluate if there is reason to act" no afirma ningún par de señales
en conflicto; forzarlo como `Contradiction` de severidad 0 contaminaría el
contrato público y toda la analítica posterior. `RuleKind.SCENARIO` mantiene
las 7 reglas (más el else) en un único formato declarativo — la tabla de
escenarios del prompt pasa a ser data-driven — sin ensuciar la salida.

**D8 — El juez no modifica el score en v1.** Tentador mapear severidad a
penalización graduada (mejor que el ×0.85 binario), pero sería cambiar dos
cosas a la vez: no podríamos atribuir mejoras/regresiones al juez o a la
política. Primero shadow (medir), luego ACTIVE en el prompt (misma política
de efecto), y solo después una `AggregationPolicy` con severidades — con el
dataset del audit trail como evidencia. Es la regla 4 del proyecto aplicada.

**D9 — Fail-open, como el critic actual.** El juez es aditivo a un sistema
en producción cuyo contrato es "el análisis diario siempre completa"
(`APPROVED_ON_ERROR`, `log_evaluation` nunca lanza). La librería declara la
política por juez (`open|closed`) porque otros dominios (aprobación de
crédito, acciones irreversibles) querrán fail-closed; el lab usa open.

**D10 — `schema_version` del audit se queda en 1.** El cambio es
estrictamente aditivo (bloque `judges` nuevo, nada existente cambia) y
`iter_evaluations` ya tolera heterogeneidad entre líneas. Bumpear versión por
adiciones entrenaría a los readers a ignorarla; se reserva el bump para
cambios que rompan (renombrar/retipar/eliminar).

**D11 — `pair` como refs (`"signal.rsi"`), valores en `details`.** Embeber
los valores observados en el par dificultaría agrupar ("¿cuántas veces
contradijo el RSI a la acción este mes?"). Refs estables = claves de
agregación; `details` conserva los valores puntuales para el forense.

**D12 — Paquete `tribunal/` top-level, adaptador en el lado lab.** Meterlo
bajo `audit/` mezclaría el writer JSONL (infra del lab) con el core extraíble.
`tribunal/` con la restricción solo-stdlib (test I8) es la frontera física de
la futura librería; `graph/contradiction_adapter.py` + `config/contradiction_rules.py`
son el primer consumidor y quedan en el repo del lab al extraerla.

**D13 — En `Any_`, una cláusula no evaluable skipea la regla entera.** Aunque
otra cláusula del `Any_` sea decidible como true (y lógicamente bastaría para
el OR), una señal ausente en cualquier `Cmp` de la condición envía la regla
completa a `skipped` — es la semántica de E3: no hay evaluación parcial.
Conservadurismo deliberado v1: preferimos no emitir una contradicción cuya
condición no pudimos evaluar íntegramente. No afecta al ruleset actual (la
única condición compuesta es R3, y es `All`), pero se documenta para no
sorprender a quien añada reglas con `Any_`.

---

## 7. Futuro (fuera de alcance v1, para no perderlo)

- **Calibración de severidades** por regresión sobre outcomes T+5/T+20 del
  audit trail + ledger (reutiliza el join de `linkage`).
- **`AggregationPolicy`**: severidad → penalización graduada del score,
  sustituyendo el ×0.85 binario; requiere lo anterior.
- **Jueces 1 y 3** compartiendo `Proposal`/`EvidenceSet`/bloque `judges` del
  audit — este contrato ya les deja el sitio (por eso `Signal.observed_at`
  existe sin que el Juez 2 lo use).
- **Selección de scenario por máxima severidad** en modo ACTIVE, como
  alternativa configurable a la selección por `priority` (hoy: fidelidad con
  el if/elif); decidir con los datos de la fase shadow.
- **Matcher semántico de `acknowledged`** (Protocol inyectable, D4).
- **Reconsiderar el nombre `ContradictionRule`** antes de la extracción
  física como API pública: una regla `kind=SCENARIO` no es una contradicción
  (tensión introducida por D7). Candidatos: `JudgeRule`, `Rule`.
- **Loader TOML/YAML** del ruleset + JSON Schema publicado para validación
  externa (D2).
- **Extracción física** del paquete: `tribunal/` ya no importa nada del lab;
  quedará mover directorio + publicar, con el adaptador trading como ejemplo
  de referencia en la doc de la librería.
