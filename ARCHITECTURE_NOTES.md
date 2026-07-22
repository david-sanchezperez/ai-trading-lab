# ARCHITECTURE_NOTES — Extracción del analista crítico como librería de auditoría

Fase 1 del proyecto "tribunal de decisiones de agentes": mapa de la arquitectura
actual, análisis de generalización, y propuesta de separación en 3 jueces.
El logging estructurado ya está implementado (ver §3).

> **Supuestos tomados** (preguntados, sin respuesta — revisables):
> 1. "Analista crítico" = `critic_node` + `decision_node` como una única unidad
>    auditable (propuesta → evaluación → veredicto → decisión).
> 2. Backend JSONL (`logs/decision_audit/`), no SQLite/DuckDB.
> 3. Las evaluaciones fast-path también se registran (la decisión de *no*
>    deliberar es auditable y evita sesgo de selección en el análisis posterior).
> 4. Cruce con outcomes por join lógico ticker+fecha contra el ledger — sin
>    tocar el schema de `predictions`.
> 5. El monitor intraday (`monitor/`) queda fuera del alcance de esta fase.

---

## 1. Arquitectura actual

### Componentes y flujo de datos

Pipeline diario (`scripts/analyze_all.py`, invocado por `scheduler/jobs/daily_run.py`
a las 20:30): un `StateGraph` de LangGraph con 7 nodos se ejecuta una vez por
ticker (21 tickers), compartiendo un `TradingState` (TypedDict) que viaja entre nodos.

```
                       ┌─ technical_node ─┐
data_node → indicators ┤                  ├→ critic_node → decision_node → execution_node
                       └─ sentiment_node ─┘
```

| Nodo | Entrada | Salida | Lógica |
|---|---|---|---|
| `data_node` | ticker | `df` OHLCV 1y | caché CSV diaria o yfinance |
| `indicators_node` | df | df + indicadores | SMA20/50, RSI, ATR, vol_ratio, 52w, RS_SPY |
| `technical_node` | df | `technical_result` | votación 6 indicadores → señal + confianza 0.50–0.90 (`agents/technical_agent.py`) |
| `sentiment_node` | ticker | `sentiment_result` | FinBERT sobre RSS Yahoo + StockTwits |
| `critic_node` | todo lo anterior | `critic_result` | **el analista crítico** (ver abajo) |
| `decision_node` | critic + score | `decision` | score 6 componentes + penalización critic + umbral régimen |
| `execution_node` | decision | `execution_result` | guardrails (earnings, cash reserve, position cap, beta cap) + broker |

Fuera del grafo: `core/market_regime.py` calcula un multiplicador macro [0.8, 1.3]
una vez por ciclo; `analytics/prediction_ledger.py` (DuckDB) registra los BUY
ejecutados y resuelve outcomes T+1/T+5/T+20; `core/session_logger.py` escribe
JSONL operacional por nodo (latencias, resúmenes — no es un audit trail de decisiones).

### El analista crítico (`critic_node`, `graph/trading_graph.py:172`)

**Qué recibe** (evidencias):
- Señal técnica propuesta: signal, confidence, votos B/S, RSI, precio, tendencia,
  posición en rango 52w, volume ratio, fuerza relativa vs SPY.
- Sentimiento: score FinBERT, nº titulares, StockTwits (bullish/bearish %).
- Precedentes RAG: 3 situaciones históricas similares de ChromaDB
  (`core/rag_store.get_similar_situations`), con outcomes a 5d/10d si existen.
- Contexto de empresa (`get_company_context`), evento clave del día extraído por
  Qwen3.6 de los titulares (`core/news_analyzer.extract_key_event`), y contexto
  intradía acumulado por el MarketMonitor.
- Preview del score de decisión (llama a `make_decision` antes de deliberar).

**Cómo delibera**:
1. **Fast path** (sin LLM) si el veredicto no puede cambiar el resultado:
   SELL sin posición abierta; señal muy fuerte (|score| > umbral/0.85 ≈ 1.07);
   señal muy débil (|score| < umbral×0.80 ≈ 0.38). → `APPROVED` automático.
2. Si delibera: una **tabla de reglas** detecta el *escenario* a desafiar
   (RSI sobrevendido + SELL, RSI sobrecomprado + BUY, BUY contra tendencia con
   debilidad relativa, BUY cerca de máximos 52w, divergencia señal/sentimiento,
   HOLD, o "señales alineadas") y genera la *key question*.
3. Prompt en inglés a Qwen3.6-35B local (`:8080`, OpenAI-compatible, con
   `reasoning_content`): 5 preguntas de análisis + protocolo de veredicto en la
   última línea (`VERDICT: APPROVED | CHALLENGED`).
4. Si el LLM falla → `APPROVED_ON_ERROR` (**fail-open**: el error no bloquea).

**Qué produce** (`critic_result`): `approved`, `verdict`, `thinking` (CoT),
`reasoning`, `rag_precedents`, `fast_path`, `error` — y desde esta fase también
`scenario`, `key_question`, `fast_path_reason` (campos aditivos).

