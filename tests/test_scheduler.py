"""
Tests del scheduler y notificaciones Telegram.

Requieren:
  - TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en entorno (vía .envrc o .env)
  - IB Gateway activo en localhost:4002 para test_gateway_check

Ejecutar:
    source .venv/bin/activate
    pytest tests/test_scheduler.py -v -s
"""

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def load_env():
    """Carga .env si las variables Telegram no están en el entorno."""
    if not os.environ.get("TELEGRAM_TOKEN"):
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_telegram_send():
    """Envía un mensaje de prueba real al bot de Telegram."""
    if not os.environ.get("TELEGRAM_TOKEN"):
        pytest.skip("TELEGRAM_TOKEN no configurado")

    from scheduler.notifier import send_notification
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"🧪 *TEST scheduler* — {timestamp}\n"
        f"AI Trading Lab — test\\_scheduler.py\n"
        f"Pipeline: PAPER\\_IBKR ✓"
    )
    ok = send_notification(msg)
    assert ok, "send_notification devolvió False — revisar token/chat_id"


def test_daily_report_generated(tmp_path):
    """Verifica que daily_run genera el JSON de report correctamente."""
    mock_report = None

    def fake_save(report):
        nonlocal mock_report
        mock_report = report
        return tmp_path / f"{report['date']}.json"

    signals_fake = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime_adjustment": 1.01,
        "tickers_analyzed": 3,
        "errors": [],
        "summary": {"BUY": ["ANET"], "SELL": [], "HOLD": ["AMD", "AVGO"]},
        "signals": [],
        "portfolio_snapshot": {"cash": 249000.0, "total_value": 250000.0, "positions": {}},
    }
    (tmp_path / "daily_signals.json").write_text(json.dumps(signals_fake))

    with (
        patch("scheduler.jobs.daily_run._is_market_open_today", return_value=True),
        patch("scheduler.jobs.daily_run._is_gateway_up", return_value=True),
        patch("scripts.analyze_all.main"),
        patch("scheduler.jobs.daily_run._save_daily_report", side_effect=fake_save),
        patch("notifications.telegram_notifier.send_notification"),
        patch("scheduler.jobs.daily_run.DAILY_SIGNALS_PATH", tmp_path / "daily_signals.json"),
    ):
        from scheduler.jobs import daily_run
        daily_run.run()

    assert mock_report is not None, "daily_run no llamó a _save_daily_report"
    assert "date" in mock_report
    assert "signals" in mock_report
    assert "portfolio_summary" in mock_report
    assert "cycle_start" in mock_report
    assert "cycle_end" in mock_report
    assert mock_report["regime_multiplier"] == pytest.approx(1.01)
    print(f"\nReport keys: {list(mock_report.keys())}")
    print(f"Signals: {mock_report['signals']}")


def test_gateway_check():
    """Verifica que _is_gateway_up detecta correctamente gateway caído vs activo."""
    from scheduler.jobs.daily_run import _is_gateway_up

    assert _is_gateway_up("127.0.0.1", 1) is False, "Puerto 1 debería estar cerrado"

    gateway_up = _is_gateway_up("127.0.0.1", 4002)
    print(f"\nGateway en 4002: {'UP' if gateway_up else 'DOWN'}")


def test_notify_formatters():
    """Verifica que todos los formatters se ejecutan sin excepciones."""
    from notifications.telegram_notifier import (
        notify_order_executed, notify_stop_hit, notify_tp_executed,
        notify_trailing_stop_updated, notify_daily_summary, notify_critical_error,
    )

    # El mock debe estar en el namespace donde send_notification fue importada
    with patch("notifications.telegram_notifier.send_notification", return_value=True) as mock_send:
        notify_order_executed("ANET", "BUY", 285, 145.03, 130.21, 162.83, 174.21, 1.054, 1.01, 214000, 1)
        notify_stop_hit("ANET", 285, 129.80, 145.03, 214000, 0)
        notify_tp_executed("ANET", "TP1", 142, 162.83, 145.03, 143, 145.03)
        notify_trailing_stop_updated("ANET", 7, 130.21, 138.50, 152.00, 8.80)
        notify_critical_error("IB Gateway caído", "test")
        notify_daily_summary(
            date="2026-05-15", tickers_analyzed=21,
            signals={"BUY": ["ANET"], "SELL": [], "HOLD": ["AMD"]},
            orders_executed=[{"ticker": "ANET"}],
            positions={"ANET": {"pnl_pct": 3.2}},
            cash=248500, positions_value=42300, total_equity=290800,
            equity_pct_change=0.23, regime=1.01, errors=[],
            cycle_start="20:30:01", cycle_end="20:47:23",
        )

    assert mock_send.call_count == 6, f"Esperadas 6 llamadas, got {mock_send.call_count}"
    print(f"\nFormatters OK — {mock_send.call_count} mensajes sin errores")
