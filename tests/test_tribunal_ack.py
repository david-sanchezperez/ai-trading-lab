"""
Tests tribunal/ack.py — A1-A8 según §5 de DESIGN_JUDGE2.md.

A1: Término de una palabra: match y no-match.
A2: Término multi-palabra: subsecuencia CONSECUTIVA de tokens.
A3: Case-insensitive.
A4: Diacríticos normalizados (NFKD).
A5: Grupos múltiples: AND de grupos, OR dentro de grupo.
A6: Rationale vacío → not acknowledged.
A7: spec=None → not acknowledged.
A8: Tests de caracterización — limitaciones conocidas (§3.2).
"""

import pytest

from tribunal.contracts import AckSpec
from tribunal.ack import check_acknowledged, _normalize, _matches_term


# ── _normalize ────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_basic_tokenization(self):
        assert _normalize("RSI is Oversold!") == ["rsi", "is", "oversold"]

    def test_removes_diacritics(self):
        assert _normalize("sobrevendido") == ["sobrevendido"]
        assert _normalize("résumé café") == ["resume", "cafe"]

    def test_casefold(self):
        assert _normalize("BUY") == ["buy"]

    def test_hyphen_splits_tokens(self):
        tokens = _normalize("52-week high")
        assert "52" in tokens
        assert "week" in tokens

    def test_empty_string(self):
        assert _normalize("") == []


# ── A1: Término de una sola palabra ──────────────────────────────────────────

class TestSingleWordTerm:
    def test_single_word_match(self):
        spec = AckSpec(groups=(("oversold",),))
        ack, by = check_acknowledged(spec, "RSI is oversold")
        assert ack is True
        assert "oversold" in by

    def test_single_word_no_match(self):
        spec = AckSpec(groups=(("oversold",),))
        ack, by = check_acknowledged(spec, "RSI is too low")
        assert ack is False
        assert by == ()


# ── A2: Término multi-palabra — subsecuencia consecutiva ─────────────────────

class TestMultiWordTerm:
    def test_consecutive_match(self):
        spec = AckSpec(groups=(("52-week",),))
        ack, _ = check_acknowledged(spec, "price is near its 52 week high")
        assert ack is True

    def test_non_consecutive_no_match(self):
        spec = AckSpec(groups=(("52-week",),))
        # "52 items week" — los tokens [52, items, week] no son consecutivos [52, week]
        ack, _ = check_acknowledged(spec, "52 items this week")
        assert ack is False

    def test_multi_word_at_start(self):
        spec = AckSpec(groups=(("annual high",),))
        ack, _ = check_acknowledged(spec, "annual high is near")
        assert ack is True

    def test_multi_word_at_end(self):
        spec = AckSpec(groups=(("near highs",),))
        ack, _ = check_acknowledged(spec, "price is near highs")
        assert ack is True


# ── A3: Case-insensitive ─────────────────────────────────────────────────────

class TestCaseInsensitive:
    def test_uppercase_rationale(self):
        spec = AckSpec(groups=(("oversold",),))
        ack, _ = check_acknowledged(spec, "RSI IS OVERSOLD")
        assert ack is True

    def test_uppercase_term_in_spec(self):
        spec = AckSpec(groups=(("OVERSOLD",),))
        ack, _ = check_acknowledged(spec, "rsi is oversold")
        assert ack is True

    def test_mixed_case(self):
        spec = AckSpec(groups=(("OverSold",),))
        ack, _ = check_acknowledged(spec, "RSI oversold territory")
        assert ack is True


# ── A4: Diacríticos ──────────────────────────────────────────────────────────

class TestDiacritics:
    def test_accented_rationale_matches_plain_term(self):
        spec = AckSpec(groups=(("sobrevendido",),))
        ack, _ = check_acknowledged(spec, "RSI sobrevendído zona")
        # "sobrevendído" normalizado → "sobrevendido"
        assert ack is True

    def test_accented_term_matches_plain_rationale(self):
        spec = AckSpec(groups=(("résumé",),))
        ack, _ = check_acknowledged(spec, "a resume of the situation")
        assert ack is True


