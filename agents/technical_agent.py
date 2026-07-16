import math


def generate_signal(df):
    """
    Sistema de votación multi-indicador.

    Cada condición emite votos BUY o SELL. La confianza final es proporcional
    al consenso. Principios expertos aplicados:
      - Confirmación por volumen (no operar en silencio)
      - Posición en rango anual (evitar comprar máximos / vender mínimos)
      - Fortaleza relativa vs SPY (operar con el mercado, no contra él)
      - Tendencia como filtro de régimen (no luchar contra la tendencia)
    """
    latest = df.iloc[-1]

    price       = float(latest["Close"])
    rsi         = float(latest["RSI"])
    sma_20      = float(latest["SMA_20"])
    sma_50      = float(latest["SMA_50"])
    momentum    = float(latest.get("Momentum", 0))
    volume_ratio = float(latest.get("volume_ratio", 1.0))
    atr_14      = float(latest.get("ATR_14", 0))
    pct_52w     = float(latest.get("pct_52w_range", 0.5))
    rs_spy      = float(latest.get("RS_SPY", 0.0))
    dist_sma20  = float(latest.get("dist_sma20", 0.0))

    # Guard: NaN safety
    for val, name in [(rsi, "rsi"), (sma_20, "sma_20"), (sma_50, "sma_50"), (atr_14, "atr_14")]:
        if math.isnan(val):
            return {
                "signal": "HOLD", "confidence": 0.5, "rsi": rsi,
                "price": price, "atr_14": 0.0, "volume_ratio": 1.0, "pct_52w_range": 0.5,
                "rs_spy": 0.0, "dist_sma20": 0.0, "buy_votes": 0, "sell_votes": 0,
            }

    trend_up   = sma_20 > sma_50
    trend_down = sma_20 < sma_50

    # ── Voting system ─────────────────────────────────────────────────────────
    # Max buy_votes = 7, max sell_votes = 7
    buy_votes  = 0
    sell_votes = 0

    # 1. RSI — mean reversion signal (weight: 1-2)
    if rsi < 35:
        buy_votes += 2      # strong oversold
    elif rsi < 45:
        buy_votes += 1      # mild weakness
    elif rsi > 70:
        sell_votes += 2     # strong overbought
    elif rsi > 60:
        sell_votes += 1     # mild elevation

    # 2. Trend (SMA20 vs SMA50) — directional filter (weight: 1)
    if trend_up:
        buy_votes += 1
    elif trend_down:
        sell_votes += 1

    # 3. Pullback / extension from SMA20 — entry quality (weight: 1)
    # Buying below SMA20 in uptrend = quality entry; above in downtrend = dangerous
    if dist_sma20 < -0.03:     # price > 3% below SMA20 → potential bounce
        buy_votes += 1
    elif dist_sma20 > 0.05:    # price > 5% above SMA20 → extended
        sell_votes += 1

    # 4. 52-week range position — valuation context (weight: 1)
    if pct_52w < 0.25:          # lower quartile of year range → value zone
        buy_votes += 1
    elif pct_52w > 0.85:        # top 15% of year range → stretched
        sell_votes += 1

    # 5. Relative Strength vs SPY — market leadership (weight: 1)
    if rs_spy > 0.04:           # outperforming SPY by >4% over 20 days
        buy_votes += 1
    elif rs_spy < -0.04:        # underperforming SPY by >4%
        sell_votes += 1

    # 6. Momentum (10-day price change) — short-term direction (weight: 1)
    if momentum > 0:
        buy_votes += 1
    elif momentum < 0:
        sell_votes += 1

    net = buy_votes - sell_votes  # range: -7 to +7

    # ── Confidence mapping ─────────────────────────────────────────────────────
    abs_net = abs(net)
    if abs_net >= 5:
        base_confidence = 0.85
    elif abs_net == 4:
        base_confidence = 0.75
    elif abs_net == 3:
        base_confidence = 0.70
    elif abs_net == 2:
        base_confidence = 0.65
    elif abs_net == 1:
        base_confidence = 0.55
    else:
        base_confidence = 0.50

    # Volume boost: high volume confirms the signal (+0.05, cap 0.90)
    if volume_ratio > 1.5 and abs_net >= 2:
        base_confidence = min(0.90, base_confidence + 0.05)

    # ── Signal direction ───────────────────────────────────────────────────────
    if net > 0:
        signal = "BUY"
    elif net < 0:
        signal = "SELL"
    else:
        signal = "HOLD"

    confidence = base_confidence if net != 0 else 0.50

    # ── Expert regime filter: penalize "catching a falling knife" ─────────────
    # BUY in downtrend + underperforming market = highest-risk scenario
    if signal == "BUY" and trend_down and rs_spy < 0.0:
        confidence = max(0.50, confidence - 0.15)
        if confidence <= 0.50:
            signal = "HOLD"

    # SELL in uptrend + outperforming market = low-conviction short signal
    if signal == "SELL" and trend_up and rs_spy > 0.0:
        confidence = max(0.50, confidence - 0.10)
        if confidence <= 0.50:
            signal = "HOLD"

    return {
        "signal":        signal,
        "confidence":    round(confidence, 2),
        "rsi":           round(rsi, 2),
        "price":         round(price, 2),
        "atr_14":        round(atr_14, 2) if atr_14 > 0 else 0.0,
        "trend_up":      trend_up,
        "volume_ratio":  round(volume_ratio, 2),
        "pct_52w_range": round(pct_52w, 3),
        "rs_spy":        round(rs_spy, 4),
        "dist_sma20":    round(dist_sma20, 4),
        "buy_votes":     buy_votes,
        "sell_votes":    sell_votes,
    }
