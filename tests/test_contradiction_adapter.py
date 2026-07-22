"""
Tests graph/contradiction_adapter.py — T1-T6 según §5 de DESIGN_JUDGE2.md.

T1: Cada regla CONTRADICTION dispara con las señales correctas.
T2: Equivalencia con if/elif — scenario_from_verdict devuelve el mismo
    scenario/key_question que el bloque if/elif para las 8 ramas.
T3: Múltiples contradicciones simultáneas.
T4: HOLD y "aligned signals" devuelven los escenarios de fallback correctos.
T5: Señales ausentes → reglas a skipped (no contradicciones).
T6: rationale_present es False cuando proposal.rationale="".
"""

import pytest

from graph.contradiction_adapter import run_contradiction_judge, scenario_from_verdict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _state(
    signal: str = "BUY",
    rsi: float | None = 50.0,
    trend_up: bool | None = True,
    rs_spy: float | None = 0.01,
    pct_52w_range: float | None = 0.5,
    sentiment: float | None = 0.0,
    confidence: float = 0.75,
) -> dict:
    """Construye un TradingState mínimo para el adaptador."""
    tech: dict = {"signal": signal, "confidence": confidence}
    if rsi is not None:
        tech["rsi"] = rsi
    if trend_up is not None:
        tech["trend_up"] = trend_up
    if rs_spy is not None:
        tech["rs_spy"] = rs_spy
    if pct_52w_range is not None:
        tech["pct_52w_range"] = pct_52w_range

    sent: dict = {}
    if sentiment is not None:
        sent["sentiment"] = sentiment

    return {"technical_result": tech, "sentiment_result": sent or None}


def _contra_ids(block: dict) -> set[str]:
    return {c["rule_id"] for c in block.get("contradictions", [])}


def _skipped_ids(block: dict) -> set[str]:
    return {s["rule_id"] for s in block.get("skipped", [])}


# ── T1: Cada regla CONTRADICTION dispara ─────────────────────────────────────

class TestEachRuleFires:
    def test_r1_rsi_oversold_vs_sell(self):
        block = run_contradiction_judge(_state(signal="SELL", rsi=30.0))
        assert "rsi_oversold_vs_sell" in _contra_ids(block)

    def test_r1_not_fires_when_rsi_ok(self):
        block = run_contradiction_judge(_state(signal="SELL", rsi=50.0))
        assert "rsi_oversold_vs_sell" not in _contra_ids(block)

    def test_r2_rsi_overbought_vs_buy(self):
        block = run_contradiction_judge(_state(signal="BUY", rsi=70.0))
        assert "rsi_overbought_vs_buy" in _contra_ids(block)

    def test_r2_boundary_65_strict(self):
        # rsi=65 exacto no dispara (condición estricta >65)
        block = run_contradiction_judge(_state(signal="BUY", rsi=65.0))
        assert "rsi_overbought_vs_buy" not in _contra_ids(block)

    def test_r3_buy_against_trend_weak_rs(self):
        block = run_contradiction_judge(_state(
            signal="BUY", trend_up=False, rs_spy=-0.05,
            rsi=50.0, pct_52w_range=0.5, sentiment=0.0,
        ))
        assert "buy_against_trend_weak_rs" in _contra_ids(block)

    def test_r3_not_fires_when_trend_up(self):
        block = run_contradiction_judge(_state(
            signal="BUY", trend_up=True, rs_spy=-0.05,
        ))
        assert "buy_against_trend_weak_rs" not in _contra_ids(block)

    def test_r4_buy_near_52w_high(self):
        block = run_contradiction_judge(_state(
            signal="BUY", pct_52w_range=0.90,
            trend_up=True, rs_spy=0.01, rsi=50.0, sentiment=0.0,
        ))
        assert "buy_near_52w_high" in _contra_ids(block)

    def test_r4_boundary_0_82_strict(self):
        block = run_contradiction_judge(_state(signal="BUY", pct_52w_range=0.82))
        assert "buy_near_52w_high" not in _contra_ids(block)

    def test_r5_buy_vs_negative_sentiment(self):
        block = run_contradiction_judge(_state(signal="BUY", sentiment=-0.5))
        assert "buy_vs_negative_sentiment" in _contra_ids(block)

    def test_r5_not_fires_when_sentiment_ok(self):
        block = run_contradiction_judge(_state(signal="BUY", sentiment=-0.1))
        assert "buy_vs_negative_sentiment" not in _contra_ids(block)

    def test_r6_sell_vs_positive_sentiment(self):
        block = run_contradiction_judge(_state(signal="SELL", rsi=50.0, sentiment=0.5))
        assert "sell_vs_positive_sentiment" in _contra_ids(block)

    def test_r6_not_fires_when_sentiment_neutral(self):
        block = run_contradiction_judge(_state(signal="SELL", rsi=50.0, sentiment=0.1))
        assert "sell_vs_positive_sentiment" not in _contra_ids(block)


