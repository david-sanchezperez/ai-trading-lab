import pandas as pd


def compute_sma(df, window):
    return df["Close"].rolling(window=window).mean()


def compute_rsi(df, window=14):
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=window).mean()
    avg_loss = loss.rolling(window=window).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_momentum(df, window=10):
    return df["Close"] - df["Close"].shift(window)


def compute_atr(df, window=14):
    """
    Average True Range (ATR) — volatilidad media de trading ranges.
    True Range = max(H-L, |H-C_prev|, |L-C_prev|)
    ATR = EMA de TR over window.
    """
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=window).mean()
    
    return atr


def add_indicators(df):
    df = df.copy()

    # Trend
    df["SMA_20"] = compute_sma(df, 20)
    df["SMA_50"] = compute_sma(df, 50)

    # Momentum & oscillator
    df["RSI"] = compute_rsi(df, 14)
    df["Momentum"] = compute_momentum(df, 10)

    # ATR (14-day average true range) — volatility measure
    df["ATR_14"] = compute_atr(df, 14)

    # Volume ratio vs 20-day average (1.0 = average, 2.0 = double volume)
    df["volume_ratio"] = (df["Volume"] / df["Volume"].rolling(20).mean()).fillna(1.0)

    # Position within 52-week range (0.0 = at 52w low, 1.0 = at 52w high)
    high_52w = df["High"].rolling(252, min_periods=60).max()
    low_52w  = df["Low"].rolling(252, min_periods=60).min()
    range_52w = (high_52w - low_52w).replace(0, float("nan"))
    df["pct_52w_range"] = ((df["Close"] - low_52w) / range_52w).fillna(0.5)

    # Distance from SMA20 as a fraction (positive = above, negative = below)
    df["dist_sma20"] = ((df["Close"] - df["SMA_20"]) / df["SMA_20"]).fillna(0.0)

    return df


def add_relative_strength(df: pd.DataFrame, spy_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Adds RS_SPY: 20-day excess return vs SPY.
    Positive = ticker outperforming SPY, negative = underperforming.
    Falls back to 0.0 if SPY data is unavailable.
    """
    if spy_df is None or spy_df.empty:
        df["RS_SPY"] = 0.0
        return df

    df = df.copy()

    spy = spy_df.copy()
    spy["Date"] = pd.to_datetime(spy["Date"])
    spy_indexed = spy.set_index("Date")["Close"]

    ticker_dates = pd.to_datetime(df["Date"])
    spy_aligned = spy_indexed.reindex(ticker_dates.values, method="ffill")

    ticker_ret_20 = df["Close"].pct_change(20)
    spy_ret_20 = spy_aligned.pct_change(20).values

    df["RS_SPY"] = (ticker_ret_20 - spy_ret_20).fillna(0.0)

    return df
