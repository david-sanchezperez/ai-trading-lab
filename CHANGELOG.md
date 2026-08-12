# Changelog

---

## [Unreleased]

### Fixes en el mirror público

- `requirements.txt` — añadido `duckdb==1.5.5` (faltaba pese a ser dependencia
  directa de `analytics/prediction_ledger.py` y `audit/counterfactual.py`;
  `pip install -r requirements.txt` seguido de cualquier import de esos
  módulos, o de `pytest`, fallaba con `ModuleNotFoundError: No module named 'duckdb'`)
- `.envrc.example` — añadido (faltaba en el mirror; el comando
  `cp .envrc.example .envrc` del quick start del README fallaba)
- `README.md` / `README.es.md` — sección "Ticker universe" actualizada al
  universo real de `core/data_loader.py` (VST, COST, APP, AXON, PANW en vez
  de NEE/RXRX/CRSP/BEAM/WOLF, ya retirados; `US10Y` en vez de `^TNX`; 4 tesis
  en vez de 5, `biotech_ai` ya no está en uso)

---

## [0.13.0] - 2026-05-25

### Sprint 13 — Nuevas señales + hardening del sistema

**Nuevas señales en `decision_agent`**
- `core/earnings_surprise.py` — señal PEAD (Post-Earnings Announcement Drift) [-0.15, +0.15]
  - Descarga `earnings_history` de yfinance (4 trimestres)
  - Decay lineal sobre 60 días desde la fecha real del anuncio (`earnings_dates`)
  - Bonus de consistencia si 2+ trimestres consecutivos con sorpresa del mismo signo
  - Caché 24h en `data/cache/earnings_cache.json`
- `core/insider_signal.py` — señal insider transactions (Form 4 SEC EDGAR) [0, +0.20]
  - Solo compras de mercado abierto (`common_stock_purchases`) — ignora ventas y ejercicios
  - Peso 2× para CEO/CFO/President; bonus de cluster buying si ≥3 insiders
  - Caché 24h en `data/cache/insider_cache.json`
- `agents/decision_agent.py` — score ahora suma 6 componentes (antes 4)
- `scripts/analyze_all.py` — campos `pead` e `insider` en `daily_signals.json`
- `scheduler/jobs/pre_market.py` — pasos 5 y 6: precalentamiento de ambas cachés para todos los tickers

**Bugs corregidos**
- `graph/trading_graph.py` — fast path del Critic ahora usa `regime_adjustment` del estado
  para calcular umbrales dinámicos (`strong_cutoff = effective_thr / 0.85`,
  `weak_cutoff = effective_thr × 0.80`) — antes usaba 0.85/0.45 fijos, incorrectos con PEAD+insider
- `core/portfolio_sim.py` — fallback ATR ya no devuelve 50 shares fijos;
  usa `price × 3.5%` como proxy de volatilidad (misma fórmula que el caso normal)
- `analytics/prediction_ledger.py` — `signal_price` y `fill_price` ahora son independientes;
  el slippage se captura correctamente cuando el fill difiere del precio de señal
- `agents/decision_agent.py` — eliminado cálculo de `action` muerto (siempre era
  sobreescrito por `decision_node`); ahora retorna `"HOLD"` como placeholder explícito
- `brokers/ibkr/gateway.py` — jitter ±25% en backoff exponencial para evitar
  thundering herd en reconexiones simultáneas
- `core/earnings_surprise.py` — fecha del anuncio ahora usa `earnings_dates` (fecha real)
  con fallback a `quarter_end + 35d`; antes siempre sumaba 30 días (impreciso para Q4)

**Configuración**
- `core/config.py` — añadido `EDGAR_IDENTITY` (email SEC EDGAR) y `data/cache/` en `ensure_dirs()`
- `core/insider_signal.py` — `set_identity()` usa `EDGAR_IDENTITY` en vez de email hardcodeado
- Ambos módulos de caché crean `data/cache/` con `mkdir(parents=True)` si no existe

---

## [0.8.0] - 2026-04-26

### Sprint 8 — Win Rates + Trailing Stops

**Win rates históricos**
- `scripts/compute_win_rates.py` — win_rate por setup bucket `{signal}_{trend}_{rsi_zone}`
  con 3 años de histórico y recency weighting (half-life 365 días)
- `core/win_rate_store.py` — `get_win_rate_contribution()` integrado en `decision_agent`
  como 4º componente del score (peso ±0.40, escalado por tamaño de muestra)
- Se ejecuta en `pre_market.py` con datos frescos cada día

**ATR sizing activo**
- Fix bug crítico en `execution_node`: `sum(cash, list)` → suma Python correcta
- ATR-based sizing ahora operativo: `compute_quantity_atr()` con 1% risk per trade

**Sistema de trailing stops + take profits**
- `core/entry_tracker.py` — persistencia JSON de entry points con `peak_price` actualizado diariamente
- `scheduler/jobs/trailing_stop.py` — job a las 21:15 CET:
  - Stop dinámico: 2×ATR (días 1-5) → trailing 1.5×ATR (6-10) → trailing 1×ATR (11+)
  - TP1 vende 50% en `entry + 2×ATR`, TP2 vende resto en `entry + 3×ATR`
  - Bootstrap automático para posiciones sin tracking previo
  - Notificación Telegram por cada stop/TP activado
- Entry points registrados automáticamente en `execution_node` en cada BUY/SELL filled
- `scheduler/market_scheduler.py` — añadido tercer job a las 21:15 CET

---

## [0.7.0] - 2026-04-25

### Sprint 7 — Technical Agent experto

**Nuevos indicadores en `core/indicators.py`**
- `volume_ratio` — volumen vs media 20 días
- `pct_52w_range` — posición en rango anual
- `dist_sma20` — distancia % a SMA20
- `add_relative_strength(df, spy_df)` — excess return 20d vs SPY