# ── T2: Equivalencia con if/elif ──────────────────────────────────────────────

class TestEquivalenceWithIfElif:
    """
    Para cada rama del if/elif de critic_node (líneas 341-371 de trading_graph.py),
    verifica que scenario_from_verdict produce el mismo (scenario, key_question).

    Las señales se dan todas presentes para evitar discrepancias por defaults.
    El if/elif usa rsi=technical.get("rsi",50); el adaptador no sintetiza defaults.
    """

    # Rama 1: rsi < 35 and signal == "SELL"
    def test_branch_rsi_oversold_sell(self):
        state = _state(signal="SELL", rsi=25.0, sentiment=0.0)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "RSI in oversold zone but SELL signal — potential contradiction"
        assert kq == "Is selling justified when RSI signals oversold conditions?"

    # Rama 2: rsi > 65 and signal == "BUY"
    def test_branch_rsi_overbought_buy(self):
        state = _state(signal="BUY", rsi=70.0, pct_52w_range=0.5, sentiment=0.0, trend_up=True)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "RSI in overbought zone but BUY signal — potential overextension"
        assert kq == "Is buying justified when RSI signals overbought conditions?"

    # Rama 3: BUY and not trend_up and rs_spy < -0.03
    # (rsi OK, no R1/R2, pct_52w OK → no R4)
    def test_branch_buy_against_trend(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=False, rs_spy=-0.05,
                       pct_52w_range=0.5, sentiment=0.0)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "BUY against bearish trend with relative weakness vs SPY"
        assert kq == "Is it prudent to buy when the ticker underperforms the market in a downtrend?"

    # Rama 4: BUY and pct_52w > 0.82 (trend_up=True → no R3)
    def test_branch_buy_near_52w_high(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.90, sentiment=0.0)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "BUY near 52-week highs — potential overextension"
        assert kq == "Is the price too extended near its 52-week highs?"

    # Rama 5: BUY and sentiment < -0.3 (rsi OK, trend OK, pct OK)
    def test_branch_buy_negative_sentiment(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.5, sentiment=-0.5)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "BUY signal but negative sentiment — divergence"
        assert kq == "Does negative sentiment invalidate the bullish signal?"

    # Rama 6: SELL and sentiment > 0.3 (rsi OK → no R1)
    def test_branch_sell_positive_sentiment(self):
        state = _state(signal="SELL", rsi=50.0, sentiment=0.5)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "SELL signal but positive sentiment — divergence"
        assert kq == "Does positive sentiment invalidate the bearish signal?"

    # Rama 7: HOLD
    def test_branch_hold(self):
        state = _state(signal="HOLD", rsi=50.0, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.5, sentiment=0.0)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "HOLD signal — evaluate if there is reason to act"
        assert kq == "Is there a clear reason to act, or is holding the correct decision?"

    # Else — aligned signals (BUY, todo OK, sin contradicciones)
    def test_branch_aligned_signals(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.5, sentiment=0.0)
        block = run_contradiction_judge(state)
        scenario, kq = scenario_from_verdict(block)
        assert scenario == "Aligned signals — verify coherence"
        assert kq == "Are all indicators pointing in the same direction?"

    # Priority: si R2 (rsi>65, BUY) y R4 (pct>0.82, BUY) ambas disparan,
    # gana R2 (priority=20 < 40).
    def test_priority_r2_over_r4(self):
        state = _state(signal="BUY", rsi=70.0, pct_52w_range=0.90,
                       trend_up=True, rs_spy=0.01, sentiment=0.0)
        block = run_contradiction_judge(state)
        scenario, _ = scenario_from_verdict(block)
        assert scenario == "RSI in overbought zone but BUY signal — potential overextension"

    # Priority: si R3 y R4 ambas disparan (BUY, bearish trend, pct>0.82),
    # gana R3 (priority=30 < 40).
    def test_priority_r3_over_r4(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=False, rs_spy=-0.05,
                       pct_52w_range=0.90, sentiment=0.0)
        block = run_contradiction_judge(state)
        scenario, _ = scenario_from_verdict(block)
        assert scenario == "BUY against bearish trend with relative weakness vs SPY"


