[🇬🇧 English](README.md) · [🇪🇸 Castellano](README.es.md)

---

# AI Trading Lab

Multi-agent AI trading system. Analyses 21 tickers daily, reasons over technical signals and
sentiment using local LLMs, and executes real orders on an IBKR paper account.

> **Goal:** learn cutting-edge AI technology (LangGraph, RAG, local LLMs) by building something
> demonstrable. Not to maximise PnL.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Orchestration | LangGraph StateGraph (7 nodes) |
| Reasoning | Qwen3.6-35B-A3B TurboQuant via llama.cpp-tq3 (~130 t/s, RTX 3090 Ti) |
| Sentiment | FinBERT (ProsusAI/finbert) + StockTwits API |
| Memory | ChromaDB — 15,000+ historical situations with T+1/5/20 outcomes |
| Data | yfinance — daily OHLCV, 3 years |
| Broker | ib_insync → IBKR paper account (IB Gateway via IBC) |
| Analytics | DuckDB — prediction ledger + calibration + slippage |
| Monitor | Intraday Market Monitor — price/news/earnings every 15–30 min |
| Scheduler | APScheduler + systemd services |
| UI | Streamlit |
| Notifications | Telegram |
| Infrastructure | GPU workstation (llama-server + trading-scheduler + ibgateway) |

No paid APIs at runtime. Everything local.

---

## Signal pipeline

```
13:00 CET  — pre_market:         download data + update RAG + win rates
20:30 CET  — daily_run:          full analysis (21 tickers) + Telegram summary
21:15 CET  — trailing_stop:      dynamic stops + take profits (ATR-based)
21:30 CET  — outcome_resolution: resolve T+1/T+5/T+20 in prediction ledger
Sun 10:00  — calibration_report: weekly calibration report

Per ticker (during daily_run):
  data_node        → OHLCV from local CSV (1 year)
  indicators_node  → SMA20, SMA50, RSI, ATR, vol_ratio, RS_SPY
  technical_node   → 6-indicator vote → BUY/SELL/HOLD + confidence
  sentiment_node   → FinBERT + StockTwits (retail tickers)
  critic_node      → Qwen3.6 with RAG precedents + intraday context
                     fast path: score > 0.85 or < 0.45 skips LLM
  decision_node    → score × macro regime → action + dynamic threshold
  execution_node   → IBKR bracket order (entry + stop + TP)
                     beta cap: >1.4 reduce 30%, >1.6 blocked
                     → prediction_ledger + slippage_analyzer
```

**Intraday monitor** (15:30–22:00 CET): price every 20 min · news every 30 min · earnings every 15 min.
Defensive (stop tighten / close) and opportunistic actions via Qwen3.6.

---

## Ticker universe

21 tradeable tickers + 4 context:

- **Core** — AMD, AVGO, ASML, TSM, MRVL, ANET, VRT, CEG, META, ORCL
- **Stabilizer** — VEEV, NEE, ISRG
- **Exploration** — CRWV, COHR, MU, CRM, RXRX, CRSP, BEAM, WOLF
- **Context** (macro regime, not tradeable) — QQQ, SPY, ^VIX, ^TNX

5 theses: silicon · infra_ai · platforms · biotech_ai · stabilizer

---

## Setup

```bash
# Install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Environment variables
cp .envrc.example .envrc   # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
echo "IBKR_PAPER_USER=..." >> ~/.env
echo "IBKR_PAPER_PASS=..." >> ~/.env
chmod 600 ~/.env
source .envrc

# Populate RAG (first time)
python scripts/populate_rag.py
python scripts/compute_win_rates.py
python scripts/enrich_rag_outcomes.py
```

### systemd services

```bash
# IB Gateway (IBC automates login)
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
# Streamlit UI
streamlit run app/streamlit_app.py
```

---

## Structure

```
agents/          technical_agent, decision_agent
analytics/       prediction_ledger (DuckDB), slippage_analyzer, portfolio_risk
brokers/         BaseBroker, PaperBroker, ibkr/ (gateway, broker, orders, portfolio)
config/          broker_config (BrokerMode), monitor_config, schedule_config
core/            config, data_loader, indicators, market_regime,
                 portfolio_sim, rag_store,
                 news_fetcher, news_analyzer, sentiment_store,
                 social_sentiment (StockTwits), entry_tracker, win_rate_store
graph/           trading_graph.py — StateGraph (7 nodes)
monitor/         market_monitor, watchers/ (price, news, earnings),
                 classifiers/ (event_classifier), actions/ (defensive, opportunistic),
                 llm_queue (priority queue)
notifications/   telegram_notifier (6 alert types)
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

## Safety guardrails (IBKR)

The system has 4 independent layers that prevent trading on a live account:

1. `config/broker_config.py` — `LIVE_ENABLED = False` blocks `BrokerMode.LIVE`
2. `brokers/ibkr/gateway.py` — rejects live ports 4001/7496 before connecting
3. `brokers/ibkr/broker.py` — verifies `DU` account prefix after connecting
4. Warning log on every connection: `⚠️ PAPER TRADING MODE`

---

## Hardware

- **GPU**: RTX 3090 Ti (24 GB VRAM) — Qwen3.6-35B-A3B TQ3_4S fits entirely (12.4 GB)
- **RAM**: 64 GB
- **CPU**: AMD Ryzen 9 5950X
- llama.cpp-tq3 fork with TurboQuant kernels, OpenAI-compatible API on :8080
