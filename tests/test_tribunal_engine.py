"""
Tests tribunal/engine.py — E1-E16 según §5 de DESIGN_JUDGE2.md.

E1:  Todos los operadores Cmp (lt, le, gt, ge, eq, ne, in).
E2:  All(()) = True, Any_(()) = False (vacuous truth/disjunction).
E3:  Señal ausente en Cmp → (None, "missing_signal:<name>").
E4:  All: cortocircuito en False (sin señal ausente → puede cortar).
E5:  Any_: NO cortocircuito en True — evalúa TODAS las cláusulas para detectar skips.
E6:  applies_to filtra correctamente; "*" coincide con cualquier acción.
E7:  Contradicciones ordenadas: severity desc, tie-break rule_id asc.
E8:  Evaluación exhaustiva entre reglas — sin cortocircuito.
E9:  matched_rule_ids por priority asc.
E10: rules_version es order-independent (mismo hash si se reordenan las reglas).
E11: SeveritySpec sin scale → severity exacto = base.
E12: SeveritySpec con scale → interpolación lineal, clamp a [base, 1.0].
E13: SeverityScale degenerada (at_base == at_max) → severity = 1.0.
E14: Señal de scale ausente → severity = spec.base.
E15: evaluate() es pura: misma entrada → misma salida.
E16: Grid estructurado de casos de evaluación completa.
"""

import pytest

from tribunal.contracts import (
    AckSpec,
    All,
    Always,
    Any_,
    Cmp,
    ConflictDirection,
    ContradictionRule,
    EvidenceSet,
    Not_,
    Proposal,
    RuleKind,
    Signal,
    SeverityLevel,
    SeverityScale,
    SeveritySpec,
)
from tribunal.conditions import _eval
from tribunal.engine import _compute_severity, compute_rules_version, evaluate


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ev(*pairs) -> EvidenceSet:
    """EvidenceSet desde pares (name, value)."""
    return EvidenceSet({n: Signal(n, v) for n, v in pairs})


def _rule(
    rule_id: str,
    action: str,
    condition,
    severity_base: float = 0.60,
    severity_scale: SeverityScale | None = None,
    priority: int = 10,
    kind: RuleKind = RuleKind.CONTRADICTION,
    scenario: str = "test scenario",
    key_question: str = "test kq?",
) -> ContradictionRule:
    kwargs = dict(
        id=rule_id,
        kind=kind,
        applies_to=(action,) if action != "*" else ("*",),
        condition=condition,
        priority=priority,
        scenario=scenario,
        key_question=key_question,
    )
    if kind == RuleKind.CONTRADICTION:
        kwargs.update(
            pair=("proposal.action", f"signal.{rule_id}"),
            direction=ConflictDirection("a", "b"),
            severity=SeveritySpec(base=severity_base, scale=severity_scale),
        )
    return ContradictionRule(**kwargs)


def _proposal(action: str = "BUY", rationale: str = "") -> Proposal:
    return Proposal(action=action, rationale=rationale)


# ── E1: Operadores Cmp ────────────────────────────────────────────────────────

class TestCmpOperators:
    def test_lt_true(self):
        r, _ = _eval(Cmp("x", "lt", 10.0), _ev(("x", 5.0)))
        assert r is True

    def test_lt_false(self):
        r, _ = _eval(Cmp("x", "lt", 5.0), _ev(("x", 5.0)))
        assert r is False

    def test_le_equal(self):
        r, _ = _eval(Cmp("x", "le", 5.0), _ev(("x", 5.0)))
        assert r is True

    def test_gt_true(self):
        r, _ = _eval(Cmp("x", "gt", 4.9), _ev(("x", 5.0)))
        assert r is True

    def test_ge_equal(self):
        r, _ = _eval(Cmp("x", "ge", 5.0), _ev(("x", 5.0)))
        assert r is True

    def test_eq_true(self):
        r, _ = _eval(Cmp("flag", "eq", True), _ev(("flag", True)))
        assert r is True

    def test_ne_true(self):
        r, _ = _eval(Cmp("flag", "ne", True), _ev(("flag", False)))
        assert r is True

    def test_in_true(self):
        r, _ = _eval(Cmp("sig", "in", ("BUY", "SELL")), _ev(("sig", "BUY")))
        assert r is True

    def test_in_false(self):
        r, _ = _eval(Cmp("sig", "in", ("BUY", "SELL")), _ev(("sig", "HOLD")))
        assert r is False

    def test_eq_false(self):
        r, _ = _eval(Cmp("trend_up", "eq", False), _ev(("trend_up", True)))
        assert r is False

    def test_strict_inequality_at_boundary(self):
        # rsi=65 exacto no dispara rsi > 65 (estricto)
        r, _ = _eval(Cmp("rsi", "gt", 65.0), _ev(("rsi", 65.0)))
        assert r is False


