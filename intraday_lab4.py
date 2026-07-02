"""APEX LAB v4 - PO3 (Power of Three / AMD) mechanical codification.
Accumulation = opening range (30/60m). Manipulation = push beyond one side
that closes back inside (Judas swing), before 12:00 ET. Distribution = enter
opposite the manipulation, stop pad*ATR beyond its extreme, 2R target, EOD flat.
Bias variant: only take trades in the direction of the PRIOR day's move
(manipulation runs against the trend, distribution with it - ICT's daily bias).
Pooled across 6 index products, fixed params, 70/30 OOS. PAPER RESEARCH ONLY.
"""
from datetime import time as T
import intraday_lab2 as L2

SYMS = {"NQ=F": 0.0001, "ES=F": 0.0001, "YM=F": 0.0001, "RTY=F": 0.0001,
        "QQQ": 0.0002, "SPY": 0.0002}
out = open("/app/intraday_lab4.txt", "w")


def log(s=""):
    print(s, flush=True)
    out.write(str(s) + "\n")
    out.flush()


def s_po3(g, prev_dir, cost, acc_min=30, pad=0.25, bias=False, tgt_r=2.0):
    cut = T(9 + (30 + acc_min) // 60, (30 + acc_min) % 60)
    acc = g[g["t"] < cut]
    rest = g[(g["t"] >= cut) & (g["t"] < T(12, 0))]
    if len(acc) < acc_min // 5 or len(rest) < 6:
        return None
    hi, lo = float(acc["high"].max()), float(acc["low"].min())
    atr = float((g["high"] - g["low"]).mean())
    if atr <= 0 or hi <= lo:
        return None
    for _, b in rest.iterrows():
        d = 0
        if b["high"] > hi and b["close"] < hi:
            d, ext = -1, float(b["high"])
        elif b["low"] < lo and b["close"] > lo:
            d, ext = 1, float(b["low"])
        if d:
            if bias and (prev_dir is None or d != prev_dir):
                return None
            entry = float(b["close"])
            stop = ext - d * pad * atr
            risk = abs(entry - stop)
            if risk <= 0:
                return None
            target = entry + d * tgt_r * risk
            after = g[g["ts"] > b["ts"]]
            exit_ = L2.sim(after, d, entry, stop, target)
            return L2.r_val(entry, exit_, stop, d, cost)
    return None


def main():
    import pandas as pd
    log("APEX LAB v4 - PO3/AMD pooled verdict (fixed params, 6 symbols, 70/30 OOS)")
    log(f"run: {pd.Timestamp.now()}")
    prep = {}
    for s in SYMS:
        df = L2.fetch(s)
        if df is None or df["date"].nunique() < 30:
            log(f"  {s}: insufficient data - skipped")
            continue
        days = sorted(df["date"].unique())
        cut = int(len(days) * 0.7)
        pdirm, prev_o, prev_c = {}, None, None
        for dt in days:
            g = df[df["date"] == dt]
            if prev_o is not None and prev_c is not None and prev_c != prev_o:
                pdirm[dt] = 1 if prev_c > prev_o else -1
            else:
                pdirm[dt] = None
            if len(g):
                prev_o = float(g.iloc[0]["open"])
                prev_c = float(g.iloc[-1]["close"])
        prep[s] = (df, days[:cut], days[cut:], pdirm)
    variants = {
        "PO3 acc=30 raw ": dict(acc_min=30, bias=False),
        "PO3 acc=30 bias": dict(acc_min=30, bias=True),
        "PO3 acc=60 raw ": dict(acc_min=60, bias=False),
        "PO3 acc=60 bias": dict(acc_min=60, bias=True),
    }
    for name, kw in variants.items():
        tr, te, per = [], [], []
        for s, (df, trd, ted, pdirm) in prep.items():
            c = SYMS[s]
            a = [s_po3(df[df["date"] == dt], pdirm.get(dt), c, **kw) for dt in trd]
            b = [s_po3(df[df["date"] == dt], pdirm.get(dt), c, **kw) for dt in ted]
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
