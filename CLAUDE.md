# AI Trading Lab — Contexto para Claude Code

## Objetivo
Sistema multiagente de trading con IA. Meta: LLM razonando + RAG + agentes cooperando.
Aprendizaje de tecnología cutting-edge, no maximizar PnL inmediato.

## Stack
- Python 3.12.3 · venv en .venv/
- yfinance — datos OHLCV
- llama.cpp-tq3 — SIN APIs de pago en runtime
  - Qwen3.6-35B-A3B TQ3_4S → critic + monitor LLM, API OpenAI-compatible en :8080
    `chat_template_kwargs: {"enable_thinking": True}` → `reasoning_content` field
  - FinBERT (ProsusAI/finbert) → sentiment financiero local
- LangGraph — StateGraph 7 nodos
- ChromaDB — RAG 15.000+ situaciones históricas
- ib_insync + IB Gateway (IBC) → IBKR paper account DU1234567
- DuckDB → prediction ledger (T+1/T+5/T+20 + calibración)
- APScheduler — 5 jobs automáticos
- systemd — ibgateway.service + trading-scheduler.service

## Hardware
- RTX 3090 Ti (24GB VRAM) · 64GB RAM · AMD Ryzen 9 5950X

## Mapa de archivos clave

### Agentes y grafo
- `agents/technical_agent.py` — votación 6 indicadores, confidence 0.55–0.90
- `agents/decision_agent.py` — scoring técnico + sentimiento + histórico + win_rate
- `core/indicators.py` — SMA20/50, RSI, ATR, vol_ratio, pct_52w_range, RS_SPY
- `graph/trading_graph.py` — StateGraph (7 nodos) + TradingState (incl. intraday_context)

### Core
- `core/config.py` — rutas absolutas (pathlib), fuente única
- `core/data_loader.py` — TICKERS dict con roles + fetch/load helpers
- `core/market_regime.py` — multiplicador régimen macro [0.8, 1.3]
- `core/portfolio_sim.py` — portfolio simulado dict serializable
- `core/rag_store.py` — ChromaDB + embeddings + fallback por thesis
- `core/news_fetcher.py` — Yahoo Finance RSS + FinBERT
- `core/news_analyzer.py` — Qwen3.6 extrae evento material de titulares
- `core/social_sentiment.py` — StockTwits API (sin clave)
- `core/entry_tracker.py` — entry points para trailing stops
- `core/win_rate_store.py` — win_rates.json → get_win_rate_contribution()

### Broker
- `brokers/base_broker.py` — interfaz abstracta (nodos nunca importan ib_insync)
- `brokers/paper_broker.py` — wraps portfolio_sim para PAPER_LOCAL
- `brokers/ibkr/broker.py` — IBKRBroker: place_bracket_order, modify_stop, DU-check
- `brokers/ibkr/gateway.py` — conexión IB Gateway, bloqueo puertos live 4001/7496
- `config/broker_config.py` — BrokerMode enum, LIVE_ENABLED=False

### Monitor intraday
- `monitor/market_monitor.py` — 5 jobs APScheduler, 15:30-22:00 CET
- `monitor/watchers/price_watcher.py` — PriceAlert si >1.5×ATR
- `monitor/watchers/news_poller.py` — RSS + FinBERT + LLM (headlines <2h)
- `monitor/watchers/earnings_watcher.py` — yfinance calendar + SEC EDGAR + Qwen3.6
- `monitor/classifiers/event_classifier.py` — DEFENSIVE / OPPORTUNISTIC / ENRICH_EOD
- `monitor/actions/defensive_action.py` — CLOSE_NOW / TIGHTEN_STOP / HOLD_MONITOR
- `monitor/actions/opportunistic_entry.py` — 5 pre-flight checks + score >1.30
- `monitor/llm_queue.py` — PriorityLLMQueue (urgent=0, normal=1, worker único)
- `config/monitor_config.py` — intervalos, umbrales, config LLM

### Analytics
- `analytics/prediction_ledger.py` — PredictionLedger + OutcomeTracker + CalibrationEngine
- `analytics/slippage_analyzer.py` — CSV slippage_log, alerta >200 bps
- `analytics/portfolio_risk.py` — correlación 60d, beta ponderada, HHI, beta cap

### Scheduler y notificaciones
- `scheduler/market_scheduler.py` — 5 jobs: pre_market(13h), daily_run(20:30h), trailing_stop(21:15h), outcome_resolution(21:30h), calibration_report(dom 10h)
- `scheduler/jobs/pre_market.py` — descarga + RAG + win_rates
- `scheduler/jobs/daily_run.py` — pipeline completo + report JSON + Telegram
- `scheduler/jobs/trailing_stop.py` — stops dinámicos + TP1/TP2
- `notifications/telegram_notifier.py` — 6 tipos de alerta

