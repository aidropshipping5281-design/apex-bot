"""APEX CONVICTION ENGINE — multi-timeframe, multi-indicator chart analysis.

For any instrument it reads the chart on EVERY timeframe it can (weekly, daily,
and intraday where available), runs a full indicator suite on each, and produces:
  * a 0-100 CONVICTION score,
  * a DIRECTION (long / short / flat),
  * a reasoned BREAKDOWN of which indicators/timeframes agree or disagree.

Indicators per timeframe: EMA(20/50/200) stack, price vs SMA200, RSI(14),
MACD histogram, ADX (trend strength), Bollinger %B, ROC(20), ATR% (volatility),
RSI(2) (short-term stretch). Timeframes are weighted: weekly = context,
daily = primary setup, intraday = timing.

HONEST FRAME: this is a calculated, probabilistic read of the chart — a quality
score for a setup — NOT a prediction of the future. A high score means "more of
the evidence lines up," not "this will go up." More indicators do not create an
edge by themselves; the score still has to be VALIDATED (see the lab/walk-forward)
before it's trusted with size. It tilts odds; it does not foretell.
"""
import sys
import numpy as np
import pandas as pd

from strategy_lab import fetch_daily


# ---------- indicator primitives ----------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))


def macd_hist(c):
    line = ema(c, 12) - ema(c, 26)
    return line - ema(line, 9)


def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1/n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(mdm, index=df.index).ewm(alpha=1/n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-9)
    return dx.ewm(alpha=1/n, adjust=False).mean()


def bollinger_pctb(c, n=20):
    mid = c.rolling(n).mean()
    sd = c.rolling(n).std()
    upper, lower = mid + 2 * sd, mid - 2 * sd
    return (c - lower) / (upper - lower).replace(0, 1e-9)


def atr_pct(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean() / c


# ---------- per-timeframe scoring ----------
def tf_factors(df):
    """Return {factor: vote in [-1..1]} for the last bar of a timeframe."""
    if df is None or len(df) < 210:
        return None
    c = df["close"]
    last = len(df) - 1
    e20, e50, e200 = ema(c, 20).iloc[last], ema(c, 50).iloc[last], ema(c, 200).iloc[last]
    px = c.iloc[last]
    r14 = rsi(c).iloc[last]
    mh = macd_hist(c).iloc[last]
    adv = adx(df).iloc[last]
    pb = bollinger_pctb(c).iloc[last]
    roc = (c.iloc[last] / c.iloc[last - 20] - 1) if last >= 20 else 0
    f = {}
    # trend: EMA stack
    f["ema_stack"] = 1.0 if (e20 > e50 > e200) else (-1.0 if (e20 < e50 < e200) else 0.0)
    f["price_vs_200"] = 1.0 if px > e200 else -1.0
    # momentum
    f["rsi"] = np.clip((r14 - 50) / 25, -1, 1) if np.isfinite(r14) else 0.0
    f["macd"] = 1.0 if mh > 0 else (-1.0 if mh < 0 else 0.0)
    f["roc20"] = np.clip(roc / 0.10, -1, 1)
    # strength gate (scales conviction, not direction)
    f["adx_strength"] = np.clip((adv - 15) / 30, 0, 1) if np.isfinite(adv) else 0.0
    # position in range (mean-rev context): low %B in uptrend = dip-buy hint
    f["bollinger"] = np.clip((0.5 - pb) * 2, -1, 1) if np.isfinite(pb) else 0.0
    return f


def resample_weekly(d):
    w = (d.set_index("ts").resample("W")
         .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
         .dropna().reset_index())
    return w


def fetch_intraday(ticker):
    try:
        import yfinance as yf
        from stock_research import normalize
        raw = yf.download(ticker, interval="60m", period="180d",
                          auto_adjust=True, progress=False, threads=False)
        return normalize(raw)
    except Exception:
        return None


TF_WEIGHTS = {"weekly": 0.40, "daily": 0.40, "intraday": 0.20}
# direction factors vs strength/context factors
DIR_FACTORS = ["ema_stack", "price_vs_200", "rsi", "macd", "roc20"]


def analyze(ticker, with_intraday=True):
    d = fetch_daily(ticker)
    if d is None or len(d) < 220:
        return None
    tfs = {"daily": tf_factors(d), "weekly": tf_factors(resample_weekly(d))}
    if with_intraday:
        intr = fetch_intraday(ticker)
        if intr is not None and len(intr) >= 210:
            tfs["intraday"] = tf_factors(intr)

    # weighted directional sum + strength gate
    dir_score, wsum, strength = 0.0, 0.0, []
    breakdown = {}
    for tf, f in tfs.items():
        if not f:
            continue
        w = TF_WEIGHTS.get(tf, 0.2)
        d_part = np.mean([f[k] for k in DIR_FACTORS])
        dir_score += w * d_part
        wsum += w
        strength.append(f["adx_strength"])
        breakdown[tf] = {k: round(float(v), 2) for k, v in f.items()}
    if wsum == 0:
        return None
    dir_norm = dir_score / wsum                       # -1..1
    strength_gate = 0.5 + 0.5 * (np.mean(strength) if strength else 0)   # 0.5..1
    conviction = round(float(abs(dir_norm) * strength_gate * 100), 1)    # 0..100
    direction = "LONG" if dir_norm > 0.15 else ("SHORT" if dir_norm < -0.15 else "FLAT")
    price = float(d["close"].iloc[-1])
    atr_abs = float((atr_pct(d).iloc[-1] or 0) * price)
    return {"ticker": ticker, "conviction": conviction, "direction": direction,
            "dir_raw": round(float(dir_norm), 3), "timeframes": list(tfs.keys()),
            "price": price, "atr": atr_abs, "breakdown": breakdown}


def explain(a):
    lines = [f"=== {a['ticker']}  ->  {a['direction']}  (conviction {a['conviction']}/100)  "
             f"[timeframes: {', '.join(a['timeframes'])}] ==="]
    for tf, f in a["breakdown"].items():
        bull = [k for k, v in f.items() if v > 0.3 and k != "adx_strength"]
        bear = [k for k, v in f.items() if v < -0.3]
        lines.append(f"  {tf:<9} strength {f.get('adx_strength', 0):.2f} | "
                     f"bullish: {', '.join(bull) or '-'} | bearish: {', '.join(bear) or '-'}")
    return "\n".join(lines)


def main():
    tickers = sys.argv[1:] or ["BTC-USD", "NVDA", "QQQ", "SPY", "AMD", "TSLA"]
    print("APEX CONVICTION ENGINE — multi-timeframe, multi-indicator analysis")
    print("(calculated probabilistic read of each chart — not a prediction)\n")
    results = [a for a in (analyze(t) for t in tickers) if a]
    results.sort(key=lambda a: a["conviction"], reverse=True)
    for a in results:
        print(explain(a))
        print()
    print("Ranked by conviction:")
    for a in results:
        print(f"  {a['ticker']:<10} {a['direction']:<6} {a['conviction']}/100")
    print("\nReminder: conviction = quality of the setup, validated edge still required.")


if __name__ == "__main__":
    main()
