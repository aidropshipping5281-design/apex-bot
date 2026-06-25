"""Daily trend-following (TSMOM) — multi-fold walk-forward + drawdown stress tests.

WHY
  strategy_lab found DAILY TSMOM survives realistic costs out-of-sample. Two things
  must still be proven before trusting it:
    1. WALK-FORWARD: not one lucky split. Score the rule on K consecutive
       out-of-sample windows (the last fold runs right up to the present).
    2. DRAWDOWN PROTECTION — but tested on the RIGHT periods, not a hardcoded year:
         (a) the WORST drawdown in the whole history (auto-detected, wherever it
             happened), and
         (b) the LAST 12 MONTHS (the most decision-relevant window today, 2026).
       In each, compare the strategy's return + max drawdown vs BUY & HOLD. If the
       strategy's drawdown is much smaller, the "go flat below the trend" rule is
       doing real work — recently, not just in 2022.

  Daily turnover is tiny, so realistic costs (0.10% fee + 0.05% slip/side) barely
  bite — but they are modeled anyway. Research-only. No live, no keys.
"""
import numpy as np
import pandas as pd

from research_harness import stats
from strategy_lab import indicators, fetch_daily, START_EQ, RISK_PCT, FEE, SLIP

SYMBOLS = ["NVDA", "AMD", "QQQ", "NQ=F", "BTC-USD", "ETH-USD"]   # validated set + crypto
N_FOLDS = 5
STOP_MULT = 3.0
RECENT_DAYS = 252        # ~ last 12 months of trading days


def run_tsmom(df):
    """Long/flat daily TSMOM with cash accounting + mark-to-market equity curve.
    Returns (trades, equity_df[ts,equity,bh]) where bh = buy&hold of same capital."""
    rows = df.reset_index(drop=True)
    cash = START_EQ
    pos = None
    trades = []
    eq_ts, eq_val = [], []
    bh_units = None
    base_price = None
    for i in range(len(rows)):
        r = rows.iloc[i]
        price, atr_v = r["close"], r["atr"]
        if base_price is None and not np.isnan(price) and price > 0:
            base_price = price
            bh_units = START_EQ / price
        if pos is not None and not np.isnan(atr_v):
            exit_px = None
            if r["low"] <= pos["stop"]:
                exit_px = pos["stop"] * (1 - SLIP)
            elif (not np.isnan(r["sma100"])) and price < r["sma100"]:
                exit_px = price * (1 - SLIP)
            if exit_px is not None:
                cash += pos["size"] * exit_px * (1 - FEE)
                pnl = (pos["size"] * exit_px * (1 - FEE)) - (pos["size"] * pos["entry"] * (1 + FEE))
                trades.append({"pnl": pnl, "r": pnl / pos["risk"] if pos["risk"] else 0})
                pos = None
        if pos is None and not np.isnan(atr_v) and atr_v > 0:
            enter = (not np.isnan(r["sma100"]) and price > r["sma100"]
                     and not np.isnan(r["mom90"]) and r["mom90"] > 0)
            if enter:
                stop_dist = atr_v * STOP_MULT
                risk_dollars = cash * RISK_PCT
                size = risk_dollars / stop_dist
                if size * price > cash:
                    size = cash / price
                cash -= size * price * (1 + FEE)
                pos = {"entry": price, "size": size,
                       "stop": price - stop_dist, "risk": risk_dollars}
        equity = cash + (pos["size"] * price if pos else 0.0)
        bh = bh_units * price if bh_units else START_EQ
        eq_ts.append(r["ts"]); eq_val.append((equity, bh))
    eq = pd.DataFrame({"ts": eq_ts, "equity": [v[0] for v in eq_val],
                       "bh": [v[1] for v in eq_val]})
    return trades, eq


def max_dd(series):
    s = np.asarray(series, dtype=float)
    if len(s) == 0:
        return 0.0
    return float((s / np.maximum.accumulate(s) - 1.0).min())


