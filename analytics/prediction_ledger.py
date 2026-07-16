"""
Prediction Ledger — registra señales y resuelve outcomes a T+1/T+5/T+20.

PredictionLedger  — inserta señales al generar/ejecutar órdenes
OutcomeTracker    — resuelve precios futuros y calcula wins/returns
CalibrationEngine — métricas de calibración (Brier, ECE, win rates)
"""

import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import duckdb

from core.config import PROJECT_ROOT

log = logging.getLogger(__name__)

DB_PATH     = PROJECT_ROOT / "data" / "ledger.duckdb"
_db_lock    = threading.Lock()


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    id                          VARCHAR PRIMARY KEY,
    timestamp                   TIMESTAMP,
    ticker                      VARCHAR,
    action                      VARCHAR,
    score                       FLOAT,
    confidence                  FLOAT,
    signal_price                FLOAT,
    fill_price                  FLOAT,
    slippage_bps                FLOAT,
    quantity                    INTEGER,
    rsi                         FLOAT,
    trend                       VARCHAR,
    momentum_10d                FLOAT,
    rs_spy                      FLOAT,
    atr                         FLOAT,
    dist_sma20                  FLOAT,
    volume_ratio                FLOAT,
    finbert_score               FLOAT,
    news_count                  INTEGER,
    regime_multiplier           FLOAT,
    vix_percentile              FLOAT,
    spy_momentum                FLOAT,
    price_t1                    FLOAT,
    price_t5                    FLOAT,
    price_t20                   FLOAT,
    return_t1                   FLOAT,
    return_t5                   FLOAT,
    return_t20                  FLOAT,
    win_t1                      BOOLEAN,
    win_t5                      BOOLEAN,
    win_t20                     BOOLEAN,
    resolved_t1                 BOOLEAN DEFAULT FALSE,
    resolved_t5                 BOOLEAN DEFAULT FALSE,
    resolved_t20                BOOLEAN DEFAULT FALSE,
    broker_order_id             VARCHAR,
    intraday_context_available  BOOLEAN DEFAULT FALSE,
    critic_reasoning            TEXT,
    universe_entry_date         DATE,
    sentiment_used              BOOLEAN
);

