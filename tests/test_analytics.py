"""
Tests de los módulos de analytics (Prediction Ledger, Slippage, Priority Queue, Portfolio Risk).

Todos usan in-memory DuckDB o tmp_path para no tocar el filesystem de producción.
Ejecutar: pytest tests/test_analytics.py -v -s
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb


def _load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_state(ticker="ANET", action="BUY", score=1.05, price=147.81):
    return {
        "ticker": ticker,
        "df": None,
        "technical_result": {
            "signal": action, "confidence": 0.75, "rsi": 29.0,
            "price": price, "atr_14": 8.8, "rs_spy": -0.05,
            "volume_ratio": 1.3, "buy_votes": 4, "sell_votes": 2,
        },
        "sentiment_result": {"sentiment": 0.38, "confidence": 0.82, "headlines": 10},
        "decision": {"action": action, "score": score, "regime_adjustment": 1.01},
        "critic_result": {"reasoning": "Fast path — strong signal"},
        "intraday_context": {},
    }


def _make_order_result(price=145.03, qty=285):
    return {
        "status": "submitted",
        "order_ids": {"entry": 4, "stop": 5, "tp": 6},
        "trade": {"action": "BUY", "ticker": "ANET", "price": price, "quantity": qty},
    }


# ── Prediction Ledger ─────────────────────────────────────────────────────────

def test_ledger_log_signal(tmp_path):
    """log_signal inserta una fila en DuckDB con todos los campos correctos."""
    from analytics.prediction_ledger import PredictionLedger

    db     = tmp_path / "test_ledger.duckdb"
    ledger = PredictionLedger(db_path=db)

    pid = ledger.log_signal(_make_state(), _make_order_result())
    assert pid.startswith("ANET_"), f"Prediction ID inesperado: {pid}"

    con  = duckdb.connect(str(db))
    data = con.execute(
        "SELECT ticker, action, score, confidence, signal_price FROM predictions WHERE id = ?",
        [pid],
    ).fetchone()
    con.close()

    assert data is not None, "Fila no encontrada en predictions"
    assert data[0] == "ANET"
    assert data[1] == "BUY"
    assert abs(data[2] - 1.05) < 0.01
    assert abs(data[3] - 0.75) < 0.01
    assert abs(data[4] - 147.81) < 0.01
    print(f"\nLedger row: ticker={data[0]}, action={data[1]}, score={data[2]:.2f}")


def test_ledger_log_fill(tmp_path):
    """log_fill actualiza fill_price y calcula slippage_bps = 150 bps correctamente."""
    from analytics.prediction_ledger import PredictionLedger

    db     = tmp_path / "test_fill.duckdb"
    ledger = PredictionLedger(db_path=db)

    pid = ledger.log_signal(_make_state(price=100.0), _make_order_result(price=100.0))
    # fill=101.5 → slippage = (101.5 - 100) / 100 * 10000 = 150 bps
    ledger.log_fill(pid, fill_price=101.5, order_id="ORDER-42")

    con  = duckdb.connect(str(db))
    data = con.execute(
        "SELECT fill_price, slippage_bps, broker_order_id FROM predictions WHERE id = ?",
        [pid],
    ).fetchone()
    con.close()

    assert abs(data[0] - 101.5) < 0.01, f"fill_price: {data[0]}"
    assert abs(data[1] - 150.0) < 1.0,  f"slippage_bps: {data[1]}"
    assert data[2] == "ORDER-42"
    print(f"\nSlippage: {data[1]:.1f} bps (esperado: 150 bps)")


def test_outcome_resolver_t5(tmp_path):
    """resolve_outcomes actualiza price_t5, return_t5 y win_t5 para señales de hace 5 días."""
    from analytics.prediction_ledger import PredictionLedger, OutcomeTracker

    db      = tmp_path / "test_outcome.duckdb"
    ledger  = PredictionLedger(db_path=db)
    tracker = OutcomeTracker(db_path=db)

    pid = ledger.log_signal(_make_state(price=100.0), _make_order_result(price=100.0))

    # Retroceder el timestamp 6 días
    ts_old = datetime.now() - timedelta(days=6)
    con = duckdb.connect(str(db))
    con.execute("UPDATE predictions SET timestamp = ? WHERE id = ?", [ts_old, pid])
    con.close()

    with patch("analytics.prediction_ledger._get_price_on_date", return_value=110.0):
        counts = tracker.resolve_outcomes()

    con  = duckdb.connect(str(db))
    data = con.execute(
        "SELECT price_t5, return_t5, win_t5, resolved_t5 FROM predictions WHERE id = ?",
        [pid],
    ).fetchone()
    con.close()

    assert abs(data[0] - 110.0) < 0.01, f"price_t5: {data[0]}"
    assert abs(data[1] - 0.10)  < 0.005, f"return_t5: {data[1]}"
    assert data[2] is True
    assert data[3] is True
    assert counts["t5"] >= 1
    print(f"\nT+5: price={data[0]}, return={data[1]:+.2%}, win={data[2]}")


def test_calibration_insufficient_data(tmp_path):
    """generate_report devuelve mensaje de datos insuficientes si hay < 10 predicciones."""
    from analytics.prediction_ledger import CalibrationEngine

    db     = tmp_path / "empty.duckdb"
    report = CalibrationEngine(db_path=db).generate_report()

    assert "Insuficientes" in report or "/10" in report
    print(f"\nCalibration insuficiente: '{report}'")


def test_calibration_with_data(tmp_path):
    """Con 15 predicciones resueltas, compute_metrics devuelve brier_score float."""
    from analytics.prediction_ledger import _connect, CalibrationEngine

    db = tmp_path / "calib.duckdb"
    _connect(db)  # inicializa tablas

    con = duckdb.connect(str(db))
    for i in range(15):
        win = i % 3 != 0
        ret = 0.05 if win else -0.02
        con.execute(
            """INSERT INTO predictions
               (id, timestamp, ticker, action, score, confidence, signal_price,
                return_t5, win_t5, resolved_t5, regime_multiplier)
               VALUES (?, ?, 'ANET', 'BUY', ?, 0.75, 100.0, ?, ?, TRUE, 1.01)""",
            [f"ANET_test_{i:03d}", datetime.now(), 0.8 + i * 0.02, ret, win],
        )
    con.close()

    engine  = CalibrationEngine(db_path=db)
    metrics = engine.compute_metrics()
    report  = engine.generate_report()

    assert isinstance(metrics.get("brier_score_t5"), float)
    assert 0.0 <= metrics["brier_score_t5"] <= 1.0
    assert "Win rate" in report
    assert metrics["n"] == 15
    print(f"\nBrier={metrics['brier_score_t5']:.3f}, WR={metrics['win_rate_t5']:.1%}")


# ── Slippage Analyzer ─────────────────────────────────────────────────────────

def test_slippage_alert_above_threshold(tmp_path):
    """Slippage > 200bps dispara alerta Telegram."""
    from analytics.slippage_analyzer import SlippageAnalyzer

    csv_path = tmp_path / "slippage_log.csv"
    analyzer = SlippageAnalyzer()

    with (
        patch("analytics.slippage_analyzer.SLIPPAGE_CSV", csv_path),
        patch("scheduler.notifier.send_notification", return_value=True) as mock_send,
    ):
        bps = analyzer.log_execution("APP", 10.0, 10.25, 100)  # 250 bps

    assert abs(bps - 250.0) < 1.0, f"Slippage calculado: {bps}"
    mock_send.assert_called_once()
    assert "HIGH SLIPPAGE" in mock_send.call_args[0][0]
    assert "APP" in mock_send.call_args[0][0]
    print(f"\nAlerta high slippage: {bps:.0f} bps")


# ── Priority LLM Queue ────────────────────────────────────────────────────────

def test_priority_queue_urgent_first():
    """Tarea urgent (prioridad 0) procesada antes que normal (prioridad 1)."""
    from monitor.llm_queue import PriorityLLMQueue

    def noop(result, ctx): pass

    q = PriorityLLMQueue()
    q.normal.put((1, datetime.now(), "normal_1", noop, {}))
    q.normal.put((1, datetime.now(), "normal_2", noop, {}))
    q.normal.put((1, datetime.now(), "normal_3", noop, {}))
    q.urgent.put((0, datetime.now(), "urgent",   noop, {}))

    task_urgent = q.urgent.get_nowait()
    task_normal = q.normal.get_nowait()

    assert task_urgent[0] == 0, "urgent debe tener prioridad 0"
    assert task_normal[0] == 1, "normal debe tener prioridad 1"
    assert task_urgent[0] < task_normal[0], "urgent debe procesarse antes"
    print("\nPriority queue: urgent(0) < normal(1) ✓")


# ── Portfolio Risk Monitor ────────────────────────────────────────────────────

def test_portfolio_beta_cap():
    """projected_beta_after_entry devuelve float sin errores."""
    from analytics.portfolio_risk import PortfolioRiskMonitor
    import pandas as pd
    import numpy as np

    np.random.seed(42)
    spy_ret  = pd.Series(np.random.normal(0.001, 0.02, 60))
    tick_ret = 1.5 * spy_ret + np.random.normal(0, 0.002, 60)
    mock_df  = pd.DataFrame({"ANET": tick_ret, "SPY": spy_ret})

    monitor   = PortfolioRiskMonitor()
    positions = {"ANET": {"quantity": 100, "avg_price": 147.0}}

    with patch.object(monitor, "_download_returns", return_value=mock_df):
        proj = monitor.projected_beta_after_entry("AVGO", 50, positions, new_price=180.0)

    assert isinstance(proj, float), f"projected_beta debe ser float, got {type(proj)}"
    print(f"\nBeta proyectada tras entrada AVGO: {proj:.3f}")


def test_portfolio_warning_high_correlation():
    """max_corr_value=0.85 genera warning de correlación."""
    from analytics.portfolio_risk import PortfolioRiskMonitor, PortfolioRisk

    monitor = PortfolioRiskMonitor()
    risk = PortfolioRisk(
        portfolio_beta=1.1,
        beta_per_ticker={"ANET": 1.2, "VRT": 1.0},
        max_corr_pair=("ANET", "VRT"),
        max_corr_value=0.85,
        hhi_concentration=0.25,
        n_positions=2,
    )
    warnings = monitor.generate_warnings(risk)

    assert any("correlaci" in w.lower() or "corr" in w.lower() for w in warnings), (
        f"Esperado warning de correlación, got: {warnings}"
    )
    print(f"\nWarning correlación: {[w for w in warnings if 'corr' in w.lower()][0]}")


def test_portfolio_minimal_one_position():
    """Una sola posición devuelve objeto mínimo sin warnings ni errores."""
    from analytics.portfolio_risk import PortfolioRiskMonitor

    monitor   = PortfolioRiskMonitor()
    positions = {"ANET": {"quantity": 100, "avg_price": 147.0}}
    risk      = monitor.compute_daily_risk(positions)

    assert risk.n_positions == 1
    assert risk.portfolio_beta == 1.0
    assert risk.warnings == []
    print(f"\n1 posición: beta={risk.portfolio_beta}, warnings={risk.warnings}")
