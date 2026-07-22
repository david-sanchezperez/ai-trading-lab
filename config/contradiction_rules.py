"""
Ruleset de contradicción para el lab de trading — adaptador trading del Juez 2.

Traducción 1:1 de la tabla if/elif de critic_node (graph/trading_graph.py:330-360).
priority preserva el orden original del if/elif.
scenario/key_question son los strings literales actuales, sin modificar.

Señales publicadas por el adaptador (de technical_result / sentiment_result):
  rsi, trend_up, rs_spy, pct_52w_range, sentiment

Notas de fidelidad (§2.5 DESIGN_JUDGE2.md):
  - Bordes estrictos: RSI 65 exacto no dispara R2 (< vs <=, igual que el código).
  - trending_up: del technical_result["trend_up"] (mismo que SMA20>SMA50 en critic).
  - R3 usa rs_spy < -0.03 (umbral del critic, no el -0.0 del technical_agent).
  - R7 (HOLD) y fallback "aligned" son kind=SCENARIO: no emiten Contradiction.
"""

from tribunal.contracts import (
    AckSpec,
    All,
    Always,
    Cmp,
    ConflictDirection,
    ContradictionRule,
    RuleKind,
    SeverityScale,
    SeveritySpec,
)

CONTRADICTION = RuleKind.CONTRADICTION
SCENARIO      = RuleKind.SCENARIO

TRADING_RULES: tuple[ContradictionRule, ...] = (

    # R1 — if/elif #1: RSI sobrevendido pero señal SELL
    ContradictionRule(
        id="rsi_oversold_vs_sell",
        kind=CONTRADICTION,
        applies_to=("SELL",),
        condition=Cmp("rsi", "lt", 35.0),
        pair=("proposal.action", "signal.rsi"),
        direction=ConflictDirection(a_claims="bearish", b_claims="bullish_reversion"),
        severity=SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=35.0, at_max=20.0),
        ),
        ack=AckSpec(groups=(("rsi", "oversold"),)),
        scenario="RSI in oversold zone but SELL signal — potential contradiction",
        key_question="Is selling justified when RSI signals oversold conditions?",
        priority=10,
    ),

    # R2 — if/elif #2: RSI sobrecomprado pero señal BUY
    ContradictionRule(
        id="rsi_overbought_vs_buy",
        kind=CONTRADICTION,
        applies_to=("BUY",),
        condition=Cmp("rsi", "gt", 65.0),
        pair=("proposal.action", "signal.rsi"),
        direction=ConflictDirection(a_claims="bullish", b_claims="overextended"),
        severity=SeveritySpec(
            base=0.60,
            scale=SeverityScale("rsi", at_base=65.0, at_max=80.0),
        ),
        ack=AckSpec(groups=(("rsi", "overbought"),)),
        scenario="RSI in overbought zone but BUY signal — potential overextension",
        key_question="Is buying justified when RSI signals overbought conditions?",
        priority=20,
    ),

    # R3 — if/elif #3: BUY contra tendencia bajista con debilidad relativa vs SPY
    ContradictionRule(
        id="buy_against_trend_weak_rs",
        kind=CONTRADICTION,
        applies_to=("BUY",),
        condition=All((
            Cmp("trend_up", "eq", False),
            Cmp("rs_spy", "lt", -0.03),
        )),
        pair=("proposal.action", "signal.trend_up"),
        contributing=("signal.rs_spy",),
        direction=ConflictDirection(a_claims="bullish", b_claims="bearish_regime"),
        severity=SeveritySpec(base=0.80),
        ack=AckSpec(groups=(
            ("downtrend", "bearish trend"),
            ("underperform", "relative weakness", "lagging"),
        )),
        scenario="BUY against bearish trend with relative weakness vs SPY",
        key_question="Is it prudent to buy when the ticker underperforms the market in a downtrend?",
        priority=30,
    ),

    # R4 — if/elif #4: BUY cerca de máximos anuales
    ContradictionRule(
        id="buy_near_52w_high",
        kind=CONTRADICTION,
        applies_to=("BUY",),
        condition=Cmp("pct_52w_range", "gt", 0.82),
        pair=("proposal.action", "signal.pct_52w_range"),
        direction=ConflictDirection(a_claims="bullish", b_claims="overextended"),
        severity=SeveritySpec(
            base=0.50,
            scale=SeverityScale("pct_52w_range", at_base=0.82, at_max=0.97),
        ),
        ack=AckSpec(groups=(("52-week", "52w", "yearly high", "annual high", "near highs"),)),
        scenario="BUY near 52-week highs — potential overextension",
        key_question="Is the price too extended near its 52-week highs?",
        priority=40,
    ),

    # R5 — if/elif #5: BUY con sentimiento negativo
    ContradictionRule(
        id="buy_vs_negative_sentiment",
        kind=CONTRADICTION,
        applies_to=("BUY",),
        condition=Cmp("sentiment", "lt", -0.3),
        pair=("proposal.action", "signal.sentiment"),
        direction=ConflictDirection(a_claims="bullish", b_claims="bearish"),
        severity=SeveritySpec(
            base=0.55,
            scale=SeverityScale("sentiment", at_base=-0.3, at_max=-0.8),
        ),
        ack=AckSpec(groups=(
            ("sentiment", "news", "headlines"),
            ("negative", "bearish", "pessimistic"),
        )),
        scenario="BUY signal but negative sentiment — divergence",
        key_question="Does negative sentiment invalidate the bullish signal?",
        priority=50,
    ),

    # R6 — if/elif #6: SELL con sentimiento positivo
    ContradictionRule(
        id="sell_vs_positive_sentiment",
        kind=CONTRADICTION,
        applies_to=("SELL",),
        condition=Cmp("sentiment", "gt", 0.3),
        pair=("proposal.action", "signal.sentiment"),
        direction=ConflictDirection(a_claims="bearish", b_claims="bullish"),
        severity=SeveritySpec(
            base=0.55,
            scale=SeverityScale("sentiment", at_base=0.3, at_max=0.8),
        ),
        ack=AckSpec(groups=(
            ("sentiment", "news", "headlines"),
            ("positive", "bullish", "optimistic"),
        )),
        scenario="SELL signal but positive sentiment — divergence",
        key_question="Does positive sentiment invalidate the bearish signal?",
        priority=60,
    ),

    # R7 — if/elif #7: HOLD — no es contradicción, es disparador de deliberación
    ContradictionRule(
        id="hold_review",
        kind=SCENARIO,
        applies_to=("HOLD",),
        condition=Always(),
        scenario="HOLD signal — evaluate if there is reason to act",
        key_question="Is there a clear reason to act, or is holding the correct decision?",
        priority=70,
    ),

    # Fallback — el `else` actual: señales alineadas, verificar coherencia
    ContradictionRule(
        id="aligned_signals_review",
        kind=SCENARIO,
        applies_to=("*",),
        condition=Always(),
        scenario="Aligned signals — verify coherence",
        key_question="Are all indicators pointing in the same direction?",
        priority=999,
    ),
)