**Efecto sobre la decisión** (`decision_node`): el veredicto es binario y su
único efecto es `score × 0.85` si `CHALLENGED`. El score de 6 componentes
(`agents/decision_agent.make_decision`) = técnico×confianza + sentimiento×0.6 +
sentimiento histórico×0.3 + win_rate + PEAD + insider; se compara con un umbral
0.7 ajustado por régimen (suelo 0.65) → BUY / SELL / HOLD.

---

## 2. Generalizable vs. acoplado al dominio

### Generalizable (candidato a la librería "tribunal")

| Mecanismo | Dónde vive hoy | Abstracción |
|---|---|---|
| Protocolo de veredicto estructurado (última línea `VERDICT: X`, parseo robusto) | `critic_node` (prompt + parseo) | Contrato juez↔LLM: rúbrica + formato de salida verificable |
| Patrón "escenario a desafiar" (detectar qué contradicción evaluar y formular la key question) | tabla de reglas en `critic_node` | Motor de reglas de contradicción con reglas inyectables por dominio |
| Fast path / *materiality gate* (no deliberar cuando el veredicto no puede cambiar el resultado) | condiciones 0–2 en `critic_node` | Gate genérico: `puede_cambiar_resultado(propuesta, política_de_efecto) → bool` |
| Recuperación de precedentes + inyección en el prompt ("¿qué pasó en situaciones similares?") | `rag_store.get_similar_situations` + sección `precedents` | Interfaz `PrecedentStore` (embedding + metadatos de outcome); ChromaDB es un backend |
| Política de efecto del veredicto (penalización ×0.85, umbral) | `decision_node` | `VerdictPolicy`: cómo un CHALLENGED modifica score/decisión |
| Política ante fallo del evaluador (fail-open `APPROVED_ON_ERROR`) | except de `critic_node` | Decisión explícita fail-open/fail-closed por juez |
| Audit trail de evaluaciones | `audit/decision_audit.py` (nuevo) | Ya diseñado como writer agnóstico + builder por dominio |
| Medición de calibración (Brier, ECE, win rate por bucket de score) | `analytics/prediction_ledger.CalibrationEngine` | Genérico dado (confianza declarada, outcome binario); hoy acoplado a DuckDB+trading |
| Cola LLM con prioridades (urgente vs. batch) | `monitor/llm_queue.py` | Infraestructura reutilizable tal cual |

### Acoplado al dominio (queda en el lab / adaptadores)

- Semántica de los indicadores y las **reglas concretas** de escenario
  (RSI<35, pct_52w>0.82, rs_spy<-0.03…) — el *patrón* generaliza, los umbrales no.
- Composición del score y sus pesos (0.6 sentimiento, 0.3 histórico, PEAD, insider).
- Fuentes de evidencia (FinBERT, StockTwits, SEC EDGAR, yfinance).
- Régimen macro y su multiplicador; guardrails de ejecución (earnings buffer,
  beta cap, cash reserve) — son *risk checks* de dominio, aunque su patrón
  (pre-flight checks vetando una decisión aprobada) sí generaliza.
- Definición de outcome (retorno T+1/T+5/T+20 > 0) y su resolución vía precios.
- El contenido del prompt (aunque su *estructura* — evidencias → preguntas →
  veredicto — es la plantilla generalizable).

**Observación clave para la librería**: hoy el veredicto es binario y global.
No hay objeciones estructuradas (qué evidencia falla, qué par de señales se
contradice, cuánta sobreconfianza) — solo texto libre en `reasoning`. Ese es el
salto principal que los 3 jueces deben dar (§4).

---

## 3. Logging estructurado implementado (Fase 1, entregado)

- **Módulo nuevo**: `audit/decision_audit.py`. Writer JSONL append-only,
  thread-safe, `schema_version`, y `log_evaluation()` que **nunca lanza**.
- **Hook único**: final de `decision_node` (fan-in natural donde ya está todo:
  propuesta, evidencias, critic_result y decisión final). 3 líneas añadidas.
- **Cambios aditivos** en `critic_node`: `scenario`, `key_question`,
  `fast_path_reason` expuestos en `critic_result` (antes solo vivían dentro del prompt).
- **Ficheros**: `logs/decision_audit/YYYY-MM-DD.jsonl`, un registro por
  evaluación (21/día) con bloques `proposal` / `evidence` / `critic` /
  `decision` / `linkage`.
- **Cruce con outcomes**:
  - Con trade: `linkage.ledger_join_key` = `{ticker}_{YYYYMMDD}` casa con el
    prefijo de `predictions.id` (`{ticker}_{YYYYMMDD_HHMM}`) en `data/ledger.duckdb`,
    que ya resuelve win/return T+1/T+5/T+20.
  - Sin trade (la mayoría): `linkage.signal_price` + `ts` permiten resolver el
    outcome contrafactual con el mismo mecanismo que `OutcomeTracker` — esto es
    oro para medir al critic (¿los CHALLENGED evitaron pérdidas reales?).