# ── T3: Múltiples contradicciones simultáneas ────────────────────────────────

class TestMultipleContradictions:
    def test_r2_and_r4_both_fire(self):
        state = _state(signal="BUY", rsi=70.0, pct_52w_range=0.90,
                       trend_up=True, rs_spy=0.01, sentiment=0.0)
        block = run_contradiction_judge(state)
        ids = _contra_ids(block)
        assert "rsi_overbought_vs_buy" in ids
        assert "buy_near_52w_high" in ids

    def test_contradictions_sorted_severity_desc(self):
        # R3 severity=0.80 (fixed), R4 severity varia con scale.
        # Con pct_52w=0.82+epsilon, R4 severity ≈ base=0.50 < R3=0.80
        state = _state(signal="BUY", rsi=50.0, trend_up=False, rs_spy=-0.05,
                       pct_52w_range=0.83, sentiment=0.0)
        block = run_contradiction_judge(state)
        sevs = [c["severity"] for c in block["contradictions"]]
        assert sevs == sorted(sevs, reverse=True)


# ── T4: HOLD y aligned signals ───────────────────────────────────────────────

class TestScenarioRules:
    def test_hold_matched_not_contradiction(self):
        state = _state(signal="HOLD", rsi=50.0, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.5, sentiment=0.0)
        block = run_contradiction_judge(state)
        assert _contra_ids(block) == set()
        assert "hold_review" in block["matched_rule_ids"]

    def test_aligned_signals_fallback(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.5, sentiment=0.0)
        block = run_contradiction_judge(state)
        assert _contra_ids(block) == set()
        assert "aligned_signals_review" in block["matched_rule_ids"]

    def test_scenario_hint_populated(self):
        state = _state(signal="HOLD")
        block = run_contradiction_judge(state)
        assert block["scenario_hint"] is not None
        assert block["scenario_hint"]["scenario"] == "HOLD signal — evaluate if there is reason to act"


# ── T5: Señales ausentes → skipped ──────────────────────────────────────────

class TestMissingSignals:
    def test_no_rsi_skips_rsi_rules(self):
        state = _state(signal="BUY", rsi=None, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.5, sentiment=0.0)
        block = run_contradiction_judge(state)
        skipped = _skipped_ids(block)
        # R2 usa rsi → skipped
        assert "rsi_overbought_vs_buy" in skipped

    def test_no_sentiment_skips_r5_r6(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=True, rs_spy=0.01,
                       pct_52w_range=0.5, sentiment=None)
        block = run_contradiction_judge(state)
        skipped = _skipped_ids(block)
        assert "buy_vs_negative_sentiment" in skipped

    def test_no_rs_spy_skips_r3(self):
        state = _state(signal="BUY", rsi=50.0, trend_up=False, rs_spy=None,
                       pct_52w_range=0.5, sentiment=0.0)
        block = run_contradiction_judge(state)
        skipped = _skipped_ids(block)
        assert "buy_against_trend_weak_rs" in skipped

    def test_skipped_not_in_contradictions(self):
        state = _state(signal="BUY", rsi=None)
        block = run_contradiction_judge(state)
        skipped_ids = _skipped_ids(block)
        contra_ids = _contra_ids(block)
        assert skipped_ids.isdisjoint(contra_ids)


# ── T6: rationale_present ────────────────────────────────────────────────────

class TestRationalePresent:
    def test_rationale_present_false_when_empty(self):
        state = _state(signal="BUY")
        block = run_contradiction_judge(state)
        assert block["rationale_present"] is False

    def test_block_has_all_expected_keys(self):
        state = _state(signal="BUY")
        block = run_contradiction_judge(state)
        expected = {
            "mode", "engine_version", "rules_version",
            "rationale_present", "contradictions", "matched_rule_ids",
            "skipped", "evaluated_rules", "scenario_hint", "duration_ms", "error",
        }
        assert expected.issubset(set(block.keys()))

    def test_error_is_none_on_success(self):
        state = _state(signal="BUY")
        block = run_contradiction_judge(state)
        assert block["error"] is None

    def test_duration_ms_positive(self):
        state = _state(signal="BUY")
        block = run_contradiction_judge(state)
        assert block["duration_ms"] >= 0
