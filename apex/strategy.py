"""Trading strategy: EMA-cross core + multi-timeframe trend filter + RSI/MACD
+ Smart Money Concepts confluence. Supports LONG and SHORT.

Signals returned by signal():
  'long'  open/keep long
  'short' open/keep short   (only if cfg.allow_short)
  'flat'  close any open position
  None    do nothing

The logic is transparent on purpose. It is a tested template, not a guaranteed
edge — prove it in backtest + paper before trusting it with money.
"""
import pandas as pd
from . import smc


def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()


def rsi(close, period=14):
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / dn.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def macd(close, fast=12, slow=26, signal_p=9):
    macd_line = ema(close, fast) - ema(close, slow)
    sig = ema(macd_line, signal_p)
    return macd_line, sig


def atr(df, period):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _cfgval(cfg, name, default):
    return getattr(cfg, name, default) if cfg is not None else default


def compute_indicators(df, cfg):
    df = df.copy()
    f = _cfgval(cfg, "ema_fast", 9)
    s = _cfgval(cfg, "ema_slow", 21)
    ap = _cfgval(cfg, "atr_period", 14)
    df["ema_fast"] = ema(df["close"], f)
    df["ema_slow"] = ema(df["close"], s)
    df["htf_ema"] = ema(df["close"], s * 4)          # higher-timeframe trend proxy
    df["rsi"] = rsi(df["close"])
    ml, sg = macd(df["close"])
    df["macd"], df["macd_sig"] = ml, sg
    df["atr"] = atr(df, ap)
    return df


def signal(df, cfg=None):
    """Multi-factor signal. Requires EMA cross + higher-TF agreement + momentum,
    with SMC confluence as a tie-breaker."""
    if len(df) < 5:
        return None
    allow_short = bool(_cfgval(cfg, "allow_short", False))
    use_smc = bool(_cfgval(cfg, "use_smc", True))
    prev, last = df.iloc[-2], df.iloc[-1]

    crossed_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    crossed_dn = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]
    htf_up = last["close"] > last["htf_ema"]
    htf_dn = last["close"] < last["htf_ema"]
    momentum_up = last["macd"] > last["macd_sig"] and last["rsi"] < 70
    momentum_dn = last["macd"] < last["macd_sig"] and last["rsi"] > 30

    bias = smc.confluence(df)["bias"] if use_smc else "neutral"

    if crossed_up and htf_up and momentum_up and (not use_smc or bias != "bear"):
        return "long"
    if allow_short and crossed_dn and htf_dn and momentum_dn and (not use_smc or bias != "bull"):
        return "short"
    if crossed_dn:
        return "flat"          # trend flip closes an open long
    if allow_short and crossed_up:
        return "flat"          # trend flip closes an open short
    return None