# ── E2: All(()) / Any_(()) ────────────────────────────────────────────────────

class TestVacuous:
    def test_all_empty_true(self):
        r, reason = _eval(All(()), _ev())
        assert r is True
        assert reason is None

    def test_any_empty_false(self):
        r, reason = _eval(Any_(()), _ev())
        assert r is False
        assert reason is None


# ── E3: Señal ausente → skip ──────────────────────────────────────────────────

class TestMissingSignal:
    def test_cmp_missing_returns_none(self):
        r, reason = _eval(Cmp("rsi", "gt", 65.0), _ev())
        assert r is None
        assert reason == "missing_signal:rsi"

    def test_all_missing_propagates(self):
        r, reason = _eval(All((Cmp("a", "gt", 0), Cmp("b", "lt", 1))), _ev(("a", 5.0)))
        assert r is None
        assert reason == "missing_signal:b"

    def test_not_missing_propagates(self):
        r, reason = _eval(Not_(Cmp("x", "eq", True)), _ev())
        assert r is None
        assert reason == "missing_signal:x"

    def test_any_missing_propagates(self):
        # Any_: evalúa TODAS las cláusulas — si una falta, skip
        r, reason = _eval(Any_((Cmp("a", "gt", 0), Cmp("missing", "lt", 1))), _ev(("a", 5.0)))
        assert r is None
        assert "missing_signal" in reason


# ── E4: All cortocircuita en False ────────────────────────────────────────────

class TestAllShortCircuit:
    def test_short_circuits_on_false(self):
        # El primer Cmp es False → no evalúa el segundo (que tiene señal ausente)
        # Resultado: False (no skip), aunque "b" esté ausente
        r, reason = _eval(All((Cmp("a", "gt", 100), Cmp("b", "lt", 0))), _ev(("a", 5.0)))
        assert r is False
        assert reason is None


# ── E5: Any_ NO cortocircuita en True ────────────────────────────────────────

class TestAnyNoShortCircuit:
    def test_any_evaluates_all_clauses_even_after_true(self):
        # Primera cláusula es True, segunda tiene señal ausente → skip
        r, reason = _eval(
            Any_((Cmp("a", "gt", 0), Cmp("missing", "lt", 0))),
            _ev(("a", 5.0)),
        )
        assert r is None, "Any_ debe propagar skip aunque ya tenga un True"


# ── E6: applies_to ────────────────────────────────────────────────────────────

class TestAppliesTo:
    def _run(self, action: str, rule_applies_to: str):
        rule = _rule("r1", rule_applies_to, Always())
        proposal = _proposal(action=action)
        evidence = _ev()
        verdict = evaluate(proposal, evidence, (rule,))
        return verdict

    def test_matching_action(self):
        v = self._run("BUY", "BUY")
        assert v.evaluated_rules == 1

    def test_non_matching_action(self):
        v = self._run("SELL", "BUY")
        assert v.evaluated_rules == 0

    def test_wildcard_matches_any(self):
        v = self._run("HOLD", "*")
        assert v.evaluated_rules == 1

    def test_wildcard_also_matches_buy(self):
        v = self._run("BUY", "*")
        assert v.evaluated_rules == 1