def worst_dd_window(bh):
    """Indices (peak, trough) of the largest buy&hold peak-to-trough drawdown."""
    b = np.asarray(bh, dtype=float)
    if len(b) == 0:
        return 0, 0
    peak = np.maximum.accumulate(b)
    trough = int((b / peak - 1.0).argmin())
    peak_idx = int(b[:trough + 1].argmax())
    return peak_idx, trough


def fold_bounds(n, k):
    e = np.linspace(0, n, k + 1, dtype=int)
    return [(e[j], e[j + 1]) for j in range(k)]


def report_window(label, win):
    if len(win) < 5:
        return
    t_ret = win["equity"].iloc[-1] / win["equity"].iloc[0] - 1
    b_ret = win["bh"].iloc[-1] / win["bh"].iloc[0] - 1
    span = f"{win['ts'].iloc[0].date()}..{win['ts'].iloc[-1].date()}"
    print(f"  --- {label} ({span}) ---")
    print(f"    TSMOM:    {t_ret:+.0%}  maxDD {max_dd(win['equity']):.0%}")
    print(f"    Buy&Hold: {b_ret:+.0%}  maxDD {max_dd(win['bh']):.0%}")
    protects = max_dd(win["equity"]) > max_dd(win["bh"]) + 0.05
    print(f"    => {'PROTECTS' if protects else 'similar/weak'} vs buy & hold")


def main():
    print("APEX DAILY-TSMOM  -  walk-forward + drawdown stress (worst-ever + last 12mo)")
    print(f"{N_FOLDS} folds, realistic costs (fee {FEE:.2%}+slip {SLIP:.2%}/side), long/flat.\n")
    for sym in SYMBOLS:
        d0 = fetch_daily(sym)
        print(f"================  {sym}  ================")
        if d0 is None or len(d0) < 500:
            print("  not enough data\n"); continue
        d = indicators(d0)
        print(f"  bars={len(d)}  span={d['ts'].iloc[0].date()}..{d['ts'].iloc[-1].date()}")

        # ---- walk-forward (last fold reaches the present) ----
        print(f"  --- walk-forward ({N_FOLDS} folds) ---")
        folds = fold_bounds(len(d), N_FOLDS)
        pos_folds = 0
        for (a, b) in folds:
            seg = d.iloc[a:b]
            s = stats(run_tsmom(seg)[0])
            if s["net"] > 0:
                pos_folds += 1
            span = f"{seg['ts'].iloc[0].date()}..{seg['ts'].iloc[-1].date()}"
            print(f"    {span:<24} PF {s['profit_factor']:<6} exp {s['expectancy_r']:<7} "
                  f"n {s['trades']:<4} net ${s['net']}")
        print(f"    => positive in {pos_folds}/{N_FOLDS} folds  (last fold = most recent)")

        # ---- full period ----
        tr_all, eq = run_tsmom(d)
        s_all = stats(tr_all)
        print(f"  --- full period ---")
        print(f"    TSMOM:    {eq['equity'].iloc[-1]/START_EQ-1:+.0%}  maxDD {max_dd(eq['equity']):.0%}"
              f"  (PF {s_all['profit_factor']}, {s_all['trades']} trades)")
        print(f"    Buy&Hold: {eq['bh'].iloc[-1]/START_EQ-1:+.0%}  maxDD {max_dd(eq['bh']):.0%}")

        # ---- stress on the RIGHT periods (not a hardcoded year) ----
        p, t = worst_dd_window(eq["bh"].values)
        report_window("WORST DRAWDOWN (auto-detected)", eq.iloc[p:t + 1])
        report_window("LAST 12 MONTHS", eq.iloc[-RECENT_DAYS:])
        print(flush=True)
    print("===== TSMOM WALK-FORWARD + STRESS DONE =====")
    print("Trust a name if: positive in a majority of folds (incl. the recent one)")
    print("AND it protects capital in the worst drawdown AND the last 12 months.")


if __name__ == "__main__":
    main()