# ── A5: Grupos múltiples ─────────────────────────────────────────────────────

class TestMultipleGroups:
    def test_and_logic_all_groups_must_match(self):
        spec = AckSpec(groups=(
            ("sentiment", "news"),
            ("negative", "bearish"),
        ))
        # Ambos grupos matchean
        ack, by = check_acknowledged(spec, "negative sentiment divergence")
        assert ack is True
        assert len(by) == 2

    def test_and_logic_fails_if_one_group_missing(self):
        spec = AckSpec(groups=(
            ("sentiment", "news"),
            ("negative", "bearish"),
        ))
        # Solo primer grupo matchea
        ack, _ = check_acknowledged(spec, "sentiment looks good")
        assert ack is False

    def test_or_logic_within_group_first_term_wins(self):
        spec = AckSpec(groups=(("rsi", "momentum"),))
        ack, by = check_acknowledged(spec, "rsi is elevated")
        assert ack is True
        assert "rsi" in by

    def test_or_logic_within_group_second_term_wins(self):
        spec = AckSpec(groups=(("rsi", "momentum"),))
        ack, by = check_acknowledged(spec, "momentum breakout confirmed")
        assert ack is True
        assert "momentum" in by

    def test_three_groups(self):
        spec = AckSpec(groups=(("buy",), ("bearish",), ("confirm",)))
        ack, _ = check_acknowledged(spec, "buy despite bearish news confirm position")
        assert ack is True

    def test_three_groups_one_missing(self):
        spec = AckSpec(groups=(("buy",), ("bearish",), ("confirm",)))
        ack, _ = check_acknowledged(spec, "buy despite bearish news")
        assert ack is False


# ── A6: Rationale vacío ──────────────────────────────────────────────────────

class TestEmptyRationale:
    def test_empty_string(self):
        spec = AckSpec(groups=(("oversold",),))
        ack, by = check_acknowledged(spec, "")
        assert ack is False
        assert by == ()

    def test_whitespace_only(self):
        spec = AckSpec(groups=(("oversold",),))
        ack, by = check_acknowledged(spec, "   ")
        assert ack is False


# ── A7: spec=None ────────────────────────────────────────────────────────────

class TestNoneSpec:
    def test_none_spec_returns_false(self):
        ack, by = check_acknowledged(None, "rsi is oversold, acknowledging contradiction")
        assert ack is False
        assert by == ()


# ── A8: Tests de caracterización — limitaciones conocidas ────────────────────

class TestKnownLimitations:
    """
    Estos tests documentan comportamiento real, no bug.
    Las limitaciones están descritas en §3.2 del diseño.
    """

    def test_false_positive_negation(self):
        # "not oversold" matchea el término "oversold" — falso positivo conocido (§3.2)
        spec = AckSpec(groups=(("oversold",),))
        ack, _ = check_acknowledged(spec, "RSI is not oversold")
        assert ack is True  # comportamiento real documentado

    def test_false_negative_paraphrase(self):
        # Paráfrasis sin tokens literales → no matchea
        spec = AckSpec(groups=(("oversold",),))
        ack, _ = check_acknowledged(spec, "RSI indicates extreme selling pressure")
        assert ack is False

    def test_false_negative_numeric_only(self):
        # Mención solo numérica ("RSI at 28") sin la palabra "oversold"
        spec = AckSpec(groups=(("oversold",),))
        ack, _ = check_acknowledged(spec, "RSI is at 28 which is extreme")
        assert ack is False

    def test_no_stemming(self):
        # Sin stemming: "overextended" no matchea "overextend"
        spec = AckSpec(groups=(("overextend",),))
        ack, _ = check_acknowledged(spec, "price is overextended")
        assert ack is False
