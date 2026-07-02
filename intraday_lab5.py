"""APEX LAB v5 - FIBONACCI OTE + PO3 combo.
F1 OTE : displacement bar (>disp*dayATR) defines a leg; enter on retrace to the
         62% fib of the leg (ICT optimal-trade-entry zone), direction of the
         leg; stop just beyond the leg origin; 2R target; EOD flat.
F2 PO3F: same OTE entry, but ONLY after a PO3 manipulation - an opening-range
         (30m) Judas sweep that closed back inside - and the leg must run
         OPPOSITE the sweep (= the distribution phase). Fib entry into PO3.
Continuation family (like the FVG that passed), NOT a manipulation fade.
Pooled 6 symbols, fixed params, 70/30 OOS. PAPER RESEARCH ONLY.
"""
from datetime import time as T
import intraday_lab2 as L2

SYMS = {"NQ=F": 0.0001, "ES=F": 0.0001, "YM=F": 0.0001, "RTY=F": 0.0001,
        "QQQ": 0.0002, "SPY": 0.0002}
out = open("/app/intraday_lab5.txt", "w")


def log(s=""):
    print(s, flush=True)
    out.write(str(s) + "\n")
    out.flush()


def sweep_side(g):
    """PO3 manipulation: first opening-range (30m) sweep that closes back
    inside, before 12:00. Returns +1 (swept above), -1 (swept below), 0."""
    acc = g[g["t"] < T(10, 0)]
    zone = g[(g["t"] >= T(10, 0)) & (g["t"] < T(12, 0))]
    if len(acc) < 4 or len(zone) < 4:
        return 0, None
    hi, lo = float(acc["high"].max()), float(acc["low"].min())
    for _, b in zone.iterrows():
        if b["high"] > hi and b["close"] < hi:
            return 1, b["ts"]
        if b["low"] < lo and b["close"] > lo:
            return -1, b["ts"]
    return 0, None


def s_ote(g, cost, disp=1.8, po3=False):
    atr = float((g["high"] - g["low"]).mean())
    if atr <= 0:
        return None
    swp, swp_ts = (0, None)
    if po3:
        swp, swp_ts = sweep_side(g)
        if swp == 0:
            return None
    gg = g.reset_index(drop=True)
    for j in range(1, len(gg)):
        b1, b2 = gg.iloc[j - 1], gg.iloc[j]
        if not (T(10, 0) <= b2["t"] < T(14, 30)):
            continue
        if float(b2["high"] - b2["low"]) < disp * atr:
            continue
        d = 1 if b2["close"] > b2["open"] else -1
        if po3:
            if b2["ts"] <= swp_ts or d != -swp:   # distribution runs opposite sweep
                continue
        leg_hi = float(max(b1["high"], b2["high"]))
        leg_lo = float(min(b1["low"], b2["low"]))
        leg = leg_hi - leg_lo
        if leg <= 0:
            continue
        entry = leg_hi - 0.62 * leg if d > 0 else leg_lo + 0.62 * leg
        stop = (leg_lo - 0.1 * atr) if d > 0 else (leg_hi + 0.1 * atr)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        target = entry + d * 2.0 * risk
        after = gg.iloc[j + 1:]
        for k, (_, a) in enumerate(after.iterrows()):
            if a["t"] >= L2.FLAT:
                return None
            touched = (d > 0 and a["low"] <= entry) or (d < 0 and a["high"] >= entry)
            if touched:
                exit_ = L2.sim(after.iloc[k:], d, entry, stop, target)
                return L2.r_val(entry, exit_, stop, d, cost)
        return None
    return None


def main():
    import pandas as pd
    log("APEX LAB v5 - FIB OTE + PO3 combo (fixed params, 6 symbols, 70/30 OOS)")
    log(f"run: {pd.Timestamp.now()}")
    prep = {}
    for s in SYMS:
        df = L2.fetch(s)
        if df is None or df["date"].nunique() < 30:
            log(f"  {s}: insufficient data - skipped")
            continue
        days = sorted(df["date"].unique())
        cut = int(len(days) * 0.7)
        prep[s] = (df, days[:cut], days[cut:])
    variants = {
        "F1 OTE  disp=1.5": dict(disp=1.5, po3=False),
        "F1 OTE  disp=1.8": dict(disp=1.8, po3=False),
        "F2 PO3F disp=1.5": dict(disp=1.5, po3=True),
        "F2 PO3F disp=1.8": dict(disp=1.8, po3=True),
    }
    for name, kw in variants.items():
        tr, te, per = [], [], []
        for s, (df, trd, ted) in prep.items():
            c = SYMS[s]
            a = [s_ote(df[df["date"] == dt], c, **kw) for dt in trd]
            b = [s_ote(df[df["date"] == dt], c, **kw) for dt in ted]
            tr += [x for x in a if x is not None]
            te += [x for x in b if x is not None]
            per.append(f"    {s:<6} TEST {L2.fmt(L2.stats(b))}")
        log(f"\n== {name}")
        log(f"  TRAIN pooled {L2.fmt(L2.stats(tr))}")
        log(f"  TEST  pooled {L2.fmt(L2.stats(te))}")
        for p in per:
            log(p)
        log(f"  ALL   pooled {L2.fmt(L2.stats(tr + te))}")
    log("\nVerdict rule: pooled ALL n>=100 AND TRAIN & TEST both exp>0 & TEST PF>1.1.")


if __name__ == "__main__":
    main()