### Datos persistentes
- `data/ledger.duckdb` — predictions + calibration_snapshots
- `data/entry_points.json` — entry prices, ATR, peak prices por posición
- `data/win_rates.json` — win rates por ticker+setup bucket
- `logs/daily_signals.json` — señales del día
- `logs/daily_reports/YYYY-MM-DD.json` — report completo del ciclo
- `logs/intraday_context/YYYY-MM-DD.json` — contexto acumulado MarketMonitor
- `logs/calibration/YYYY-WW.json` — snapshots semanales
- `logs/pnl_history.json` — equity curve diaria

## Estado actual — Sprint 13 completado (2026-05-25)
Sprints 1–13 completados. Sistema en producción con:
- LangGraph 7 nodos · RAG ChromaDB · Qwen3.6 critic · FinBERT sentiment
- IBKR paper mode con guardrails 4 capas + session broker pattern
- MarketMonitor intraday · LLM priority queue
- Prediction ledger DuckDB · calibración automática · slippage tracker (fill_price real)
- Portfolio risk (beta cap con portfolio_value, HHI, correlación)
- Trailing stops + TP1/TP2 · win rates históricos · ATR sizing (fallback price×3.5%)
- Señal PEAD `core/earnings_surprise.py` · Señal insider `core/insider_signal.py` (Form 4 SEC)
- Score 6 componentes en decision_agent · fast path dinámico con regime_adjustment
- Jitter ±25% en backoff IBKR · EDGAR_IDENTITY en config · caché automática de señales
- ChromaDB `status` field backfilled en 16 tickers pre-existentes (total 11.658 docs activos, 21 tickers)
- `sentiment_used BOOLEAN` añadido al Ledger DuckDB; warning log en `make_decision()` si sentiment_result=None

## Universo de tickers (21 operables — actualizado 2026-05-25)
- **CORE (11)**: AMD, AVGO, ASML, TSM, MRVL · ANET, VRT, CEG, VST · META, ORCL
- **STABILIZER (3)**: VEEV, ISRG, COST
- **EXPLORATION (7)**: CRWV, COHR, MU, CRM, APP, AXON, PANW
- **CONTEXT (4, no operables)**: QQQ, SPY, VIX, US10Y
- Retirados en Sprint 13: NEE, RXRX, CRSP, BEAM, WOLF (status=retired en ChromaDB)
- No en sistema (cartera privada): NVDA, PLTR, MSFT, LLY, MA, TSLA, GOOGL

## Reglas — NO romper sin consenso explícito
1. No eliminar separación decision/execution
2. No introducir APIs de pago en runtime (todo local)
3. No romper arquitectura modular existente
4. Funcionalidad → medición → mejora
5. Portfolio personal y simulado son INDEPENDIENTES
6. JSON portfolio personal solo se edita via UI
7. Ningún nodo LangGraph importa ib_insync directamente — solo via BaseBroker
8. Prompts LLM en inglés · Logs en español · Telegram en español

## Convenciones de código
- Usar `df.style.map()` — nunca `df.style.applymap()` (deprecado pandas 2.1.0)

## Pendientes activos

### Fase 2 — En curso
- **Narrative Agent v1**: script semanal Qwen3.6 evalúa thesis_coherence_score por ticker. Necesita 4+ semanas de datos de producción.
- **Calibración threshold**: con suficientes outcomes en DuckDB, mover threshold base 0.7 a Kelly Criterion empírico.

### Nuevas señales — seguimiento post-Sprint 13
- Medir impacto de PEAD e insider en win_rate real (outcome T+5/T+20 en prediction_ledger)
- Calibrar pesos de los 6 componentes del score con regresión sobre outcomes históricos
- Considerar señal Put/Call ratio de CBOE como componente de régimen macro (Fase 3)

### Fase 3 — Mes 2+
- Promoción/degradación automática de tickers (ver docs/ARCHITECTURE_V2.md §4)

### No urgente
- Streaming thinking Qwen3.6 en UI (stream=True + st.write_stream())
- FIFO estricto para fiscal (Hacienda España) — solo necesario antes de operar en real
- Arquitectura híbrida DS220+: scheduler en NAS, PC solo en horario de mercado

## Arquitectura futura
Ver `docs/ARCHITECTURE_V2.md`
