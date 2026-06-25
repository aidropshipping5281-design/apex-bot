"""Systematic backtest of PO3 + Fibonacci (ICT 'Power of Three' + golden pocket).

Objective codification (5m, regular hours):
  ACCUMULATION = the opening range (first OR_BARS bars) high/low.
  MANIPULATION = a bar sweeps the OR high (->short bias) or OR low (->long bias)
                 and closes back inside (a stop-run / false move).
  REVERSAL LEG = the impulse off the sweep (sweep extreme -> first swing).
  FIB ENTRY    = price retraces to the 0.618 "golden pocket" of that impulse,
                 entering in the reversal direction.
  STOP   = beyond the sweep extreme.  TARGET = the opposite side of the OR
           (the opposing liquidity, classic PO3 distribution).
  Intraday only, realistic costs. ~60 days of free 5m data (recent regime).

This is a discretionary method made objective; if it fails it means the RULES have
no edge (consistent with the TJR result + the academic verdict on fib levels).
Research-only; no live, no keys.
"""
import numpy as np
from tjr_backtest import fetch_5m, FEE, SLIP, stats, show, PAIRS

OR_BARS = 6        # opening range = first 30 min (accumulation)
IMPULSE_BARS = 8   # window to find the reversal impulse after the sweep
RETRACE_BARS = 10  # window for price to pull back into the golden pocket
FIB = 0.618        # golden-pocket entry


def sim_long(high, low, n, fill, entry, stop, target):
    risk = entry - stop
    if risk <= 0:
        return None
    ef = entry * (1 + SLIP)
    for k in range(fill + 1, n):
        if low[k] <= stop:
            ex = stop * (1 - SLIP)
            return ((ex - ef) - FEE * (ef + ex)) / risk
        if high[k] >= target:
            return ((target - ef) - FEE * (ef + target)) / risk
    return None  # no resolution in session -> skip


def sim_short(high, low, n, fill, entry, stop, target):
    risk = stop - entry
    if risk <= 0:
        return None
    ef = entry * (1 - SLIP)
    for k in range(fill + 1, n):
        if high[k] >= stop:
            ex = stop * (1 + SLIP)
            return ((ef - ex) - FEE * (ef + ex)) / risk
        if low[k] <= target:
            return ((ef - target) - FEE * (ef + target)) / risk
    return None


def day_trades(g):
    g = g.reset_index(drop=True)
    n = len(g)
    if n < OR_BARS + 12:
        return []
    high, low, close = g["high"].values, g["low"].values, g["close"].values
    orh = high[:OR_BARS].max(); orl = low[:OR_BARS].min()
    out, i = [], OR_BARS
    while i < n - 2:
        if low[i] < orl and close[i] > orl:           # sell-side sweep -> long
            sweep = low[i]
            he = min(i + 1 + IMPULSE_BARS, n)
            seg = high[i + 1:he]
            if len(seg) == 0:
                break
            imp_high = seg.max(); imp_idx = i + 1 + int(seg.argmax())
            entry = imp_high - FIB * (imp_high - sweep)
            fill = next((j for j in range(imp_idx + 1, min(imp_idx + 1 + RETRACE_BARS, n))
                         if low[j] <= entry), None)
            if fill is not None:
                r = sim_long(high, low, n, fill, entry, sweep, orh)
                if r is not None:
                    out.append(r)
            i = imp_idx + 1
        elif high[i] > orh and close[i] < orh:        # buy-side sweep -> short
            sweep = high[i]
            he = min(i + 1 + IMPULSE_BARS, n)
            seg = low[i + 1:he]
            if len(seg) == 0:
                break
            imp_low = seg.min(); imp_idx = i + 1 + int(seg.argmin())
            entry = imp_low + FIB * (sweep - imp_low)
            fill = next((j for j in range(imp_idx + 1, min(imp_idx + 1 + RETRACE_BARS, n))
                         if high[j] >= entry), None)
            if fill is not None:
                r = sim_short(high, low, n, fill, entry, sweep, orl)
                if r is not None:
                    out.append(r)
            i = imp_idx + 1
        else:
            i += 1
    return out


def run(df):
    rs = []
    for _, g in df.groupby("day"):
        rs += day_trades(g)
    return rs


def main():
    print("APEX — SYSTEMATIC PO3 + FIBONACCI BACKTEST (5m RTH, costs)")
    print(f"OR={OR_BARS} bars, golden-pocket entry {FIB}, stop=sweep, target=opposite OR.")
    print("Compare to a real edge: positive expectancy after costs. (ICT-family; expect weak.)\n")
    for a, b in PAIRS:
        for sym in (a, b):
            d = fetch_5m(sym)
            if d is None or len(d) < 200:
                print(f"  {sym:<9} no data"); continue
            show(f"{sym} PO3+Fib", run(d))
        print()
    print("===== PO3 + FIB DONE =====")
    print("Edge only if expectancy clearly > 0 with a real sample. Otherwise it's")
    print("confirmation bias, same as the TJR sweep test and the fib-level literature.")


if __name__ == "__main__":
    main()