# ── E7: Orden de contradicciones ─────────────────────────────────────────────

class TestContradictionOrder:
    def test_severity_desc_then_rule_id_asc(self):
        rules = (
            _rule("r_high", "BUY", Always(), severity_base=0.90, priority=20),
            _rule("r_low",  "BUY", Always(), severity_base=0.40, priority=10),
            _rule("r_mid",  "BUY", Always(), severity_base=0.60, priority=30),
        )
        verdict = evaluate(_proposal("BUY"), _ev(), rules)
        assert len(verdict.contradictions) == 3
        sevs = [c.severity for c in verdict.contradictions]
        assert sevs == sorted(sevs, reverse=True), "Debe estar ordenado por severity desc"

    def test_tie_break_rule_id_asc(self):
        rules = (
            _rule("r_z", "BUY", Always(), severity_base=0.70, priority=20),
            _rule("r_a", "BUY", Always(), severity_base=0.70, priority=10),
        )
        verdict = evaluate(_proposal("BUY"), _ev(), rules)
        ids = [c.rule_id for c in verdict.contradictions]
        assert ids == ["r_a", "r_z"], "Tie-break: rule_id asc"


# ── E8: Evaluación exhaustiva entre reglas ───────────────────────────────────

class TestExhaustiveEvaluation:
    def test_all_rules_evaluated(self):
        rules = (
            _rule("r1", "BUY", Always(), severity_base=0.60, priority=10),
            _rule("r2", "BUY", Always(), severity_base=0.70, priority=20),
            _rule("r3", "BUY", Always(), severity_base=0.80, priority=30),
        )
        verdict = evaluate(_proposal("BUY"), _ev(), rules)
        assert len(verdict.contradictions) == 3

    def test_skipped_rule_not_stopping_evaluation(self):
        rules = (
            _rule("r_skip", "BUY", Cmp("missing", "gt", 0), priority=10),
            _rule("r_ok",   "BUY", Always(), priority=20),
        )
        verdict = evaluate(_proposal("BUY"), _ev(), rules)
        assert len(verdict.skipped) == 1
        assert len(verdict.contradictions) == 1


# ── E9: matched_rule_ids por priority ────────────────────────────────────────

class TestMatchedRuleIds:
    def test_order_by_priority_asc(self):
        rules = (
            _rule("r_c", "BUY", Always(), priority=30),
            _rule("r_a", "BUY", Always(), priority=10),
            _rule("r_b", "BUY", Always(), priority=20),
        )
        verdict = evaluate(_proposal("BUY"), _ev(), rules)
        assert list(verdict.matched_rule_ids) == ["r_a", "r_b", "r_c"]

    def test_scenario_rules_included_in_matched(self):
        rules = (
            _rule("r_scenario", "*", Always(), priority=999, kind=RuleKind.SCENARIO),
            _rule("r_contra",   "BUY", Always(), priority=10),
        )
        verdict = evaluate(_proposal("BUY"), _ev(), rules)
        assert "r_scenario" in verdict.matched_rule_ids
        assert "r_contra" in verdict.matched_rule_ids

    def test_scenario_rule_no_contradiction_emitted(self):
        rules = (_rule("r_s", "*", Always(), priority=100, kind=RuleKind.SCENARIO),)
        verdict = evaluate(_proposal("BUY"), _ev(), rules)
        assert verdict.contradictions == ()
        assert "r_s" in verdict.matched_rule_ids


# ── E10: rules_version order-independent ─────────────────────────────────────

class TestRulesVersion:
    def test_same_hash_regardless_of_order(self):
        r1 = _rule("r1", "BUY", Always(), priority=10)
        r2 = _rule("r2", "SELL", Always(), priority=20)
        h_ab = compute_rules_version((r1, r2))
        h_ba = compute_rules_version((r2, r1))
        assert h_ab == h_ba

    def test_different_rules_different_hash(self):
        r1 = _rule("r1", "BUY", Always(), severity_base=0.60)
        r2 = _rule("r1", "BUY", Always(), severity_base=0.80)  # mismo id, distinta base
        assert compute_rules_version((r1,)) != compute_rules_version((r2,))

    def test_hash_is_12_chars(self):
        r = _rule("r1", "BUY", Always())
        h = compute_rules_version((r,))
        assert len(h) == 12


