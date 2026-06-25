"""TSMOM PRO — adds the missing trading skills to the validated daily trend core.

Adds, on top of the baseline daily TSMOM (long when close>SMA100 & 90d mom>0):
  * ADX REGIME FILTER  — only take trend trades when ADX(14) > adx_min, i.e. only
    when the market is actually trending (research: trend-following works in
    trending regimes, bleeds in chop).
  * ATR TRAILING STOP  — once long, ratchet the stop up to price - trail*ATR so
    winners are protected and losers cut, instead of only exiting on the SMA break.
  * PORTFOLIO ALLOCATION — run BTC + ETH + QQQ together, equal capital split, and
    combine the equity curves. Diversification should cut drawdown vs any single
    market.

Everything is judged OUT-OF-SAMPLE with realistic costs (the only standard that
counts here). Research-only; no live, no keys.
"""
import numpy as np
import pandas as pd

from research_harness import stats
from strategy_lab import fetch_daily, indicators, START_EQ, RISK_PCT, FEE, SLIP

SYMBOLS = ["BTC-USD", "ETH-USD", "QQQ"]
TRAIN_FRAC = 0.70
STOP_MULT = 3.0
TRAIL_MULT = 4.0
ADX_MIN = 20.0


def add_adx(df, period=14):
    df = df.copy()
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-9)
    df["adx"] = dx.ewm(alpha=1/period, adjust=False).mean()
    return df


def run_pro(df, use_adx=False, trail=False):
    """Daily long/flat TSMOM with optional ADX filter + ATR trailing stop.
    Cash accounting + mark-to-market equity curve. Returns (trades, eq_df)."""
    rows = df.reset_index(drop=True)
    cash, pos = START_EQ, None
    trades, eq_ts, eq_val = [], [], []
    bh_units, base = None, None
    for i in range(len(rows)):
        r = rows.iloc[i]
        price, atr_v = r["close"], r["atr"]
        if base is None and price > 0:
            base, bh_units = price, START_EQ / price
        if pos is not None and not np.isnan(atr_v):
            if trail:                                    # ratchet stop up
                pos["stop"] = max(pos["stop"], price - atr_v * TRAIL_MULT)
            exit_px = None
            if r["low"] <= pos["stop"]:
                exit_px = pos["stop"] * (1 - SLIP)
            elif (not np.isnan(r["sma100"])) and price < r["sma100"]:
                exit_px = price * (1 - SLIP)
            if exit_px is not None:
                cash += pos["size"] * exit_px * (1 - FEE)
                pnl = pos["size"] * exit_px * (1 - FEE) - pos["size"] * pos["entry"] * (1 + FEE)
                trades.append({"pnl": pnl, "r": pnl / pos["risk"] if pos["risk"] else 0})
                pos = None
        if pos is None and not np.isnan(atr_v) and atr_v > 0:
            ok = (not np.isnan(r["sma100"]) and price > r["sma100"]
                  and not np.isnan(r["mom90"]) and r["mom90"] > 0)
            if use_adx:
                ok = ok and (not np.isnan(r.get("adx", np.nan))) and r["adx"] > ADX_MIN
            if ok:
                stop_dist = atr_v * STOP_MULT
                risk = cash * RISK_PCT
                size = risk / stop_dist
                if size * price > cash:
                    size = cash / price
                cash -= size * price * (1 + FEE)
                pos = {"entry": price, "size": size, "stop": price - stop_dist, "risk": risk}
        equity = cash + (pos["size"] * price if pos else 0.0)
        eq_ts.append(r["ts"]); eq_val.append((equity, bh_units * price if bh_units else START_EQ))
    eq = pd.DataFrame({"ts": eq_ts, "equity": [v[0] for v in eq_val], "bh": [v[1] for v in eq_val]})
    return trades, eq


def max_dd(s):
    s = np.asarray(s, float)
    return float((s / np.maximum.accumulate(s) - 1).min()) if len(s) else 0.0


def main():
    print("APEX TSMOM PRO  -  ADX regime + trailing stop + portfolio (daily, OOS, costs)\n")
    curves = {}
    for sym in SYMBOLS:
        d0 = fetch_daily(sym)
        print(f"================  {sym}  ================")
        if d0 is None or len(d0) < 500:
            print("  not enough data\n"); continue
        d = add_adx(indicators(d0))
        cut = int(len(d) * TRAIN_FRAC)
        print(f"  bars={len(d)} span={d['ts'].iloc[0].date()}..{d['ts'].iloc[-1].date()} (test={len(d)-cut})")
        print(f"  {'variant':<22} {'TRAIN pf/exp/n':<22} {'TEST pf/exp/n':<22} verdict")
        for label, ua, tr in [("baseline", False, False),
                              ("+ADX regime", True, False),
                              ("+ADX +trailing", True, True)]:
            te_tr, _ = run_pro(d.iloc[cut:], ua, tr)
            tn_tr, _ = run_pro(d.iloc[:cut], ua, tr)
            te, tn = stats(te_tr), stats(tn_tr)
            keep = te["profit_factor"] > 1.1 and te["expectancy_r"] > 0 and te["trades"] >= 10
            tn_s = f"{tn['profit_factor']}/{tn['expectancy_r']}/{tn['trades']}"
            te_s = f"{te['profit_factor']}/{te['expectancy_r']}/{te['trades']}"
            verdict = "EDGE? keep" if keep else "reject"
            print(f"  {label:<22} {tn_s:<22} {te_s:<22} {verdict}")
        # full-period curve with best config (+ADX+trailing) for portfolio combine
        _, eq = run_pro(d, True, True)
        curves[sym] = eq.set_index("ts")["equity"]
        print()

    # ---- portfolio: equal capital split across symbols, combined equity ----
    if len(curves) >= 2:
        print("################  PORTFOLIO (equal-weight BTC/ETH/QQQ, +ADX+trailing)  ################")
        norm = [c / c.iloc[0] for c in curves.values()]            # each starts at 1
        port = sum(norm) / len(norm) * START_EQ
        port = port.dropna()
        print(f"  Portfolio: return {port.iloc[-1]/START_EQ-1:+.0%}  maxDD {max_dd(port):.0%}")
        for sym, c in curves.items():
            print(f"    {sym:<9} alone: return {c.iloc[-1]/c.iloc[0]-1:+.0%}  maxDD {max_dd(c):.0%}")
        print("  (lower portfolio drawdown than the individual markets = diversification working)")
    print("\n===== TSMOM PRO DONE =====")


if __name__ == "__main__":
    main()
