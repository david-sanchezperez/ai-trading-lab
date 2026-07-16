[🇬🇧 English](README.md) · [🇪🇸 Castellano](README.es.md)

---

# AI Trading Lab

Sistema multiagente de trading basado en IA local. Analiza 21 tickers diariamente,
razona con LLMs sobre señales técnicas y sentimiento, y ejecuta órdenes en una cuenta
paper de IBKR.

> **Objetivo:** aprender tecnología AI cutting-edge (LangGraph, RAG, LLMs locales)
> construyendo algo demostrable. No maximizar PnL.

---

## Stack

| Capa | Tecnología |
|------|-----------|
| Orquestación | LangGraph StateGraph (7 nodos) |
| Reasoning | Qwen3.6-35B-A3B TurboQuant via llama.cpp-tq3 (~130 t/s, RTX 3090 Ti) |
| Sentiment | FinBERT (ProsusAI/finbert) + StockTwits API |
| Memoria | ChromaDB — 15.000+ situaciones históricas con outcomes T+1/5/20 |
| Datos | yfinance — OHLCV diario 3 años |
| Broker | ib_insync → cuenta paper de IBKR (IB Gateway vía IBC) |
| Analytics | DuckDB — prediction ledger + calibración + slippage |
| Monitor | Market Monitor intraday — precio/noticias/earnings cada 15-30 min |
| Scheduler | APScheduler + systemd services |
| UI | Streamlit |
| Notificaciones | Telegram |
| Infraestructura | PC con GPU (llama-server + trading-scheduler + ibgateway) |

Sin APIs de pago en runtime. Todo local.

---

## Pipeline de señales

```
13:00 CET  — pre_market:         descarga datos + actualiza RAG + win rates
20:30 CET  — daily_run:          análisis completo (21 tickers) + resumen Telegram
21:15 CET  — trailing_stop:      stops dinámicos + take profits (ATR-based)
21:30 CET  — outcome_resolution: resuelve T+1/T+5/T+20 en prediction ledger
Dom 10:00  — calibration_report: reporte semanal de calibración

Por ticker (durante daily_run):
  data_node        → OHLCV desde CSV local (1 año)
  indicators_node  → SMA20, SMA50, RSI, ATR, vol_ratio, RS_SPY
  technical_node   → votación 6 indicadores → BUY/SELL/HOLD + confidence
  sentiment_node   → FinBERT + StockTwits (tickers retail)
  critic_node      → Qwen3.6 con RAG de precedentes + contexto intraday
                     fast path: score > 0.85 o < 0.45 skip LLM
  decision_node    → score × régimen macro → acción + threshold dinámico
  execution_node   → IBKR bracket order (entry + stop + TP)
                     beta cap: >1.4 reduce 30%, >1.6 bloquea
                     → prediction_ledger + slippage_analyzer
```

**Monitor intraday** (15:30-22:00 CET): precio cada 20 min · noticias cada 30 min · earnings cada 15 min.
Acciones defensivas (stop tighten / close) y oportunistas via Qwen3.6.

---

## Universo de tickers

21 tickers operables + 4 de contexto:

- **Core** — AMD, AVGO, ASML, TSM, MRVL, ANET, VRT, CEG, META, ORCL
- **Stabilizer** — VEEV, NEE, ISRG
- **Exploration** — CRWV, COHR, MU, CRM, RXRX, CRSP, BEAM, WOLF
- **Context** (régimen macro, no operables) — QQQ, SPY, ^VIX, ^TNX

5 tesis: silicon · infra_ai · platforms · biotech_ai · stabilizer

---

## Arranque

```bash
# Instalar dependencias
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Variables de entorno
cp .envrc.example .envrc   # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
echo "IBKR_PAPER_USER=..." >> ~/.env
echo "IBKR_PAPER_PASS=..." >> ~/.env
chmod 600 ~/.env
source .envrc

# Poblar RAG (primera vez)
python scripts/populate_rag.py
python scripts/compute_win_rates.py
python scripts/enrich_rag_outcomes.py
```

### Servicios systemd

```bash
# IB Gateway (IBC automatiza el login)
sudo cp systemd/ibgateway.service /etc/systemd/system/
sudo systemctl enable --now ibgateway

# Trading scheduler
sudo mkdir -p /var/log/trading && sudo chown $USER:$USER /var/log/trading
sudo cp systemd/trading-scheduler.service /etc/systemd/system/
sudo systemctl enable --now trading-scheduler

# llama-server (Qwen3.6 LLM)
systemctl --user enable --now llama-server
```

```bash
# UI Streamlit
streamlit run app/streamlit_app.py
```

---

## Estructura

```
agents/          technical_agent, decision_agent
analytics/       prediction_ledger (DuckDB), slippage_analyzer, portfolio_risk
brokers/         BaseBroker, PaperBroker, ibkr/ (gateway, broker, orders, portfolio)
config/          broker_config (BrokerMode), monitor_config, schedule_config
core/            config, data_loader, indicators, market_regime,
                 portfolio_sim, rag_store,
                 news_fetcher, news_analyzer, sentiment_store,
                 social_sentiment (StockTwits), entry_tracker, win_rate_store
graph/           trading_graph.py — StateGraph (7 nodos)
monitor/         market_monitor, watchers/ (price, news, earnings),
                 classifiers/ (event_classifier), actions/ (defensive, opportunistic),
                 llm_queue (priority queue)
notifications/   telegram_notifier (6 tipos de alerta)
scheduler/       market_scheduler, jobs/ (pre_market, daily_run, trailing_stop),
                 notifier
scripts/         analyze_all, compute_win_rates, enrich_rag_outcomes,
                 populate_rag, e2e_ibkr_test
app/             streamlit_app + views (dashboard, analyze,
                 portfolio_sim, trailing_stops)
systemd/         ibgateway.service, trading-scheduler.service, llama-server.service
tests/           test_ibkr, test_scheduler, test_market_monitor, test_analytics
logs/            daily_signals.json, daily_signals_YYYY-MM-DD.json,
                 pnl_history.json, daily_reports/, intraday_context/, calibration/
data/            ledger.duckdb, portfolio_sim.json, entry_points.json,
                 win_rates.json, chromadb/, raw/, history/
```

---

## Guardrails de seguridad (IBKR)

El sistema tiene 4 capas independientes que impiden operar en cuenta real:

1. `config/broker_config.py` — `LIVE_ENABLED = False` bloquea `BrokerMode.LIVE`
2. `brokers/ibkr/gateway.py` — rechaza puertos live 4001/7496 antes de conectar
3. `brokers/ibkr/broker.py` — verifica prefijo `DU` en cuenta tras conectar
4. Log de advertencia en cada conexión: `⚠️ PAPER TRADING MODE`

---

## Hardware

- **GPU**: RTX 3090 Ti (24GB VRAM) — Qwen3.6-35B-A3B TQ3_4S cabe entero (12.4GB)
- **RAM**: 64GB
- **CPU**: AMD Ryzen 9 5950X
- llama.cpp-tq3 fork con kernels TurboQuant, API OpenAI-compatible en :8080
