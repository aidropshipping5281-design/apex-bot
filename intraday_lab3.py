"""APEX INTRADAY LAB v3 - POOLED 100+ TRADE VERDICT.
Same FVG + IM logic (imported from lab v1/v2), FIXED params, pooled across
6 index products so the sample is honest (no per-symbol param picking).
"""
import intraday_lab as L1
import intraday_lab2 as L2

SYMS = {"NQ=F": 0.0001, "ES=F": 0.0001, "YM=F": 0.0001, "RTY=F": 0.0001,
        "QQQ": 0.0002, "SPY": 0.0002}
out = open("/app/intraday_lab3.txt", "w")


def log(s=""):
    print(s, flush=True)
    out.write(str(s) + "\n")
    out.flush()


def main():
    log("APEX LAB v3 - POOLED VERDICT (fixed params, 6 symbols, 70/30 OOS)")
    import pandas as pd
    log(f"run: {pd.Timestamp.now()}")
    prep = {}
    for s in SYMS:
        df = L2.fetch(s)
        if df is None or df["date"].nunique() < 30:
            log(f"  {s}: insufficient data - skipped")
            continue
        days = sorted(df["date"].unique())
        cut = int(len(days) * 0.7)
        m, prev, pc, pprev = {}, None, {}, None
        for dt in days:
            g = df[df["date"] == dt]
            m[dt] = prev
            pc[dt] = pprev
            if len(g):
                prev = dict(ph=float(g["high"].max()), pl=float(g["low"].min()))
                pprev = float(g.iloc[-1]["close"])
        prep[s] = (df, days[:cut], days[cut:], m, pc)
    tests = {
        "FVG disp=1.2": lambda g, inf, pcl, c: L2.s_fvg(g, inf, c, disp=1.2),
        "FVG disp=1.5": lambda g, inf, pcl, c: L2.s_fvg(g, inf, c, disp=1.5),
        "FVG disp=1.8": lambda g, inf, pcl, c: L2.s_fvg(g, inf, c, disp=1.8),
        "IM  thr=0.0 ": lambda g, inf, pcl, c: L1.s_im(g, pcl, c, thr=0.0),
    }
    for name, fn in tests.items():
        tr, te, per = [], [], []
        for s, (df, trd, ted, m, pc) in prep.items():
            c = SYMS[s]
            a = [fn(df[df["date"] == dt], m.get(dt), pc.get(dt), c) for dt in trd]
            b = [fn(df[df["date"] == dt], m.get(dt), pc.get(dt), c) for dt in ted]
            tr += [x for x in a if x is not None]
            te += [x for x in b if x is not None]
            per.append(f"    {s:<6} TEST {L2.fmt(L2.stats(b))}")
        log(f"\n== {name}")
        log(f"  TRAIN pooled {L2.fmt(L2.stats(tr))}")
        log(f"  TEST  pooled {L2.fmt(L2.stats(te))}")
        for p in per:
            log(p)
        log(f"  ALL   pooled {L2.fmt(L2.stats(tr + te))}")
    log("\nVerdict rule: pooled ALL n>=100 AND pooled TEST exp>0 & PF>1.1.")


if __name__ == "__main__":
    main()
