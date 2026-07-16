# AI Trading Lab — Architecture V2 Decisions

Decisiones de diseño razonadas. No son pendientes inmediatos — son
la dirección acordada hacia la que evoluciona el sistema.
Actualizado: 2026-04-07

---

## 1. Restructuración del universo de tickers

### Problema con el modelo actual
El dict `TICKERS` es un universo plano. Todos los tickers tienen el mismo
rol implícito: generar señales operables. Esto mezcla activos con funciones
incompatibles — el Critic Agent no puede tener umbrales calibrados si entrena
sobre AMD y VEEV simultáneamente como si fueran equivalentes.

### Modelo propuesto: cuatro roles explícitos

**CORE** — Tickers donde el sistema genera edge real
- Cobertura mediática suficiente para FinBERT (5-25 artículos/30 días)
- Volatilidad suficiente para que RSI/SMA generen señales con contenido
- Tesis temática clara y verificable
- Han superado las dos fases de promoción (ver sección 4)

**EXPLORATION** — Tickers en período de observación
- Entran aquí todos los tickers nuevos
- El sistema genera señales pero no ejecuta posiciones reales
- Se promueven a CORE tras superar métricas de signal quality
- Se degradan a EXPLORATION desde CORE si pierden cobertura mediática

**STABILIZER** — Tickers de calibración, no de alpha
- Rol explícito: medir si las señales del CORE son coherentes
- Comportamiento predecible, baja varianza, señales limpias
- Ejemplos: VEEV (pharma SaaS estable), NEE (utility regulada)
- El sistema los usa como referencia de "mercado normal"

**CONTEXT** — Features macro, no operables
- No generan señales de trading
- Alimentan el clasificador de régimen del Critic Agent
- QQQ, SPY, VIX, US10Y
- Se descargan en cada ciclo de análisis vía yfinance

### Implementación en el dict
```python
TICKERS = {
    "AMD": {
        "thesis": "silicon",
        "role": "core",
        "risk": "medium",
        "type": "standard",
        "thesis_description": "CPU/GPU para AI training e inference",
        "specific_risks": ["competitive pressure from NVIDIA", "custom ASIC adoption"],
    },
    "VEEV": {
        "thesis": "stabilizer",
        "role": "stabilizer",
        "risk": "low",
        "type": "standard",
        "thesis_description": "Pharma SaaS — anchor de calibración",
        "specific_risks": ["competition from Salesforce Health Cloud"],
    },
    "QQQ": {
        "thesis": "context",
        "role": "context",
        "risk": None,
        "type": "context",
        "thesis_description": "Proxy Nasdaq 100 — input régimen macro",
        "specific_risks": [],
    },
}

# Listas derivadas automáticamente
TICKERS_FLAT = [t for t, m in TICKERS.items() if m["role"] != "context"]
CORE_TICKERS = [t for t, m in TICKERS.items() if m["role"] == "core"]
CONTEXT_TICKERS = [t for t, m in TICKERS.items() if m["role"] == "context"]
```

---

## 2. thesis_strength como función computada con caché TTL

### Problema con campo estático
Si `thesis_strength` es un campo en el dict, es una opinión congelada.
Caduca en días para activos con narrativa AI activa.

### Diseño: evaluación lazy con invalidación por evento
```
thesis_strength(ticker):
  1. Consultar ChromaDB: ¿hay evaluación con timestamp < 24h
     Y volumen de noticias nuevas < 5 desde entonces?
     → SÍ: devolver cached
     → NO: recomputar

  2. Recompute:
     - Pull últimas 72h noticias RSS (FinBERT scored)
     - Pull precio vs. peers temáticos (yfinance)
     - Prompt a DeepSeek-R1 con contexto + tesis original
     - Almacenar resultado + timestamp en ChromaDB
```

**Por qué no función pura en cada ejecución:** DeepSeek-R1:32b tarda
30-60 segundos por ticker en la RTX 3090 Ti. 20 tickers = 10-20 minutos
de overhead por ciclo. El caché con TTL da frescura sin ese coste.

---

## 3. Narrative Agent

