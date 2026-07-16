# Project State

## 📍 Estado actual (2026-06-05)

Sistema en **producción paper** (IBKR DU1234567). El pipeline completo está operativo
y ejecutándose automáticamente desde 2026-04-06. Sprint 13 completado: 6 señales en el
score de decisión, sistema resiliente a fallos IBKR y cachés de señales externas.

---

## 🧠 Qué funciona

### Pipeline de datos
- Descarga OHLCV diaria con yfinance (1 año de historia, cachés CSV por ticker)
- `data_node` lee CSV del día si existe (evita re-descarga)
- 21 tickers operables (TICKERS_FLAT) + 4 de contexto macro

### Indicadores técnicos (`core/indicators.py`)
- SMA20, SMA50, RSI, ATR(14), volume_ratio, pct_52w_range, RS_SPY

### Agentes y scoring
- `technical_agent` — votación 6 indicadores, confidence 0.55–0.90
- `decision_agent` — score de 6 componentes:
  - `tech_signal × tech_conf` [-0.90, +0.90]
  - `sentiment × 0.6` [-0.60, +0.60] (FinBERT)
  - `historical_sentiment × 0.3` [-0.30, +0.30]
  - `wr_contribution` [-0.40, +0.40] (win rates históricos)
  - `pead_contribution` [-0.15, +0.15] (Post-Earnings Announcement Drift)
  - `insider_contribution` [0, +0.20] (Form 4 SEC EDGAR, compras de mercado)
- `sentiment_node` — FinBERT sobre titulares Yahoo RSS
- `critic_node` — Qwen3.6-35B con RAG de precedentes históricos
  - Fast path dinámico: umbrales calculados con `regime_adjustment` real del ciclo
  - Strong: `abs(score) > effective_threshold / 0.85` → skip Qwen
  - Weak: `abs(score) < effective_threshold × 0.80` → skip Qwen

### Régimen macro (`core/market_regime.py`)
- Multiplicador continuo [0.8, 1.3] sobre umbral base 0.7
- Componentes: VIX percentil 30d + SPY momentum 20d + US10Y RoC 10d

### Portfolio simulado (`core/portfolio_sim.py`)
- 10.000€ iniciales, viaja como dict serializable por el StateGraph
- ATR sizing: `risk_per_trade=1%` con fallback `price × 3.5%` si ATR no disponible
- Persistido en `data/portfolio_sim.json`

### IBKR paper trading (resiliente)
- `brokers/ibkr/gateway.py` — backoff exponencial ±25% jitter, reconexión automática
- `brokers/ibkr/broker.py` — `ensure_connected()` en cada operación
- Session broker pattern: una única conexión para todo el ciclo de 21 tickers
- Guardrails: cash reserve 20%, max position 15%, beta cap 1.4/1.6, earnings buffer

### RAG (`core/rag_store.py`)
- ChromaDB con 15.000+ situaciones históricas
- Fallback por thesis si no hay resultados por ticker
- Actualización incremental diaria (upsert idempotente)

### Scheduler automático
- APScheduler + systemd user service (arranque sin login)
- Pre-market 13:00 CET: descarga → RAG → win rates → PEAD cache → insider cache → Telegram
- Daily run 20:30 CET: pipeline completo → report JSON → Telegram
- Trailing stops 21:15 CET: stops dinámicos + TP1/TP2
- Outcome resolution 21:30 CET: resolución T+1/T+5/T+20 en DuckDB
- Calibración semanal: domingos 10:00 CET

### Analytics
- `analytics/prediction_ledger.py` — DuckDB: señales + fill_price + slippage real
- `analytics/slippage_analyzer.py` — CSV, alerta >200 bps
- `analytics/portfolio_risk.py` — beta cap (con portfolio_value como denominador), HHI, correlación

### Observabilidad
- `logs/daily_signals.json` — señales del día con campos: score, pead, insider, trade
- `logs/daily_reports/YYYY-MM-DD.json` — report completo del ciclo
- `logs/pnl_history.json` — equity curve diaria acumulada
- Telegram @quantlabaibot — resumen diario + alertas de riesgo + slippage

---

## 🔄 Flujo operativo completo