# ── E11: Severidad sin scale ─────────────────────────────────────────────────

class TestSeverityNoScale:
    def test_exact_base(self):
        spec = SeveritySpec(base=0.72)
        sev = _compute_severity(spec, _ev())
        assert sev == pytest.approx(0.72)


# ── E12: Severidad con scale → interpolación ────────────────────────────────

class TestSeverityWithScale:
    def test_at_base_returns_base(self):
        spec = SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=65.0, at_max=80.0),
        )
        sev = _compute_severity(spec, _ev(("rsi", 65.0)))
        assert sev == pytest.approx(0.60)

    def test_at_max_returns_1(self):
        spec = SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=65.0, at_max=80.0),
        )
        sev = _compute_severity(spec, _ev(("rsi", 80.0)))
        assert sev == pytest.approx(1.0)

    def test_midpoint_interpolation(self):
        spec = SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=65.0, at_max=80.0),
        )
        # t = (72.5 - 65) / 15 = 0.5; sev = 0.6 + 0.5*(1-0.6) = 0.8
        sev = _compute_severity(spec, _ev(("rsi", 72.5)))
        assert sev == pytest.approx(0.80)

    def test_clamp_below_base(self):
        spec = SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=65.0, at_max=80.0),
        )
        # rsi=50 → t negativo → clamp a base
        sev = _compute_severity(spec, _ev(("rsi", 50.0)))
        assert sev == pytest.approx(0.60)

    def test_clamp_above_1(self):
        spec = SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=65.0, at_max=80.0),
        )
        sev = _compute_severity(spec, _ev(("rsi", 100.0)))
        assert sev == pytest.approx(1.0)

    def test_descending_scale(self):
        # sentimiento: at_base=-0.3, at_max=-0.8 (at_max < at_base)
        spec = SeveritySpec(
            base=0.55,
            scale=SeverityScale("sentiment", at_base=-0.3, at_max=-0.8),
        )
        # t = (-0.3 - (-0.3)) / (-0.8 - (-0.3)) = 0 → sev = 0.55
        sev_at_base = _compute_severity(spec, _ev(("sentiment", -0.3)))
        assert sev_at_base == pytest.approx(0.55)
        # t = (-0.8 - (-0.3)) / (-0.8 - (-0.3)) = 1.0 → sev = 1.0
        sev_at_max = _compute_severity(spec, _ev(("sentiment", -0.8)))
        assert sev_at_max == pytest.approx(1.0)


# ── E13: Scale degenerada (at_base == at_max) ─────────────────────────────────

class TestDegenerateScale:
    def test_returns_1_no_division_by_zero(self):
        spec = SeveritySpec(
            base=0.60,
            scale=SeverityScale("x", at_base=50.0, at_max=50.0),
        )
        sev = _compute_severity(spec, _ev(("x", 50.0)))
        assert sev == pytest.approx(1.0)


# ── E14: Scale signal ausente → base ────────────────────────────────────────

class TestScaleSignalAbsent:
    def test_absent_scale_signal_returns_base(self):
        spec = SeveritySpec(
            base=0.65,
            scale=SeverityScale("absent_signal", at_base=0.0, at_max=1.0),
        )
        sev = _compute_severity(spec, _ev())
        assert sev == pytest.approx(0.65)


# ── E15: Pureza — misma entrada → misma salida ───────────────────────────────

