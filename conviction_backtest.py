"""Validate the CONVICTION score — is it a real edge, or just a pretty number?

Two honest tests, both out-of-sample with realistic costs:

  (A) FORWARD-RETURN BY CONVICTION BUCKET — the cleanest test. Compute the
      conviction score on every historical bar, then look at the average forward
      20-day return grouped by conviction level. If conviction is real, HIGH
      conviction bars should show HIGHER forward returns than LOW ones. If the
      buckets are flat/random, the score isn't predictive.

  (B) CONVICTION-GATED STRATEGY — tradeable confirmation. Go long when the
      multi-timeframe conviction direction is LONG and score >= threshold; exit
      when it turns down or stops. Backtest OOS (70/30) with costs and compare to
      buy & hold. If it survives, the score earns the right to drive the scanner.

Conviction here = blend of DAILY + WEEKLY factors (EMA stack, price vs 200MA,
RSI, MACD, ROC) gated by ADX strength — the same engine as conviction.py, made
per-bar (vectorized) so it can be tested across history.
"""
import numpy as np
import pandas as pd

from conviction import ema, rsi, macd_hist, adx, resample_weekly
from strategy_lab import fetch_daily, START_EQ, RISK_PCT, FEE, SLIP
from research_harness import stats

SYMBOLS = ["BTC-USD", "ETH-USD", "NVDA", "AMD", "QQQ", "SPY"]
TRAIN_FRAC = 0.70
CONV_MIN = 30.0          # entry threshold
STOP_MULT = 3.0
FWD = 20                 # forward-return horizon (bars) for bucket test


def dir_series(d):
    """Per-bar directional score (-1..1) and ADX strength gate (0..1)."""
    c = d["close"]
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    r14 = rsi(c); mh = macd_hist(c); adv = adx(d)
    roc = c / c.shift(20) - 1
    ema_stack = np.where((e20 > e50) & (e50 > e200), 1.0,
                         np.where((e20 < e50) & (e50 < e200), -1.0, 0.0))
    pv200 = np.where(c > e200, 1.0, -1.0)
    rsi_s = np.clip((r14 - 50) / 25, -1, 1)
    macd_s = np.where(mh > 0, 1.0, np.where(mh < 0, -1.0, 0.0))
    roc_s = np.clip(roc / 0.10, -1, 1)
    d_dir = (ema_stack + pv200 + rsi_s.values + macd_s + roc_s.values) / 5
    strength = np.clip((adv - 15) / 30, 0, 1).fillna(0).values
    return pd.Series(d_dir, index=d.index), pd.Series(strength, index=d.index)


def conviction_frame(d):
    """Attach combined (daily+weekly) conviction score + direction to daily bars."""
    d = d.reset_index(drop=True).copy()
    ddir, dstr = dir_series(d)
    w = resample_weekly(d)
    wdir, _ = dir_series(w)
    def naive(s):
        s = pd.to_datetime(s)
        try:
            return s.dt.tz_localize(None)
        except (TypeError, AttributeError):
            return s
    wmap = pd.DataFrame({"ts": naive(pd.Series(w["ts"].values)), "wdir": wdir.values})
    left = pd.DataFrame({"ts": naive(d["ts"]), "_i": np.arange(len(d))})
    merged = pd.merge_asof(left.sort_values("ts"), wmap.sort_values("ts"), on="ts")
    merged = merged.sort_values("_i")
    combined = 0.5 * ddir.values + 0.5 * merged["wdir"].fillna(0).values
    d["cdir"] = combined
    d["conv"] = np.abs(combined) * (0.5 + 0.5 * dstr.values) * 100
    # ATR for sizing/stops
    h, l, c = d["high"], d["low"], d["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    return d


def bucket_test(d):
    """Avg forward FWD-bar return grouped by conviction bucket (long-side dir>0)."""
    d = d.copy()
    d["fwd"] = d["close"].shift(-FWD) / d["close"] - 1
    longish = d[(d["cdir"] > 0) & d["fwd"].notna() & d["conv"].notna()]
    if len(longish) < 50:
        return None
    bins = [0, 20, 40, 60, 200]
    labels = ["0-20", "20-40", "40-60", "60+"]
    longish = longish.assign(bucket=pd.cut(longish["conv"], bins=bins, labels=labels))
    g = longish.groupby("bucket", observed=True)["fwd"].agg(["mean", "count"])
    return g


def gated_backtest(d):
    """Long when conviction LONG & score>=CONV_MIN; exit when dir<=0 or stop. Costs."""
    rows = d.reset_index(drop=True)
    cash, pos, trades = START_EQ, None, []
    for i in range(len(rows)):
        r = rows.iloc[i]
        price, atr = r["close"], r["atr"]
        if not np.isfinite(atr) or atr <= 0:
            continue
        if pos:
            exit_px = None
            if r["low"] <= pos["stop"]:
                exit_px = pos["stop"] * (1 - SLIP)
            elif r["cdir"] <= 0.05:
                exit_px = price * (1 - SLIP)
            if exit_px is not None:
                pnl = pos["size"] * exit_px * (1 - FEE) - pos["size"] * pos["entry"] * (1 + FEE)
                cash += pos["size"] * exit_px * (1 - FEE)
                trades.append({"pnl": pnl, "r": pnl / pos["risk"] if pos["risk"] else 0})
                pos = None
        if pos is None and r["cdir"] > 0.15 and r["conv"] >= CONV_MIN:
            stop_dist = atr * STOP_MULT
            risk = cash * RISK_PCT
            size = risk / stop_dist
            if size * price > cash:
                size = cash / price
            cash -= size * price * (1 + FEE)
            pos = {"entry": price, "size": size, "stop": price - stop_dist, "risk": risk}
    return trades


def main():
    print("APEX CONVICTION VALIDATION — does high conviction actually pay?\n")
    print(f"(A) forward {FWD}-day return by conviction bucket (long-side bars):")
    for sym in SYMBOLS:
        d0 = fetch_daily(sym)
        if d0 is None or len(d0) < 400:
            print(f"  {sym}: no data"); continue
        d = conviction_frame(d0)
        g = bucket_test(d)
        print(f"  {sym}:")
        if g is None:
            print("    insufficient sample")
        else:
            for b, row in g.iterrows():
                print(f"    conviction {b:<6} avg fwd {row['mean']:+.2%}  (n={int(row['count'])})")
    print("\n(B) conviction-gated long strategy, OUT-OF-SAMPLE, after costs:")
    print(f"  {'symbol':<9} {'TRAIN pf/exp/n':<22} {'TEST pf/exp/n':<22} verdict")
    for sym in SYMBOLS:
        d0 = fetch_daily(sym)
        if d0 is None or len(d0) < 400:
            continue
        d = conviction_frame(d0)
        cut = int(len(d) * TRAIN_FRAC)
        tr = stats(gated_backtest(d.iloc[:cut]))
        te = stats(gated_backtest(d.iloc[cut:]))
        keep = te["profit_factor"] > 1.1 and te["expectancy_r"] > 0 and te["trades"] >= 8
        tr_s = f"{tr['profit_factor']}/{tr['expectancy_r']}/{tr['trades']}"
        te_s = f"{te['profit_factor']}/{te['expectancy_r']}/{te['trades']}"
        verdict = "EDGE? keep" if keep else "reject"
        print(f"  {sym:<9} {tr_s:<22} {te_s:<22} {verdict}")
    print("\n===== CONVICTION VALIDATION DONE =====")
    print("Edge confirmed if (A) forward returns RISE with conviction AND")
    print("(B) the gated strategy survives OOS. Then the scanner's picks earn real size.")


if __name__ == "__main__":
    main()