```
pre_market.py (13:00 CET)
  ├── _is_market_open_today()
  ├── load_all_data(period="1y") → CSVs en data/raw/
  ├── update_today() → ChromaDB upsert
  ├── compute_and_save() → win_rates.json
  ├── pead_prefetch(TICKERS_FLAT) → data/cache/earnings_cache.json
  ├── insider_prefetch(TICKERS_FLAT) → data/cache/insider_cache.json
  └── send_notification() → Telegram

daily_run.py (20:30 CET)
  ├── _wait_for_gateway_ready() (solo IBKR modes)
  ├── _is_market_open_today()
  ├── portfolio_risk.compute_daily_risk() → warnings Telegram
  ├── load intraday_context (si existe)
  ├── analyze_all.main(intraday_context)
  │     ├── session_broker.connect() → set_session_broker()
  │     ├── compute_regime_adjustment()
  │     ├── sim.load()
  │     └── for ticker in TICKERS_FLAT:
  │           └── graph.invoke(initial_state)
  │                 ├── data_node
  │                 ├── indicators_node
  │                 ├── technical_node
  │                 ├── sentiment_node
  │                 ├── critic_node (fast path o Qwen3.6)
  │                 ├── decision_node (score × régimen → BUY/SELL/HOLD)
  │                 └── execution_node (IBKR bracket o paper sim)
  ├── sim.save() + pnl_history.json + daily_signals.json
  ├── _get_ibkr_portfolio() o _get_sim_portfolio()
  ├── _save_daily_report()
  ├── CalibrationEngine().generate_report()
  ├── slippage_analyzer.format_for_report()
  └── notify_daily_summary() → Telegram
```

---

## 🐛 Bugs corregidos (2026-06-05)

- **OCA en brackets**: stop y TP ahora tienen `ocaGroup` compartido → cuando uno ejecuta, el otro se cancela automáticamente. Previene posiciones cortas involuntarias (bug que causó ANET -285).
- **Scheduler instancia única**: `_acquire_instance_lock()` con file lock en `market_scheduler.py`. Previene duplicados en ledger por doble proceso.
- **Pending orders check**: `get_pending_buy_tickers()` en IBKRBroker consulta órdenes activas en IBKR antes de colocar nueva BUY → previene GTC duplicadas acumuladas por ticker.
- **local_state cleanup**: trailing_stop limpia entradas de tickers con qty ≤ 0 (posiciones cerradas o short involuntario) al inicio de cada ciclo.
- **IBKR como fuente de verdad**: `pnl_history.json` ahora se escribe desde `daily_run.py` con datos reales de IBKR (base $250k). `analyze_all.py` solo escribe pnl_history en modo PAPER_LOCAL.
- **Streamlit Portfolio Sim**: renombrada a "Portfolio IBKR Paper", lee daily_reports + pnl_history IBKR en lugar de portfolio_sim.json.
- **VRT CSV corruption**: última fila con `Close='VRT'` (string) causaba `str - str` en ATR. ReDescargado y saneado.
- **Deduplicación ledger**: 6 entradas duplicadas eliminadas (causa: doble proceso scheduler).

## ⚠️ Acción manual pendiente en IBKR Paper

Conectarse a la cuenta DU1234567 y:
1. Cancelar todas las órdenes pendientes de **ANET** (stop+TP órfanos que causaron el short)
2. Comprar 285 acc ANET al mercado para cerrar el short involuntario
3. Revisar **APP**: cancelar posibles GTC duplicadas acumuladas (múltiples brackets a $599.89)

## 📊 P&G acumulado (2026-06-05)

| Período | Base | Valor actual | PnL |
|---------|------|--------------|-----|
| Sim local (20-abr → 29-may) | $100k | $112k | **+12.17%** |
| IBKR paper (20-may → 04-jun) | $250k | $255.9k* | **+2.36%*** |

*Distorsionado por short involuntario de ANET. Real (excluyendo ANET): ~+3-4%.

## 🗓️ Estrategia de transición a real

- **Plan**: continuar paper IBKR mínimo 3 meses más (hasta sep/oct 2026) acumulando outcomes T+5/T+20 limpios
- **Condición de entrada real**: calibración DuckDB muestre Brier score < 0.25 y win rate > 55% con ≥ 50 predicciones resueltas
- **Capital inicial real propuesto**: $20-25k (mínimo viable con el sizing actual)
- **No viable < $10k**: comisiones destrozan el edge en posiciones de 1-2 acciones

## ⚠️ Limitaciones conocidas

- Insider signal solo detecta compras de mercado abierto; la mayoría de insiders tech
  operan con RSUs/opciones, por lo que la señal es 0 para la mayoría de tickers
- PEAD usa `earnings_dates` como fecha de anuncio cuando disponible; en tickers sin
  cobertura yfinance, cae back a `quarter_end + 35d`
- Prediction ledger requiere ≥5 días de datos para calibración estadística significativa
- Narrative Agent v1 (thesis_coherence_score) pendiente: necesita 4+ semanas de producción
