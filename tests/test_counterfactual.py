"""
Tests para audit.counterfactual — resolución de outcomes contrafactuales.

Todos los casos usan tmp_path / monkeypatching para no tocar el filesystem
de producción ni llamar a yfinance. OUTCOMES_PATH y get_price_on_date se
mockean en cada test.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_eval(
    ticker: str = "AMD",
    signal: str = "BUY",
    signal_price: float = 180.0,
    days_ago: int = 10,
    verdict: str = "APPROVED",
    fast_path: bool = False,
    scenario: str = None,
    eval_suffix: str = "",
) -> dict:
    """Construye un registro de audit trail sintético."""
    ts_date = date.today() - timedelta(days=days_ago)
    ts_str = f"{ts_date.isoformat()}T20:30:00.000Z"
    eval_id = f"{ticker}_{ts_date:%Y%m%d}_203000{eval_suffix}"
    return {
        "schema_version": 1,
        "eval_id": eval_id,
        "ts": ts_str,
        "ticker": ticker,
        "proposal": {
            "source": "technical_agent",
            "signal": signal,
            "confidence": 0.75,
            "score_pre_critic": 0.85,
        },
        "evidence": {
            "technical": {"rsi": 40.0, "price": signal_price},
            "sentiment": {"score": 0.1, "headlines": 5},
        },
        "critic": {
            "engaged": not fast_path,
            "fast_path": fast_path,
            "fast_path_reason": "weak signal — score=0.2 < 0.38" if fast_path else None,
            "scenario": scenario,
            "verdict": verdict,
            "approved": verdict != "CHALLENGED",
            "error": False,
        },
        "decision": {"action": "BUY" if verdict != "CHALLENGED" else "HOLD"},
        "linkage": {
            "signal_price": signal_price,
            "ledger_join_key": f"{ticker}_{ts_date:%Y%m%d}",
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _patch_price(price: float):
    """Parchea get_price_on_date para devolver siempre el mismo precio."""
    return patch("audit.counterfactual.get_price_on_date", return_value=price)


def _patch_price_none():
    """Simula que yfinance no tiene datos (mercado cerrado, ticker delisted…)."""
    return patch("audit.counterfactual.get_price_on_date", return_value=None)


def _patch_ledger_none():
    """Simula que no hay trade en el ledger para ese ticker/fecha."""
    return patch("audit.counterfactual._fetch_ledger_outcomes", return_value=None)


# ── Test 1: señal BUY con precio que sube ────────────────────────────────────

def test_buy_signal_positive_return(tmp_path):
    """
    Señal BUY a 180, precio futuro 198 → retorno +10%, win=True.
    Verificamos T+1, T+5 y T+20 (todos resolvibles con days_ago=30).
    """
    import audit.counterfactual as cf

    outcomes_path = tmp_path / "audit_outcomes.json"
    ev = _make_eval(ticker="AMD", signal="BUY", signal_price=180.0, days_ago=30)

    with (
        patch.object(cf, "OUTCOMES_PATH", outcomes_path),
        _patch_price(198.0),
        _patch_ledger_none(),
    ):
        result = cf.resolve_all([ev])

    assert ev["eval_id"] in result
    entry = result[ev["eval_id"]]

    for h in ("t1", "t5", "t20"):
        assert h in entry, f"Horizonte {h} no resuelto"
        assert abs(entry[h]["return"] - 0.10) < 1e-5, \
            f"{h}: return={entry[h]['return']} esperado≈0.10"
        assert entry[h]["win"] is True
        assert entry[h]["source"] == "counterfactual"


# ── Test 2: señal SELL con precio que baja ───────────────────────────────────

def test_sell_signal_return_semantics(tmp_path):
    """
    Señal SELL a 200, precio futuro 170 → retorno +15%, win=True.
    La señal SELL gana cuando el precio cae.
    """
    import audit.counterfactual as cf

    outcomes_path = tmp_path / "audit_outcomes.json"
    ev = _make_eval(ticker="COST", signal="SELL", signal_price=200.0, days_ago=10)

    with (
        patch.object(cf, "OUTCOMES_PATH", outcomes_path),
        _patch_price(170.0),
        _patch_ledger_none(),
    ):
        result = cf.resolve_all([ev])

    entry = result.get(ev["eval_id"], {})
    # T+1 debería estar resuelto (10 días > 1 + buffer de 2)
    assert "t1" in entry, "T+1 no resuelto"
    expected = (200.0 - 170.0) / 200.0   # 0.15
    assert abs(entry["t1"]["return"] - expected) < 1e-5, \
        f"return={entry['t1']['return']:.6f} esperado={expected:.6f}"
    assert entry["t1"]["win"] is True


# ── Test 3: precio no disponible ─────────────────────────────────────────────

def test_missing_price_leaves_horizon_unresolved(tmp_path):
    """
    Si yfinance no devuelve precio, el horizonte queda sin resolver.
    No debe lanzar excepciones ni dejar el fichero corrupto.
    """
    import audit.counterfactual as cf

    outcomes_path = tmp_path / "audit_outcomes.json"
    ev = _make_eval(ticker="MU", signal="BUY", signal_price=90.0, days_ago=30)

    with (
        patch.object(cf, "OUTCOMES_PATH", outcomes_path),
        _patch_price_none(),
        _patch_ledger_none(),
    ):
        result = cf.resolve_all([ev])

    entry = result.get(ev["eval_id"], {})
    for h in ("t1", "t5", "t20"):
        assert h not in entry, f"Horizonte {h} no debería estar resuelto sin precio"

    # El fichero debe ser JSON válido si se escribió algo
    if outcomes_path.exists():
        json.loads(outcomes_path.read_text())


# ── Test 4: evaluación demasiado reciente ────────────────────────────────────

def test_too_recent_not_resolved(tmp_path):
    """
    Una evaluación de ayer no puede tener T+5 ni T+20 resueltos.
    (T+1 tampoco porque exige days_ago >= 1 + buffer=2 → 3 días mínimo)
    """
    import audit.counterfactual as cf

    outcomes_path = tmp_path / "audit_outcomes.json"
    ev = _make_eval(ticker="ORCL", signal="BUY", signal_price=150.0, days_ago=1)

    with (
        patch.object(cf, "OUTCOMES_PATH", outcomes_path),
        _patch_price(160.0),    # precio disponible, pero no debería consultarse
        _patch_ledger_none(),
    ):
        result = cf.resolve_all([ev])

    entry = result.get(ev["eval_id"], {})
    for h in ("t1", "t5", "t20"):
        assert h not in entry, f"Horizonte {h} no debería resolverse con solo 1 día"


# ── Test 5: señal HOLD se omite ──────────────────────────────────────────────

def test_hold_signal_skipped(tmp_path):
    """
    Señales HOLD no tienen dirección implícita — no se resuelven outcomes.
    """
    import audit.counterfactual as cf

    outcomes_path = tmp_path / "audit_outcomes.json"
    ev = _make_eval(ticker="VRT", signal="HOLD", signal_price=95.0, days_ago=15)

    with (
        patch.object(cf, "OUTCOMES_PATH", outcomes_path),
        _patch_price(100.0),
        _patch_ledger_none(),
    ):
        result = cf.resolve_all([ev])

    entry = result.get(ev["eval_id"], {})
    for h in ("t1", "t5", "t20"):
        assert h not in entry, f"HOLD no debería tener horizonte {h} resuelto"


# ── Test 6: idempotencia ─────────────────────────────────────────────────────

def test_resolve_is_idempotent(tmp_path):
    """
    Llamar resolve_all dos veces no duplica outcomes ni lanza errores.
    El segundo pase ve los horizontes ya resueltos y los deja intactos.
    """
    import audit.counterfactual as cf

    outcomes_path = tmp_path / "audit_outcomes.json"
    ev = _make_eval(ticker="ANET", signal="BUY", signal_price=155.0, days_ago=10)

    call_count = {"n": 0}
    original_fn = cf.get_price_on_date

    def counting_price(ticker, target_date):
        call_count["n"] += 1
        return 165.0

    with (
        patch.object(cf, "OUTCOMES_PATH", outcomes_path),
        patch.object(cf, "get_price_on_date", counting_price),
        _patch_ledger_none(),
    ):
        r1 = cf.resolve_all([ev])
        calls_after_first = call_count["n"]

        r2 = cf.resolve_all([ev])
        calls_after_second = call_count["n"]

    # El segundo pase no debería llamar a get_price_on_date de nuevo
    assert calls_after_second == calls_after_first, \
        f"get_price_on_date llamado {calls_after_second - calls_after_first} veces extra en 2ª pasada"

    # El resultado debe ser el mismo
    assert r1[ev["eval_id"]].get("t1") == r2[ev["eval_id"]].get("t1")


# ── Test 7: get_enriched_evaluations fusiona correctamente ───────────────────

def test_enriched_evaluations_merges_outcomes(tmp_path):
    """
    get_enriched_evaluations devuelve la evaluación original + bloque outcomes.
    any_resolved=True cuando hay ≥1 horizonte resuelto.
    """
    import audit.counterfactual as cf

    outcomes_path = tmp_path / "audit_outcomes.json"
    ev = _make_eval(ticker="TSM", signal="BUY", signal_price=420.0, days_ago=10)

    with (
        patch.object(cf, "OUTCOMES_PATH", outcomes_path),
        _patch_price(441.0),
        _patch_ledger_none(),
    ):
        enriched = cf.get_enriched_evaluations([ev], resolve=True)

    assert len(enriched) == 1
    rec = enriched[0]

    assert rec["ticker"] == "TSM"
    assert rec["eval_id"] == ev["eval_id"]

    outs = rec["outcomes"]
    assert outs["any_resolved"] is True
    assert outs["t1"] is not None
    assert outs["t1"]["win"] is True
    assert abs(outs["t1"]["return"] - (441.0 - 420.0) / 420.0) < 1e-5