### Qué hace
Agente semanal que evalúa si cada ticker sigue siendo un representante
válido de su tesis temática. Razonamiento semántico — lo que ningún
sistema técnico (RSI, SMA, momentum) puede hacer.

### Por qué es publicable
Los sistemas cuantitativos usan factores numéricos. Los sistemas
fundamentales usan analistas humanos. Un LLM evaluando coherencia
narrativa automáticamente es nuevo en el espacio retail.

### Prompt base (DeepSeek-R1)
```
La tesis original para incluir {ticker} era: {thesis_description}

Noticias recientes ({N} artículos, últimas 72h):
{news_summaries_with_finbert_scores}

Comportamiento de precio relativo a peers temáticos:
{price_data_vs_peers}

Evalúa:
1. ¿La tesis original sigue siendo válida? (score 0.0-1.0)
2. ¿Ha mutado a una tesis diferente? Si sí, ¿cuál?
3. ¿Es este ticker el mejor representante de su tesis,
   o existe un sustituto más puro?

Razona paso a paso antes de dar scores.
```

### Salvaguarda contra recency bias del LLM
El Narrative Agent no tiene poder unilateral de degradar un ticker.
Genera un `thesis_coherence_score` que es una métrica más dentro del
`signal_quality_score` (ver sección 4). Es un juez del tribunal,
no un dictador.

---

## 4. Mecanismo de promoción/degradación

### Las tres métricas del signal_quality_score

**news_density** — volumen de artículos RSS en ventana 30 días
- Rango óptimo: 5-25 artículos relevantes
- Por debajo: FinBERT no tiene material
- Por encima: ruido domina señal
- No es "más es mejor"

**signal_consistency** — % de ciclos donde RSI + SMA + FinBERT
apuntan en la misma dirección
- Solo informativa si news_density está en rango
- Alta consistencia con news_density baja = ausencia de información,
  no señal limpia

**prediction_calibration** — precisión real de señales vs. precio posterior
- La métrica reina
- Solo disponible después de N semanas de simulación registrada
- No existe para tickers nuevos en Exploration

### Dos fases de promoción (no un umbral único)

**Fase 1: Exploration → Candidato**
Criterio: news_density en rango óptimo durante 4 semanas consecutivas
+ signal_consistency > 60%
No requiere prediction_calibration (aún no hay datos)

**Fase 2: Candidato → Core**
Criterio: prediction_calibration > umbral (a calibrar con datos reales)
tras mínimo 8 semanas de simulación registrada
Requiere tiempo — no hay atajos

### Degradación (más simple que la promoción)
Cualquier ticker CORE que caiga por debajo de news_density mínima
durante 3 semanas consecutivas → baja a EXPLORATION automáticamente.
La ausencia de cobertura es condición suficiente para degradar.
No se necesitan las tres métricas.

---

## 5. Clasificador de régimen macro (Critic Agent)

### Problema con buckets discretos
Definir "risk-on / risk-off / alta volatilidad" como categorías fijas
con umbrales fijos (ej. VIX > 25) es overfitting a los últimos 2 años.
Los regímenes no tienen fronteras claras.

### Diseño: multiplicador continuo sobre el umbral base
```python
critic_threshold_adjustment = f(
    vix_percentile_30d,      # VIX vs. su propia historia reciente
    spy_momentum_20d,         # ¿Mercado en tendencia o sin dirección?
    us10y_rate_of_change      # ¿Yields subiendo rápido?
)
# Resultado: multiplicador 0.8x - 1.3x sobre umbral base del Critic
```

**Por qué percentiles y no valores absolutos:**
VIX en 20 en 2022 era "tranquilo". VIX en 20 en 2017 era "alto".
Comparar contra la historia reciente del propio VIX es más robusto
que comparar contra un número fijo.

**Implementación:** yfinance + Python puro. No requiere ML.
La sofisticación viene de usarlo bien, no de complicar el cálculo.

### Efecto sobre roles de tickers
- Multiplicador bajo (mercado tranquilo): umbrales de confianza se
  relajan, position sizing normal en CORE
- Multiplicador alto (estrés macro): umbrales suben en todo el CORE,
  STABILIZERS reciben más peso relativo