- **Lectura**: `audit.decision_audit.iter_evaluations(date_str=None)`.

---

## 4. Propuesta: separación en 3 jueces (sin implementar)

Tribunal = N jueces independientes que evalúan la misma `(propuesta, evidencias)`
y emiten veredictos estructurados; una política de agregación los convierte en
efecto sobre la decisión; todo pasa por el audit trail.

### Juez 1 — Evidencia (¿la propuesta está soportada por evidencia suficiente y de calidad?)

- **Mapea a código existente**: recuperación e inyección de precedentes RAG
  (`rag_store.get_similar_situations` + sección `precedents` del prompt);
  conteo de titulares y `sentiment_used`; preguntas 2–3 del prompt ("what do
  the precedents suggest?", "does the trend support the signal?"); el warning
  de `make_decision` cuando falta sentimiento.
- **Falta**: inventario explícito de evidencias con metadatos de frescura y
  fiabilidad por fuente; comprobación afirmación-por-afirmación (¿cada claim
  del razonamiento cita una evidencia disponible, o el LLM inventó?); veredicto
  estructurado `{evidence_coverage, missing_evidence[], stale_evidence[]}`;
  tratamiento de "precedentes sin outcome" como evidencia débil (hoy entran
  al prompt igual que los resueltos).

### Juez 2 — Contradicción (¿hay señales internas que se contradicen y la propuesta lo ignora?)

- **Mapea a código existente**: la tabla de escenarios de `critic_node` es
  exactamente esto en embrión (RSI vs señal, sentimiento vs señal, tendencia+RS
  vs señal, extensión 52w vs BUY); las preguntas 1 y 4 del prompt; los filtros
  "falling knife" del `technical_agent` (penalizan señal contra tendencia+mercado).
- **Falta**: hoy solo se desafía **el primer escenario** que matchea (if/elif) —
  una matriz de pares señal-vs-señal evaluaría *todas* las contradicciones
  presentes; severidad por contradicción en vez de veredicto global; salida
  estructurada `{contradictions: [{pair, direction, severity, acknowledged}]}`
  donde `acknowledged` mide si el razonamiento de la propuesta ya lo tuvo en
  cuenta; reglas declarativas inyectables (las 7 actuales serían el adaptador trading).

### Juez 3 — Riesgo / sobreconfianza (¿la confianza declarada está justificada por el histórico?)

- **Mapea a código existente**: `CalibrationEngine` (Brier, ECE, win rate por
  bucket de score) — pero corre semanalmente como *reporte*, no como juez en el
  momento de decidir; `win_rate_store.get_win_rate_contribution` (prior empírico
  por ticker+setup); umbral por régimen y fast-path cutoffs; guardrails de
  `execution_node` (beta cap, position cap) como veto de riesgo post-decisión.
- **Falta**: cerrar el loop en tiempo de decisión — "declaras confianza 0.85
  pero tu bucket 0.8–0.9 tiene win rate histórico 0.55 → sobreconfianza +0.30";
  posición del score en la distribución histórica (¿es este 0.91 un outlier?);
  chequeo de tamaño muestral (n del bucket) antes de fiarse del prior; veredicto
  `{stated_confidence, empirical_confidence, overconfidence_gap, sample_size}`.
  El audit trail de §3 es precisamente el dataset que alimentará esto.

### Agregación y contrato (esqueleto conceptual)

```
Proposal {action, stated_confidence, rationale, evidence[]}
  → EvidenceJudge.evaluate()      → Verdict{score, objections[]}
  → ContradictionJudge.evaluate() → Verdict{score, objections[]}
  → OverconfidenceJudge.evaluate()→ Verdict{score, objections[]}
  → AggregationPolicy(verdicts)   → Ruling{approved, adjustment, objections[]}
  → AuditTrail.log(proposal, evidence, verdicts, ruling)   # ya existe (§3)
```

- La política actual (binario global → ×0.85) sería la `AggregationPolicy` más
  simple; la librería permitiría ponderar por juez y por severidad.
- Cada juez declara su política ante fallo (fail-open como hoy, o fail-closed).
- El lab quedaría como primer consumidor: adaptador que convierte
  `TradingState` en `Proposal` + `Evidence` (el builder de
  `audit/decision_audit.py` ya hace la mitad de ese mapeo).

### Camino incremental sugerido

1. *(hecho)* Audit trail JSONL — acumular semanas de evaluaciones con outcomes.
2. Resolver outcomes contrafactuales de las evaluaciones sin trade (reutilizando
   `OutcomeTracker`) → medir empíricamente el valor del critic actual
   (¿CHALLENGED predice peores retornos?).
3. Extraer Juez 2 primero (la tabla de escenarios ya existe y es puro código,
   testeable sin LLM), luego Juez 3 (necesita el dataset del paso 1-2),
   y Juez 1 al final (requiere estructurar el inventario de evidencias).