**Sistema de votación en `agents/technical_agent.py`**
- 6 indicadores votan BUY/SELL independientemente
- Confidence graduada: 0.55 → 0.65 → 0.70 → 0.75 → 0.85
- Volume boost: +0.05 si volume_ratio > 1.5 (cap 0.90)
- Fast path técnico: ≥5 votos netos → confidence 0.85
- Filtro régimen: BUY en downtrend + rs_spy < 0 → penalización -0.15

**Otros**
- SPY descargado y cacheado en `indicators_node`
- 2 nuevos escenarios en `critic_node`
- Fix bug qty=0 en execution_node
- Equity curve en Streamlit Portfolio Sim

---

## [0.6.0] - 2026-04-07

### Sprint 6 — Observabilidad y corrección de calidad

**Bugs corregidos**
- `historical_sentiment` siempre era 0.0 — `save_sentiment()` nunca se llamaba. Corregido en `sentiment_node`
- `data_node` re-descargaba Yahoo aunque el CSV del día existía. Ahora lee CSV si `mtime == today`, descarga con `period="1y"` si no
- Fast path fuerte era inalcanzable: condición `confidence > 0.75` usaba `technical_result["confidence"]` (max 0.7). Reemplazado por `abs(score_preview) > 0.85`
- Umbral fast path débil: `< 0.40` → `< 0.45`
- `sentiment_store.py` usaba path relativo → ahora usa `core/config.py`
- SELL sin posición: marcado como `skipped` antes de llamar a `sim.sell`
- `portfolio total_value` no incluía valor de posiciones abiertas (`market_prices={}`)

**Observabilidad**
- `logs/pnl_history.json` — equity curve diaria acumulada
- `logs/daily_signals_YYYY-MM-DD.json` — archivo histórico por fecha (ya no se sobreescribe)
- Telegram mejorado: top señales con score/RSI/precio, HOLDs bloqueados fuertes, régimen macro

**Timing**
- Análisis movido al cierre: `post_market.py` ejecuta `analyze_all` a las 20:30 CET
- `pre_market.py` (13:00 CET): solo descarga datos + RAG + notifica apertura
- `ds220_scheduler.py`: WoL solo en días NYSE (no fines de semana ni festivos), segundo job a las 20:20 CET

---

## [0.5.0] - 2026-04-04

### Sprint 5 — Productivización

- `core/config.py` — rutas centralizadas con pathlib, fuente única para todo el proyecto
- `core/data_loader.py` — TICKERS flat con roles explícitos (core/stabilizer/exploration/context), 25 tickers en 5 tesis
- `core/market_regime.py` — multiplicador continuo [0.8, 1.3]: VIX percentil 30d + SPY momentum 20d + US10Y RoC 10d
- `core/portfolio_sim.py` — portfolio simulado como dict serializable en LangGraph (10.000€ iniciales)
- RAG incremental: `update_today()` con upsert idempotente por `{ticker}_{date}`
- `scheduler/market_scheduler.py` — APScheduler con jobs pre/post market y calendario NYSE
- `scheduler/jobs/pre_market.py` + `post_market.py` + `notifier.py`
- Systemd user services: `trading-bot.service` + `trading-scheduler.service`, autostart sin login
- `scheduler/ds220_scheduler.py` + `docker/ds220/` — WoL desde Synology DS220+
- `scripts/populate_rag.py` — `populate_new_tickers()` + flag `--new-only`
- Critic fast path: `abs(score) > 0.85` skip DeepSeek (señal fuerte) / `< 0.45` skip (señal débil)

---

## [0.4.0] - 2026-03-31

### Sprint 4 — UI Streamlit

- `app/streamlit_app.py` — entrada principal con 4 vistas
- `app/views/dashboard.py` — señales del día por tesis, colores BUY/SELL/HOLD
- `app/views/analyze.py` — análisis en tiempo real con thinking de DeepSeek
- `app/views/portfolio_sim.py` — trades simulados + resumen fiscal
- `core/fiscal.py` — tracking fiscal con conversión EUR/USD (Hacienda España)

---

## [0.3.0] - 2026-03-30

### Sprint 3 — Noticias reales + Telegram

- `core/news_fetcher.py` — Yahoo Finance RSS + FinBERT (ProsusAI/finbert)
- `sentiment_node` sustituido: FinBERT local sobre titulares reales
- `app/telegram_bot.py` — bot @quantlabaibot con `/analyze` y `/sentiment`
- `scripts/analyze_all.py` — análisis diario de todos los tickers operables
- `logs/daily_signals.json` — señales del día persistidas

---

## [0.2.0] - 2026-03-29

### Sprint 2 — RAG con ChromaDB

- `core/rag_store.py` — ChromaDB persistente, embeddings all-MiniLM-L6-v2
- 5268 situaciones almacenadas: 29 tickers, 1 año de histórico
- `scripts/populate_rag.py` — carga inicial del RAG
- `critic_node` usa RAG: recupera 3 precedentes similares antes de razonar
- Fallback por thesis si no hay resultados por ticker

---

## [0.1.0] - 2026-03-28

### Sprint 1 — LangGraph backbone

- `graph/trading_graph.py` — StateGraph con 7 nodos encadenados
- DeepSeek-R1:32b via Ollama con `think:True` — reasoning interno visible
- `critic_node` con prompt dinámico: detecta contradicciones RSI/señal/sentiment
- `critic_override` flag + penalización ×0.85 sobre score si CHALLENGED
- Position sizing confidence-based (2% risk per trade, 10% max position)
- Indicadores: SMA20, SMA50, RSI, Momentum
- `core/rag_store.py`, `core/sentiment_store.py` — bases de persistencia
