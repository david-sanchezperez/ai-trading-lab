"""
Tests tribunal/contracts.py — C1-C5 según §5 de DESIGN_JUDGE2.md.

C1: Frozen — mutación lanza FrozenInstanceError.
C2: Round-trip to_dict/from_dict sin pérdida.
C3: JSON-safe — json.dumps no lanza.
C4: Compatibilidad hacia adelante — claves desconocidas ignoradas en from_dict.
C5: Validaciones — SeveritySpec.base fuera de [0,1], ContradictionRule CONTRADICTION
    sin campos obligatorios, Contradiction.level inconsistente con severity.
"""

import json
from dataclasses import FrozenInstanceError

import pytest

from tribunal.contracts import (
    AckSpec,
    All,
    Always,
    Any_,
    Cmp,
    ConflictDirection,
    Contradiction,
    ContradictionRule,
    ContradictionVerdict,
    EvidenceSet,
    Not_,
    Proposal,
    RuleKind,
    Signal,
    SeverityLevel,
    SeverityScale,
    SeveritySpec,
    SkippedRule,
    condition_from_dict,
    condition_to_dict,
    severity_to_level,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_contradiction(severity: float = 0.75) -> Contradiction:
    return Contradiction(
        rule_id="r_test",
        pair=("proposal.action", "signal.rsi"),
        direction=ConflictDirection(a_claims="bullish", b_claims="overbought"),
        severity=severity,
        level=severity_to_level(severity),
        acknowledged=False,
        acknowledged_by=(),
        contributing=(),
        details={"rsi": 70.0, "threshold_rsi": 65.0},
    )


def _make_rule(kind: RuleKind = RuleKind.CONTRADICTION) -> ContradictionRule:
    kwargs = dict(
        id="r1",
        kind=kind,
        applies_to=("BUY",),
        condition=Cmp("rsi", "gt", 65.0),
        priority=10,
        scenario="RSI overbought",
        key_question="Is this overextended?",
    )
    if kind == RuleKind.CONTRADICTION:
        kwargs.update(
            pair=("proposal.action", "signal.rsi"),
            direction=ConflictDirection(a_claims="bullish", b_claims="overbought"),
            severity=SeveritySpec(base=0.60),
        )
    return ContradictionRule(**kwargs)


# ── C1: Frozen ─────────────────────────────────────────────────────────────────

class TestFrozen:
    def test_proposal_frozen(self):
        p = Proposal(action="BUY")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            p.action = "SELL"  # type: ignore[misc]

    def test_signal_frozen(self):
        s = Signal(name="rsi", value=70.0)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            s.value = 50.0  # type: ignore[misc]

    def test_contradiction_frozen(self):
        c = _make_contradiction()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            c.severity = 0.5  # type: ignore[misc]

    def test_contradiction_rule_frozen(self):
        r = _make_rule()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            r.priority = 99  # type: ignore[misc]

    def test_severity_spec_frozen(self):
        spec = SeveritySpec(base=0.6)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            spec.base = 0.8  # type: ignore[misc]


# ── C2: Round-trip to_dict / from_dict ────────────────────────────────────────

class TestRoundTrip:
    def test_proposal(self):
        p = Proposal(action="BUY", rationale="rsi bullish", confidence=0.8, source="ta")
        assert Proposal.from_dict(p.to_dict()) == p

    def test_signal(self):
        s = Signal(name="rsi", value=72.5, source="ta")
        assert Signal.from_dict(s.to_dict()) == s

    def test_evidence_set(self):
        ev = EvidenceSet({
            "rsi": Signal("rsi", 72.5),
            "sentiment": Signal("sentiment", -0.4),
        })
        assert EvidenceSet.from_dict(ev.to_dict()) == ev

    def test_contradiction(self):
        c = _make_contradiction(0.55)
        assert Contradiction.from_dict(c.to_dict()) == c

    def test_contradiction_rule(self):
        r = _make_rule()
        assert ContradictionRule.from_dict(r.to_dict()) == r

    def test_skipped_rule(self):
        s = SkippedRule(rule_id="r1", reason="missing_signal:rsi")
        assert SkippedRule.from_dict(s.to_dict()) == s

    def test_condition_cmp(self):
        c = Cmp("rsi", "gt", 65.0)
        assert condition_from_dict(condition_to_dict(c)) == c

    def test_condition_all(self):
        c = All((Cmp("a", "lt", 1.0), Cmp("b", "gt", 0.0)))
        assert condition_from_dict(condition_to_dict(c)) == c

    def test_condition_any_(self):
        c = Any_((Cmp("a", "eq", True), Always()))
        assert condition_from_dict(condition_to_dict(c)) == c

    def test_condition_not_(self):
        c = Not_(Cmp("x", "ne", 0))
        assert condition_from_dict(condition_to_dict(c)) == c

    def test_condition_always(self):
        c = Always()
        assert condition_from_dict(condition_to_dict(c)) == c

    def test_condition_in_op_tuple_restored(self):
        c = Cmp("signal", "in", ("BUY", "SELL"))
        restored = condition_from_dict(condition_to_dict(c))
        assert isinstance(restored.value, tuple)
        assert restored == c

    def test_ack_spec(self):
        a = AckSpec(groups=(("rsi", "oversold"), ("sell",)))
        assert AckSpec.from_dict(a.to_dict()) == a

    def test_severity_spec_with_scale(self):
        spec = SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=65.0, at_max=80.0),
        )
        assert SeveritySpec.from_dict(spec.to_dict()) == spec

    def test_contradiction_verdict(self):
        v = ContradictionVerdict(
            contradictions=(_make_contradiction(),),
            matched_rule_ids=("r_test",),
            skipped=(SkippedRule("r2", "missing_signal:sentiment"),),
            evaluated_rules=2,
            engine_version="contradiction-judge/1.0.0",
            rules_version="abc123def456",
        )
        assert ContradictionVerdict.from_dict(v.to_dict()) == v