class TestPurity:
    def test_deterministic(self):
        from config.contradiction_rules import TRADING_RULES
        proposal = Proposal(action="BUY")
        evidence = EvidenceSet({"rsi": Signal("rsi", 70.0), "trend_up": Signal("trend_up", True)})
        v1 = evaluate(proposal, evidence, TRADING_RULES)
        v2 = evaluate(proposal, evidence, TRADING_RULES)
        assert v1.contradictions == v2.contradictions
        assert v1.rules_version == v2.rules_version

    def test_no_side_effects_on_input(self):
        rules = (
            _rule("r1", "BUY", Cmp("rsi", "gt", 65.0)),
        )
        evidence = EvidenceSet({"rsi": Signal("rsi", 70.0)})
        evaluate(_proposal("BUY"), evidence, rules)
        # La evidencia no debe haber cambiado
        assert evidence.get("rsi").value == 70.0


# ── E16: Grid estructurado de casos de evaluación completa ──────────────────

class TestEvaluationGrid:
    """
    Grid determinista sin hypothesis.
    Cubre: action ∈ {BUY,SELL,HOLD}, señal presente/ausente, condición F/T.
    """

    @pytest.mark.parametrize("action,rsi,expected_ids", [
        # BUY, rsi>65 → R2 fires
        ("BUY",  70.0, {"rsi_overbought_vs_buy"}),
        # BUY, rsi<65 → R2 no aplica
        ("BUY",  50.0, set()),
        # SELL, rsi<35 → R1 fires
        ("SELL", 25.0, {"rsi_oversold_vs_sell"}),
        # SELL, rsi>35 → R1 no aplica
        ("SELL", 50.0, set()),
        # HOLD → ninguna de R1/R2 aplica
        ("HOLD", 70.0, set()),
    ])
    def test_rsi_rules(self, action, rsi, expected_ids):
        from config.contradiction_rules import TRADING_RULES
        evidence = _ev(
            ("rsi", rsi),
            ("trend_up", True),
            ("rs_spy", 0.01),
            ("pct_52w_range", 0.5),
            ("sentiment", 0.0),
        )
        verdict = evaluate(_proposal(action), evidence, TRADING_RULES)
        contra_ids = {c.rule_id for c in verdict.contradictions}
        assert expected_ids.issubset(contra_ids), (
            f"action={action}, rsi={rsi}: esperaba {expected_ids} en {contra_ids}"
        )

    @pytest.mark.parametrize("trend_up,rs_spy,expected_fires", [
        (False, -0.05, True),   # ambas condiciones True → R3 dispara
        (True,  -0.05, False),  # trend_up=True → condición False
        (False,  0.00, False),  # rs_spy >= -0.03 → condición False
        (True,   0.01, False),  # ninguna
    ])
    def test_r3_buy_against_trend(self, trend_up, rs_spy, expected_fires):
        from config.contradiction_rules import TRADING_RULES
        evidence = _ev(
            ("rsi", 50.0),
            ("trend_up", trend_up),
            ("rs_spy", rs_spy),
            ("pct_52w_range", 0.5),
            ("sentiment", 0.0),
        )
        verdict = evaluate(_proposal("BUY"), evidence, TRADING_RULES)
        fired = any(c.rule_id == "buy_against_trend_weak_rs" for c in verdict.contradictions)
        assert fired == expected_fires

    @pytest.mark.parametrize("sentiment,action,rule_id,expected", [
        (-0.5, "BUY",  "buy_vs_negative_sentiment",  True),
        (-0.1, "BUY",  "buy_vs_negative_sentiment",  False),
        ( 0.5, "SELL", "sell_vs_positive_sentiment",  True),
        ( 0.1, "SELL", "sell_vs_positive_sentiment",  False),
    ])
    def test_sentiment_rules(self, sentiment, action, rule_id, expected):
        from config.contradiction_rules import TRADING_RULES
        evidence = _ev(
            ("rsi", 50.0),
            ("trend_up", True),
            ("rs_spy", 0.01),
            ("pct_52w_range", 0.5),
            ("sentiment", sentiment),
        )
        verdict = evaluate(_proposal(action), evidence, TRADING_RULES)
        fired = any(c.rule_id == rule_id for c in verdict.contradictions)
        assert fired == expected
