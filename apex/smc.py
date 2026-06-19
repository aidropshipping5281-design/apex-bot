"""Smart Money Concepts (ICT / 'TJR'-style) detectors.

These encode the concepts those traders teach as concrete, testable rules:
market structure, fair value gaps, liquidity sweeps, order blocks. They return
a bullish/bearish/neutral read used as ENTRY CONFLUENCE — not standalone signals.
Encoding a method is honest; whether it has an edge is decided by the backtest.
"""
import pandas as pd


def swings(df, left=2, right=2):
    """Mark swing highs/lows (fractals)."""
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    sh = [False] * n
    sl = [False] * n
    for i in range(left, n - right):
        if highs[i] == max(highs[i - left:i + right + 1]):
            sh[i] = True
        if lows[i] == min(lows[i - left:i + right + 1]):
            sl[i] = True
    return sh, sl


def market_structure(df):
    """Return 'bullish' (higher highs+lows), 'bearish' (lower highs+lows), or 'range'."""
    sh, sl = swings(df)
    last_highs = [df["high"].iloc[i] for i in range(len(df)) if sh[i]][-2:]
    last_lows = [df["low"].iloc[i] for i in range(len(df)) if sl[i]][-2:]
    if len(last_highs) < 2 or len(last_lows) < 2:
        return "range"
    hh = last_highs[-1] > last_highs[-2]
    hl = last_lows[-1] > last_lows[-2]
    lh = last_highs[-1] < last_highs[-2]
    ll = last_lows[-1] < last_lows[-2]
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "range"


def fair_value_gap(df):
    """Detect the most recent 3-candle FVG. Returns ('bull'/'bear'/None, gap_low, gap_high)."""
    if len(df) < 3:
        return None, None, None
    for i in range(len(df) - 1, 1, -1):
        a, c = df.iloc[i - 2], df.iloc[i]
        if a["high"] < c["low"]:                 # bullish imbalance
            return "bull", a["high"], c["low"]
        if a["low"] > c["high"]:                 # bearish imbalance
            return "bear", c["high"], a["low"]
    return None, None, None


def liquidity_sweep(df, lookback=20):
    """Did the last candle sweep a recent high/low then reverse? (stop run)"""
    if len(df) < lookback + 2:
        return None
    recent = df.iloc[-(lookback + 1):-1]
    last = df.iloc[-1]
    swept_high = last["high"] > recent["high"].max() and last["close"] < recent["high"].max()
    swept_low = last["low"] < recent["low"].min() and last["close"] > recent["low"].min()
    if swept_low:
        return "bull"      # swept sell-side liquidity, likely bullish reversal
    if swept_high:
        return "bear"
    return None


def confluence(df):
    """Combine the reads into a directional bias score in [-3, 3]."""
    score = 0
    ms = market_structure(df)
    if ms == "bullish":
        score += 1
    elif ms == "bearish":
        score -= 1
    fvg, _, _ = fair_value_gap(df)
    if fvg == "bull":
        score += 1
    elif fvg == "bear":
        score -= 1
    sweep = liquidity_sweep(df)
    if sweep == "bull":
        score += 1
    elif sweep == "bear":
        score -= 1
    bias = "bull" if score > 0 else "bear" if score < 0 else "neutral"
    return {"score": score, "bias": bias, "structure": ms, "fvg": fvg, "sweep": sweep}