CREATE TABLE IF NOT EXISTS calibration_snapshots (
    snapshot_date   DATE PRIMARY KEY,
    total_signals   INTEGER,
    win_rate_t5     FLOAT,
    avg_return_t5   FLOAT,
    brier_score_t5  FLOAT,
    ece_t5          FLOAT,
    sharpe_30d      FLOAT,
    profit_factor   FLOAT
);
"""


def _connect(db_path: str | Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_DDL)
    return con


# ── Singleton ─────────────────────────────────────────────────────────────────

_ledger_instance: Optional["PredictionLedger"] = None


def get_ledger(db_path: str | Path = DB_PATH) -> "PredictionLedger":
    global _ledger_instance
    if _ledger_instance is None:
        _ledger_instance = PredictionLedger(db_path)
    return _ledger_instance


# ── Universe entry dates ──────────────────────────────────────────────────────
# Fecha en que cada ticker entró al universo operable.
# Tickers sin entrada = pre-tracking (NULL en el ledger — semánticamente correcto).
# Actualizar aquí cada vez que se migre el universo.

_UNIVERSE_ENTRY_DATES: dict[str, date] = {
    # Sprint 13 — 2026-05-25
    "VST":  date(2026, 5, 25),
    "COST": date(2026, 5, 25),
    "APP":  date(2026, 5, 25),
    "AXON": date(2026, 5, 25),
    "PANW": date(2026, 5, 25),
}


def _get_universe_entry_date(ticker: str) -> date | None:
    return _UNIVERSE_ENTRY_DATES.get(ticker)


# ── PredictionLedger ──────────────────────────────────────────────────────────

class PredictionLedger:

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = str(db_path)

    def _con(self) -> duckdb.DuckDBPyConnection:
        return _connect(self.db_path)

    def log_signal(self, state: dict, order_result: dict) -> str:
        """
        Registra una señal/ejecución. Extrae contexto de TradingState.
        Devuelve el prediction_id generado.
        """
        now    = datetime.now()
        ticker = state.get("ticker", "UNKNOWN")
        pred_id = f"{ticker}_{now:%Y%m%d_%H%M}"

        tech    = state.get("technical_result") or {}
        sent    = state.get("sentiment_result") or {}
        dec     = state.get("decision") or {}
        critic  = state.get("critic_result") or {}
        df      = state.get("df")

        # Trend desde SMA
        trend = "sideways"
        if df is not None and len(df) > 0:
            try:
                last = df.iloc[-1]
                if last.get("SMA_20", 0) > last.get("SMA_50", 0):
                    trend = "uptrend"
                elif last.get("SMA_20", 0) < last.get("SMA_50", 0):
                    trend = "downtrend"
            except Exception:
                pass

        # dist_sma20: (price - SMA20) / SMA20
        dist_sma20 = None
        if df is not None and len(df) > 0:
            try:
                last     = df.iloc[-1]
                sma20    = last.get("SMA_20", 0)
                price    = tech.get("price", 0)
                dist_sma20 = (price - sma20) / sma20 if sma20 else None
            except Exception:
                pass

        trade        = order_result.get("trade") or {}
        signal_price = tech.get("price")                    # precio en el momento de la señal
        fill_price   = trade.get("price") or signal_price   # precio de ejecución real
        slip         = None
        if fill_price and signal_price and signal_price > 0:
            slip = round((fill_price - signal_price) / signal_price * 10000, 2)

        row = {
            "id":                         pred_id,
            "timestamp":                  now,
            "ticker":                     ticker,
            "action":                     dec.get("action", "HOLD"),
            "score":                      dec.get("score"),
            "confidence":                 tech.get("confidence"),
            "signal_price":               signal_price,
            "fill_price":                 fill_price,
            "slippage_bps":               slip,
            "quantity":                   trade.get("quantity"),
            "rsi":                        tech.get("rsi"),
            "trend":                      trend,
            "momentum_10d":               None,
            "rs_spy":                     tech.get("rs_spy"),
            "atr":                        tech.get("atr_14"),
            "dist_sma20":                 dist_sma20,
            "volume_ratio":               tech.get("volume_ratio"),
            "finbert_score":              sent.get("sentiment"),
            "news_count":                 sent.get("headlines"),
            "regime_multiplier":          dec.get("regime_adjustment"),
            "vix_percentile":             None,
            "spy_momentum":               None,
            "price_t1":                   None, "price_t5":  None, "price_t20": None,
            "return_t1":                  None, "return_t5": None, "return_t20": None,
            "win_t1":                     None, "win_t5":    None, "win_t20":   None,
            "resolved_t1":                False, "resolved_t5": False, "resolved_t20": False,
            "broker_order_id":            str(order_result.get("order_ids", {}).get("entry", "") or ""),
            "intraday_context_available": bool(state.get("intraday_context")),
            "critic_reasoning":           critic.get("reasoning", "")[:2000],
            "universe_entry_date":        _get_universe_entry_date(ticker),
            "sentiment_used":             bool(sent) and len(sent) > 0,
        }

        cols = ", ".join(row.keys())
        vals = ", ".join(["?" for _ in row])
        with _db_lock:
            con = self._con()
            try:
                con.execute(
                    f"INSERT OR REPLACE INTO predictions ({cols}) VALUES ({vals})",
                    list(row.values()),
                )
            finally:
                con.close()

        log.info(f"[ledger] Señal registrada: {pred_id} action={row['action']} score={row['score']}")
        return pred_id

    def log_fill(self, prediction_id: str, fill_price: float, order_id: str = "") -> None:
        """Actualiza fill_price, slippage_bps y broker_order_id."""
        with _db_lock:
            con = self._con()
            try:
                row = con.execute(
                    "SELECT signal_price FROM predictions WHERE id = ?",
                    [prediction_id],
                ).fetchone()
                if not row:
                    return
                signal_price = row[0]
                slip = None
                if signal_price and signal_price > 0:
                    slip = round((fill_price - signal_price) / signal_price * 10000, 2)
                con.execute(
                    """UPDATE predictions
                       SET fill_price = ?, slippage_bps = ?, broker_order_id = ?
                       WHERE id = ?""",
                    [fill_price, slip, order_id, prediction_id],
                )
            finally:
                con.close()


# ── OutcomeTracker ────────────────────────────────────────────────────────────

class OutcomeTracker:

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = str(db_path)

    def _con(self) -> duckdb.DuckDBPyConnection:
        return _connect(self.db_path)

    def resolve_outcomes(self) -> dict:
        """Resuelve T+1, T+5, T+20 para predicciones pendientes. Devuelve cuántas se resolvieron."""
        today   = date.today()
        counts  = {"t1": 0, "t5": 0, "t20": 0}

        with _db_lock:
            con = self._con()
            try:
                for horizon, days, col_p, col_r, col_w, col_res in [
                    (1,  1,  "price_t1",  "return_t1",  "win_t1",  "resolved_t1"),
                    (5,  5,  "price_t5",  "return_t5",  "win_t5",  "resolved_t5"),
                    (20, 20, "price_t20", "return_t20", "win_t20", "resolved_t20"),
                ]:
                    cutoff = today - timedelta(days=days)
                    pending = con.execute(
                        f"""SELECT id, ticker, signal_price, timestamp
                            FROM predictions
                            WHERE {col_res} = FALSE
                              AND DATE(timestamp) <= ?
                              AND action = 'BUY'
                              AND signal_price IS NOT NULL""",
                        [cutoff],
                    ).fetchall()

                    for pred_id, ticker, signal_price, ts in pending:
                        if isinstance(ts, str):
                            try:
                                ts = datetime.fromisoformat(ts)
                            except Exception:
                                continue
                        target_date = ts.date() + timedelta(days=days)
                        price = _get_price_on_date(ticker, target_date)
                        if price is None:
                            continue
                        ret  = (price - signal_price) / signal_price
                        win  = ret > 0
                        con.execute(
                            f"""UPDATE predictions
                                SET {col_p} = ?, {col_r} = ?, {col_w} = ?, {col_res} = TRUE
                                WHERE id = ?""",
                            [price, round(ret, 6), win, pred_id],
                        )
                        counts[f"t{horizon}"] += 1
            finally:
                con.close()

        if any(counts.values()):
            log.info(f"[outcome_tracker] Resueltos: T+1={counts['t1']}, T+5={counts['t5']}, T+20={counts['t20']}")
        return counts


def _get_price_on_date(ticker: str, target_date: date) -> Optional[float]:
    """Precio de cierre en target_date (o siguiente día hábil disponible)."""
    try:
        import yfinance as yf
        end = target_date + timedelta(days=5)
        hist = yf.Ticker(ticker).history(
            start=target_date.isoformat(),
            end=end.isoformat(),
        )
        if hist.empty:
            return None
        return float(hist["Close"].iloc[0])
    except Exception as e:
        log.warning(f"[outcome_tracker] No se pudo obtener precio de {ticker} en {target_date}: {e}")
        return None


# Public alias — reutilizable desde audit.counterfactual y otros módulos
get_price_on_date = _get_price_on_date


# ── CalibrationEngine ─────────────────────────────────────────────────────────

class CalibrationEngine:

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = str(db_path)

    def _con(self) -> duckdb.DuckDBPyConnection:
        return _connect(self.db_path)

    def compute_metrics(self) -> dict:
        """Calcula métricas de calibración. Persiste snapshot. Devuelve dict."""
        with _db_lock:
            con = self._con()
            try:
                rows = con.execute(
                    """SELECT id, score, win_t5, return_t5, regime_multiplier, ticker
                       FROM predictions
                       WHERE resolved_t5 = TRUE AND action = 'BUY'"""
                ).fetchall()
            finally:
                con.close()

        if len(rows) < 10:
            return {"insufficient_data": True, "n": len(rows)}

        ids, scores, wins, returns, regimes, tickers = zip(*rows)

        # Normalizar score a [0,1] para probabilidad predicha
        min_s, max_s = min(scores), max(scores)
        rng = max_s - min_s if max_s > min_s else 1.0
        probs = [(s - min_s) / rng for s in scores]

        # Brier Score
        brier = sum((p - w) ** 2 for p, w in zip(probs, wins)) / len(probs)

        # ECE — 5 buckets
        buckets = [[] for _ in range(5)]
        for p, w in zip(probs, wins):
            idx = min(4, int(p * 5))
            buckets[idx].append((p, w))
        ece = 0.0
        for bkt in buckets:
            if bkt:
                mean_p = sum(x[0] for x in bkt) / len(bkt)
                mean_w = sum(x[1] for x in bkt) / len(bkt)
                ece   += len(bkt) / len(probs) * abs(mean_p - mean_w)

        # Win rate por bucket de score
        buckets_wr: dict[str, dict] = {}
        for s, w, r in zip(scores, wins, returns):
            b = round(int(s * 10) / 10, 1)
            key = str(b)
            if key not in buckets_wr:
                buckets_wr[key] = {"wins": 0, "total": 0, "returns": []}
            buckets_wr[key]["total"] += 1
            buckets_wr[key]["wins"]   += int(w)
            buckets_wr[key]["returns"].append(r)

        # Win rate por ticker
        ticker_stats: dict[str, dict] = {}
        for t, w, r in zip(tickers, wins, returns):
            if t not in ticker_stats:
                ticker_stats[t] = {"wins": 0, "total": 0, "returns": []}
            ticker_stats[t]["total"] += 1
            ticker_stats[t]["wins"]   += int(w)
            ticker_stats[t]["returns"].append(r)

        # Win rate por régimen
        favorable_wins = sum(w for w, reg in zip(wins, regimes) if reg and reg > 1.0)
        favorable_n    = sum(1 for reg in regimes if reg and reg > 1.0)
        unfav_wins     = sum(w for w, reg in zip(wins, regimes) if not reg or reg <= 1.0)
        unfav_n        = len(wins) - favorable_n

        # Profit factor
        win_sum  = sum(r for r, w in zip(returns, wins) if w)
        loss_sum = abs(sum(r for r, w in zip(returns, wins) if not w)) or 1e-9
        pf       = win_sum / loss_sum

        # Win rate / avg return global
        win_rate   = sum(wins) / len(wins)
        avg_return = sum(returns) / len(returns)

        metrics = {
            "n":             len(rows),
            "win_rate_t5":   round(win_rate, 4),
            "avg_return_t5": round(avg_return, 6),
            "brier_score_t5": round(brier, 4),
            "ece_t5":        round(ece, 4),
            "profit_factor": round(pf, 3),
            "score_buckets": {
                k: {
                    "win_rate": round(v["wins"] / v["total"], 3) if v["total"] else 0,
                    "n":        v["total"],
                    "avg_return": round(sum(v["returns"]) / v["total"], 4) if v["total"] else 0,
                }
                for k, v in sorted(buckets_wr.items())
            },
            "ticker_stats": {
                t: {
                    "win_rate":   round(v["wins"] / v["total"], 3),
                    "n":          v["total"],
                    "avg_return": round(sum(v["returns"]) / v["total"], 4),
                }
                for t, v in sorted(ticker_stats.items(), key=lambda x: -x[1]["wins"] / x[1]["total"])
            },
            "regime_stats": {
                "favorable":   {"win_rate": round(favorable_wins / favorable_n, 3) if favorable_n else 0, "n": favorable_n},
                "unfavorable": {"win_rate": round(unfav_wins / unfav_n, 3) if unfav_n else 0, "n": unfav_n},
            },
        }

        # Guardar snapshot
        with _db_lock:
            con = self._con()
            try:
                con.execute(
                    """INSERT OR REPLACE INTO calibration_snapshots
                       (snapshot_date, total_signals, win_rate_t5, avg_return_t5,
                        brier_score_t5, ece_t5, sharpe_30d, profit_factor)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                    [date.today(), len(rows), win_rate, avg_return, brier, ece, pf],
                )
            finally:
                con.close()

        return metrics

    def generate_report(self) -> str:
        """Reporte Markdown para Telegram. Solo si hay >= 10 predicciones resueltas."""
        metrics = self.compute_metrics()

        if metrics.get("insufficient_data"):
            return f"Insuficientes datos ({metrics['n']}/10 predicciones resueltas)"

        bkt = metrics.get("score_buckets", {})
        tickers = metrics.get("ticker_stats", {})
        top3    = list(tickers.keys())[:3]
        bottom3 = list(tickers.keys())[-3:]

        def _bkt(lo: float) -> tuple[float, int]:
            key = str(round(lo, 1))
            v   = bkt.get(key, {"win_rate": 0, "n": 0})
            return v["win_rate"], v["n"]

        wr_07, n_07 = _bkt(0.7)
        wr_08, n_08 = _bkt(0.8)
        wr_09, n_09 = _bkt(0.9)
        wr_10, n_10 = _bkt(1.0)

        lines = [
            f"📊 *CALIBRATION REPORT — {date.today()}*",
            f"Predictions resolved: {metrics['n']} (T+5)",
            "",
            "*Overall:*",
            f"• Win rate: {metrics['win_rate_t5']:.1%}",
            f"• Avg return T+5: {metrics['avg_return_t5']:+.2%}",
            f"• Brier score: {metrics['brier_score_t5']:.3f} (lower=better, random=0.25)",
            f"• Profit factor: {metrics['profit_factor']:.2f}",
            "",
            "*Score calibration:*",
            f"• Score 0.7-0.8 → win rate {wr_07:.1%} ({n_07} signals)",
            f"• Score 0.8-0.9 → win rate {wr_08:.1%} ({n_08} signals)",
            f"• Score 0.9-1.0 → win rate {wr_09:.1%} ({n_09} signals)",
            f"• Score > 1.0   → win rate {wr_10:.1%} ({n_10} signals)",
        ]
        if top3:
            lines.append(f"\nBest setups: {', '.join(top3)}")
        if bottom3 and bottom3 != top3:
            lines.append(f"Worst setups: {', '.join(bottom3)}")

        return "\n".join(lines)