---

## 6. Tax drag en position sizing (España — FIFO)

### Contexto fiscal
En España, plusvalías van a base del ahorro: 19-28% independientemente
del holding period. El drag relativo es lo que importa:
23% sobre un 3% de ganancia en 3 semanas >> 23% sobre 40% en 2 años.

### Penalización por rotación esperada
```python
tax_adjusted_size = base_size * (1 - tax_rate * expected_turnover_ratio)
```

`expected_turnover_ratio` se estima a partir del historial de señales
de ese ticker en simulación. Tickers con señales frecuentes reciben
posiciones más pequeñas automáticamente.

**Por qué es endógeno:** usa datos que el sistema ya genera.
No requiere input manual. Es fiscalmente conservador por construcción.

**Prerequisito:** necesita historial de simulación. Se implementa
cuando el sistema lleve suficientes semanas registrando señales.

---

## 7. Infraestructura híbrida DS220+ / PC con GPU

### Arquitectura objetivo
```
DS220+ (siempre encendido, IP fija LAN)
├── Docker containers:
│   ├── Telegram bot
│   ├── APScheduler (jobs 13:00 / 14:30 / 21:00 CET)
│   ├── Streamlit UI (acceso web siempre disponible)
│   └── ChromaDB (RAG persistente)
└── Wake-on-LAN → PC (.32) antes de jobs que requieren GPU

PC con RTX 3090 Ti (.32)
├── Ollama (DeepSeek-R1:32b, Qwen2.5:14b)
├── analyze_all.py (análisis heavy con LLMs)
└── Se apaga tras completar jobs GPU
```

### Separación de responsabilidades
**DS220+** — siempre encendido, sin GPU:
- Orquestación, scheduling, notificaciones
- UI web (Streamlit no necesita GPU)
- Persistencia (ChromaDB, JSONs, logs)
- Web pública si se expone hacia internet

**PC con GPU** — solo encendido en ventanas de mercado:
- Inferencia LLM (Ollama)
- Análisis técnico + RAG + Critic Agent
- Se levanta por WoL desde el DS220+

### Prerrequisitos técnicos
- DS220+ DSM 7.3.2 con Docker/Container Manager instalado
- PC con WoL habilitado en BIOS (buscar "Wake on LAN" o
  "Power on by PCI-E" en Power Management)
- IP .32 fija para el PC (verificar si es DHCP o estática)
- Red local con broadcast WoL funcionando

### Orden de implementación DS220+
1. Instalar Container Manager en DS220+ (si no está)
2. Verificar/fijar IP .32 en el PC
3. Habilitar WoL en BIOS del PC + probar desde DS220+
4. Dockerizar Telegram bot + scheduler (los menos dependientes de GPU)
5. Dockerizar Streamlit + ChromaDB
6. Implementar job de WoL + healthcheck que confirme que el PC
   respondió antes de lanzar jobs GPU

---

## Orden de implementación global

1. ✅ **Restructurar TICKERS dict** con roles explícitos
   (core/exploration/stabilizer/context + thesis_description)
   → `core/data_loader.py` — completado Sprint 5

2. ✅ **Context features pipeline**
   Descargar QQQ/SPY/VIX/US10Y en cada ciclo +
   implementar critic_threshold_adjustment continuo en Critic Agent
   → `core/market_regime.py` — completado Sprint 5
   → Valor en producción: 1.008x (2026-04-07)

3. ✅ **Infraestructura DS220+**
   WoL + Docker containers básicos (bot + scheduler)
   → `scheduler/ds220_scheduler.py` + `docker/ds220/` — completado Sprint 5
   → Systemd user services en PC también operativos

4. **Narrative Agent v1** ← pendiente semana 4+
   Script semanal con thesis_coherence_score → ChromaDB
   Requiere: 4+ semanas de simulación acumulada

5. **Signal quality score + promoción/degradación** ← pendiente semana 8+
   Requiere semanas de datos de simulación acumulados
   Prerequisito: prediction_calibration script operativo

6. **Tax drag en position sizing** ← pendiente cuando se opere en real
   Cuando el sistema lleve tiempo registrando señales reales