# ── C3: JSON-safe ──────────────────────────────────────────────────────────────

class TestJsonSafe:
    def test_proposal_json(self):
        p = Proposal(action="BUY", confidence=0.8)
        json.dumps(p.to_dict())  # must not raise

    def test_contradiction_json(self):
        json.dumps(_make_contradiction().to_dict())

    def test_rule_json(self):
        json.dumps(_make_rule().to_dict())

    def test_nested_condition_json(self):
        c = All((Cmp("rsi", "gt", 65.0), Not_(Cmp("trend_up", "eq", True))))
        json.dumps(condition_to_dict(c))

    def test_evidence_set_json(self):
        ev = EvidenceSet({"rsi": Signal("rsi", 72.0)})
        json.dumps(ev.to_dict())


# ── C4: Forward compat — claves desconocidas ignoradas ────────────────────────

class TestForwardCompat:
    def test_proposal_extra_keys(self):
        d = Proposal(action="BUY").to_dict()
        d["future_field"] = "ignored"
        p = Proposal.from_dict(d)
        assert p.action == "BUY"

    def test_contradiction_rule_extra_keys(self):
        d = _make_rule().to_dict()
        d["future_weight"] = 0.42
        r = ContradictionRule.from_dict(d)
        assert r.id == "r1"

    def test_verdict_missing_optional_keys(self):
        d = {
            "contradictions": [],
            "evaluated_rules": 0,
            "engine_version": "contradiction-judge/1.0.0",
            "rules_version": "abc",
        }
        v = ContradictionVerdict.from_dict(d)
        assert v.matched_rule_ids == ()
        assert v.skipped == ()


# ── C5: Validaciones ──────────────────────────────────────────────────────────

class TestValidation:
    def test_severity_spec_base_above_1(self):
        with pytest.raises(ValueError):
            SeveritySpec(base=1.1)

    def test_severity_spec_base_below_0(self):
        with pytest.raises(ValueError):
            SeveritySpec(base=-0.1)

    def test_severity_spec_base_at_bounds_ok(self):
        SeveritySpec(base=0.0)
        SeveritySpec(base=1.0)

    def test_contradiction_rule_missing_pair(self):
        with pytest.raises(ValueError, match="pair"):
            ContradictionRule(
                id="bad",
                kind=RuleKind.CONTRADICTION,
                applies_to=("BUY",),
                condition=Always(),
                direction=ConflictDirection("a", "b"),
                severity=SeveritySpec(base=0.5),
                # pair missing
            )

    def test_contradiction_rule_missing_direction(self):
        with pytest.raises(ValueError, match="direction"):
            ContradictionRule(
                id="bad",
                kind=RuleKind.CONTRADICTION,
                applies_to=("BUY",),
                condition=Always(),
                pair=("proposal.action", "signal.rsi"),
                severity=SeveritySpec(base=0.5),
                # direction missing
            )

    def test_contradiction_rule_missing_severity(self):
        with pytest.raises(ValueError, match="severity"):
            ContradictionRule(
                id="bad",
                kind=RuleKind.CONTRADICTION,
                applies_to=("BUY",),
                condition=Always(),
                pair=("proposal.action", "signal.rsi"),
                direction=ConflictDirection("a", "b"),
                # severity missing
            )

    def test_contradiction_scenario_kind_no_validation(self):
        # SCENARIO kind: no necesita pair/direction/severity
        r = ContradictionRule(
            id="scenario_ok",
            kind=RuleKind.SCENARIO,
            applies_to=("*",),
            condition=Always(),
        )
        assert r.kind == RuleKind.SCENARIO

    def test_contradiction_level_inconsistent(self):
        with pytest.raises(ValueError, match="level"):
            Contradiction(
                rule_id="bad",
                pair=("a", "b"),
                direction=ConflictDirection("x", "y"),
                severity=0.30,  # LOW
                level=SeverityLevel.HIGH,  # inconsistente
                acknowledged=False,
                acknowledged_by=(),
                contributing=(),
                details={},
            )

    def test_contradiction_from_dict_recomputes_level(self):
        d = _make_contradiction(0.75).to_dict()
        d["level"] = "low"  # valor incorrecto — from_dict recomputa
        c = Contradiction.from_dict(d)
        assert c.level == SeverityLevel.HIGH


# ── Severidad → nivel ─────────────────────────────────────────────────────────

class TestSeverityToLevel:
    @pytest.mark.parametrize("sev,expected", [
        (0.00, SeverityLevel.LOW),
        (0.39, SeverityLevel.LOW),
        (0.40, SeverityLevel.MEDIUM),
        (0.69, SeverityLevel.MEDIUM),
        (0.70, SeverityLevel.HIGH),
        (1.00, SeverityLevel.HIGH),
    ])
    def test_boundaries(self, sev, expected):
        assert severity_to_level(sev) == expected
