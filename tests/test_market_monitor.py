"""
Tests del Market Monitor.

La mayoría usan mocks para no depender de yfinance, IBKR ni LLM.
Ejecutar: pytest tests/test_market_monitor.py -v -s
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Cargar .env para TELEGRAM_TOKEN ──────────────────────────────────────────
def _load_env():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()


# ── price_watcher ─────────────────────────────────────────────────────────────

def test_price_watcher_detects_drop():
    """Posición que cae 2×ATR genera PriceAlert severity=HIGH, direction=DOWN."""
    from monitor.watchers.price_watcher import PriceWatcher

    atr = 5.0
    open_price = 100.0
    drop = open_price - 2.5 * atr     # -12.5% drop, >1.5×ATR

    mock_data = {
        "current_price": drop,
        "open_price":    open_price,
        "volume_today":  1_000_000,
        "avg_volume":    200_000,   # 5× avg → anomaly
        "atr_14":        atr,
    }

    with patch("monitor.watchers.price_watcher._get_intraday", return_value=mock_data):
        pw     = PriceWatcher()
        alerts = pw.check_positions({"ANET": {"quantity": 100, "avg_price": 100.0}})

    assert len(alerts) == 1
    a = alerts[0]
    assert a.ticker    == "ANET"
    assert a.severity  == "HIGH"
    assert a.direction == "DOWN"
    assert a.atr_multiple >= 1.5
    assert a.volume_anomaly is True
    print(f"\nPriceAlert: {a.move_pct:+.2%} | {a.atr_multiple:.1f}×ATR | vol_anomaly={a.volume_anomaly}")


# ── news_poller ───────────────────────────────────────────────────────────────

def test_news_poller_filters_old_news():
    """Headlines más antiguos de 2 horas deben ignorarse."""
    from monitor.watchers.news_poller import NewsPoller

    old_ts = datetime.now(timezone.utc) - timedelta(hours=3)
    import time as _t
    old_struct = _t.strptime(
        old_ts.strftime("%Y-%m-%d %H:%M:%S"), "%Y-%m-%d %H:%M:%S"
    )

    fake_feed = MagicMock()
    fake_feed.entries = [{
        "title":            "ANET reports strong earnings",
        "published_parsed": old_struct,
    }]

    with (
        patch("feedparser.parse", return_value=fake_feed),
        patch.object(NewsPoller, "_score_headline", return_value=(0.9, 0.9)),
    ):
        poller = NewsPoller()
        events = poller.poll(["ANET"])

    assert len(events) == 0, "Headlines antiguos no deben procesarse"
    print("\nFiltro de noticias antiguas: OK")


def test_news_llm_prompt_is_english():
    """El prompt enviado a Qwen3.6 debe estar en inglés y esperar JSON."""
    from monitor.watchers.news_poller import NewsPoller

    captured_prompt = {}

    def fake_call_llm(prompt, **kwargs):
        captured_prompt["text"] = prompt
        return '{"is_material": true, "impact": "positive", "urgency": "wait_eod", "reasoning": "test"}'

    with patch("monitor.watchers.news_poller.call_llm", side_effect=fake_call_llm):
        poller = NewsPoller()
        result = poller._analyze_with_llm("ANET beats earnings estimates", "ANET")

    assert "captured_prompt" in dir() or captured_prompt, "LLM no fue llamado"
    prompt = captured_prompt.get("text", "")

    # El prompt debe estar en inglés
    spanish_words = ["Analiza", "Determina", "Responde", "Eres"]
    for word in spanish_words:
        assert word not in prompt, f"Prompt contiene palabra en español: '{word}'"

    # Debe pedir JSON
    assert "JSON" in prompt, "Prompt no solicita respuesta en JSON"
    assert "is_material" in prompt, "Schema JSON no incluye is_material"

    # Debe tener claves en inglés
    assert result["is_material"] is True
    assert result["impact"] == "positive"
    print("\nPrompt en inglés ✓ | Schema JSON en inglés ✓")


# ── earnings_watcher ──────────────────────────────────────────────────────────

def test_earnings_watcher_detects_today():
    """check_earnings_today debe detectar ticker con earnings hoy."""
    from monitor.watchers.earnings_watcher import EarningsWatcher
    from datetime import date

    today = date.today()

    mock_ticker = MagicMock()
    mock_ticker.calendar = {"Earnings Date": [today]}

    with patch("yfinance.Ticker", return_value=mock_ticker):
        ew     = EarningsWatcher()
        result = ew.check_earnings_today(["AVGO"])

    assert "AVGO" in result, f"AVGO debería estar en earnings hoy, got: {result}"
    print(f"\nEarnings detectados hoy: {result}")


# ── event_classifier ──────────────────────────────────────────────────────────

def test_event_classifier_defensive():
    """Posición abierta + PriceAlert HIGH DOWN → ClassifiedEvent DEFENSIVE."""
    from monitor.classifiers.event_classifier import EventClassifier
    from monitor.watchers.price_watcher import PriceAlert

    alert = PriceAlert(
        ticker="ANET", severity="HIGH", direction="DOWN",
        move_pct=-0.08, atr_multiple=2.1,
        current_price=135.0, open_price=147.0, volume_anomaly=False,
    )
    ec = EventClassifier()
    events = ec.classify(
        price_alerts=[alert],
        news_events=[],
        transcript=None,
        open_positions={"ANET": {"quantity": 100, "avg_price": 145.0}},
    )

    assert len(events) >= 1
    defensive = [e for e in events if e.type == "DEFENSIVE"]
    assert len(defensive) == 1
    assert defensive[0].ticker == "ANET"
    assert defensive[0].urgency == "immediate"
    print(f"\nDEFENSIVE event: {defensive[0].trigger}")


def test_event_classifier_no_action_if_not_in_portfolio():
    """PriceAlert HIGH DOWN para ticker NO en posiciones → no DEFENSIVE."""
    from monitor.classifiers.event_classifier import EventClassifier
    from monitor.watchers.price_watcher import PriceAlert

    alert = PriceAlert(
        ticker="AMD", severity="HIGH", direction="DOWN",
        move_pct=-0.09, atr_multiple=2.5,
        current_price=110.0, open_price=121.0, volume_anomaly=False,
    )
    ec = EventClassifier()
    events = ec.classify(
        price_alerts=[alert],
        news_events=[],
        transcript=None,
        open_positions={},   # sin posiciones
    )

    defensive = [e for e in events if e.type == "DEFENSIVE"]
    assert len(defensive) == 0, f"No debería haber DEFENSIVE sin posición, got: {defensive}"
    print("\nNo DEFENSIVE sin posición: OK")


# ── opportunistic_entry ────────────────────────────────────────────────────────

def test_opportunistic_blocked_near_close():
    """Blackout activo (19:50 CET) → evaluate_and_act devuelve False."""
    from monitor.actions.opportunistic_entry import OpportunisticEntry
    from monitor.classifiers.event_classifier import ClassifiedEvent

    event = ClassifiedEvent(
        type="OPPORTUNISTIC", ticker="ANET", urgency="immediate",
        trigger="test", recommended_action="", confidence=0.9, evidence={},
    )
    broker = MagicMock()

    # Parchear _is_blackout directamente para simular 19:50 CET (dentro del blackout)
    with patch.object(OpportunisticEntry, "_is_blackout", return_value=True):
        oe     = OpportunisticEntry()
        result = oe.evaluate_and_act(event, broker, intraday_entries_today=0)

    assert result is False, "Blackout a las 19:50 CET debe bloquear la entrada"
    print("\nBlackout cerca del cierre: OK")


def test_opportunistic_blocked_max_entries():
    """Máximo de entradas diarias alcanzado → devuelve False."""
    from monitor.actions.opportunistic_entry import OpportunisticEntry
    from monitor.classifiers.event_classifier import ClassifiedEvent

    event = ClassifiedEvent(
        type="OPPORTUNISTIC", ticker="AVGO", urgency="immediate",
        trigger="test", recommended_action="", confidence=0.9, evidence={},
    )
    broker = MagicMock()

    oe     = OpportunisticEntry()
    # Forzar que no estemos en blackout
    with patch.object(oe, "_is_blackout", return_value=False):
        result = oe.evaluate_and_act(event, broker, intraday_entries_today=2)

    assert result is False, "Con 2 entradas ya realizadas debe bloquearse"
    print("\nMáximo entradas diarias bloqueado: OK")


# ── market_monitor ────────────────────────────────────────────────────────────

def test_monitor_skips_outside_hours():
    """A las 10:00 CET (fuera de mercado) no se hacen llamadas al broker."""
    from monitor.market_monitor import MarketMonitor
    from zoneinfo import ZoneInfo

    broker = MagicMock()
    monitor = MarketMonitor(broker)

    fake_now = datetime(2026, 5, 16, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    with patch("monitor.market_monitor.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        monitor._run_price_cycle()

    broker.get_positions.assert_not_called()
    print("\nMonitor inactivo fuera de horario: OK")


def test_eod_context_persisted(tmp_path):
    """market_close_summary persiste el contexto EOD en JSON."""
    from monitor.market_monitor import MarketMonitor

    broker = MagicMock()
    broker.get_positions.return_value = {"ANET": {"quantity": 100, "avg_price": 145.0}}
    broker.get_cash.return_value = 200_000.0

    monitor = MarketMonitor(broker)
    monitor.eod_context = {
        "news_ANET_2026-05-16T15:30:00": {"ticker": "ANET", "type": "ENRICH_EOD"},
        "earnings_AVGO": {"ticker": "AVGO", "signal": "BUY"},
    }

    log_dir = tmp_path / "intraday_context"

    with (
        patch("monitor.market_monitor._INTRADAY_LOG_DIR", log_dir),
        patch("scheduler.notifier.send_notification", return_value=True),
    ):
        monitor.market_close_summary()

    date_str = datetime.now().strftime("%Y-%m-%d")
    report   = log_dir / f"{date_str}.json"
    assert report.exists(), f"EOD context no fue persistido en {report}"

    data = json.loads(report.read_text())
    assert "earnings_AVGO" in data
    assert data["earnings_AVGO"]["signal"] == "BUY"
    print(f"\nEOD context persistido: {report}")
    print(f"Eventos: {list(data.keys())}")
