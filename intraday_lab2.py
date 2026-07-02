"""APEX INTRADAY LAB v2 — ICT/SMC mechanical concepts on futures (NQ/ES).

Lessons from the failed TJR codification baked in: ATR-padded stops (not sweep
wicks), ES/NQ alignment filter as a variant, session windows, EOD flat.
5m RTH, costs on, 70/30 OOS by day. R accounting. PAPER RESEARCH ONLY.

  I1 SWEEP  - liquidity sweep of PRIOR-DAY high/low + close back inside -> fade,
              stop = sweep extreme +/- pad*ATR, target 2R, EOD flat
  I2 FVG    - displacement bar (> disp*ATR) leaves a fair value gap; enter on
              first retrace into the gap, direction of displacement, 2R target
  I3 SWEEPA - I1 gated by ES/NQ alignment (partner's first-hour direction must
              agree with the trade)
"""
import os
from datetime import time as T
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "intraday_lab2.txt")
SYMS = {"NQ=F": 0.0001, "ES=F": 0.0001}
PARTNER = {"NQ=F": "ES=F", "ES=F": "NQ=F"}
RTH_O, RTH_C, FLAT = T(9, 30), T(16, 0), T(15, 55)

_out = open(OUT, "w")


def log(s=""):
    print(s, flush=True)
    _out.write(str(s) + "\n")
    _out.flush()


def fetch(sym):
    import yfinance as yf
    raw = yf.download(sym, interval="5m", period="60d", auto_adjust=False,
                      progress=False, threads=False, prepost=False)
    if raw is None or len(raw) == 0:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [str(c[0]).lower() for c in raw.columns]
    else:
        raw.columns = [str(c).lower() for c in raw.columns]
    df = raw.reset_index()
    df = df.rename(columns={df.columns[0]: "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
    df["date"] = df["ts"].dt.date
    df["t"] = df["ts"].dt.time
    df = df[(df["t"] >= RTH_O) & (df["t"] < RTH_C)].reset_index(drop=True)
    return df


def sim(bars, d, entry, stop, target=None):
    for _, b in bars.iterrows():
        if d > 0 and b["low"] <= stop:
            return stop
        if d < 0 and b["high"] >= stop:
            return stop
        if target is not None:
            if d > 0 and b["high"] >= target:
                return target
            if d < 0 and b["low"] <= target:
                return target
        if b["t"] >= FLAT:
            return float(b["close"])
    return float(bars.iloc[-1]["close"]) if len(bars) else None


def r_val(entry, exit_, stop, d, cost):
    risk = abs(entry - stop)
    if risk <= 0 or exit_ is None:
        return None
    return (exit_ - entry) / risk * d - (entry + exit_) * cost / risk


def s_sweep(g, info, cost, pad=0.5, end_h=12, align=None, need_align=False):
    if info is None:
        return None
    ph, pl = info["ph"], info["pl"]
    atr = float((g["high"] - g["low"]).mean())
    if atr <= 0:
        return None
    zone = g[(g["t"] >= T(9, 35)) & (g["t"] < T(end_h, 0))]
    for _, b in zone.iterrows():
        d = 0
        if b["high"] > ph and b["close"] < ph:
            d, ext = -1, float(b["high"])
        elif b["low"] < pl and b["close"] > pl:
            d, ext = 1, float(b["low"])
        if d:
            if need_align and align != d:
                return None
            entry = float(b["close"])
            stop = ext - d * pad * atr      # beyond the sweep extreme
            risk = abs(entry - stop)
            if risk <= 0:
                return None
            target = entry + d * 2.0 * risk
            after = g[g["ts"] > b["ts"]]
            exit_ = sim(after, d, entry, stop, target)
            return r_val(entry, exit_, stop, d, cost)
    return None


def s_fvg(g, info, cost, disp=1.5):
    atr = float((g["high"] - g["low"]).mean())
    if atr <= 0:
        return None
    gg = g.reset_index(drop=True)
    for j in range(2, len(gg)):
        b1, b2, b3 = gg.iloc[j - 2], gg.iloc[j - 1], gg.iloc[j]
        if not (T(10, 0) <= b3["t"] < T(14, 30)):
            continue
        if float(b2["high"] - b2["low"]) < disp * atr:
            continue
        if b2["close"] > b2["open"] and b1["high"] < b3["low"]:
            d, top, bot = 1, float(b3["low"]), float(b1["high"])
        elif b2["close"] < b2["open"] and b1["low"] > b3["high"]:
            d, top, bot = -1, float(b1["low"]), float(b3["high"])
        else:
            continue
        after = gg.iloc[j + 1:]
        for k, (_, a) in enumerate(after.iterrows()):
            if a["t"] >= FLAT:
                return None
            hit = (d > 0 and a["low"] <= top) or (d < 0 and a["high"] >= bot)
            if hit:
                entry = top if d > 0 else bot
                stop = (bot - 0.25 * atr) if d > 0 else (top + 0.25 * atr)
                risk = abs(entry - stop)
                if risk <= 0:
                    return None
                target = entry + d * 2.0 * risk
                exit_ = sim(after.iloc[k:], d, entry, stop, target)
                return r_val(entry, exit_, stop, d, cost)
        return None
    return None


def stats(rs):
    rs = [r for r in rs if r is not None]
    n = len(rs)
    if n == 0:
        return dict(n=0, exp=0.0, pf=0.0, win=0.0, tot=0.0)
    a = np.array(rs)
    pos, neg = a[a > 0].sum(), -a[a <= 0].sum()
    return dict(n=n, exp=float(a.mean()),
                pf=float(pos / neg) if neg > 0 else 99.0,
                win=float((a > 0).mean()), tot=float(a.sum()))


def fmt(s):
    return (f"n={s['n']:>3}  win={s['win']*100:4.0f}%  PF={min(s['pf'],99):5.2f}  "
            f"exp={s['exp']:+.3f}R  tot={s['tot']:+.1f}R")


def main():
    log("APEX INTRADAY LAB v2 - ICT/SMC on futures, 5m RTH, costs on, 70/30 OOS")
    log(f"run: {pd.Timestamp.now()}")
    data = {s: fetch(s) for s in SYMS}
    # per-day prior high/low/close + partner first-hour direction
    info, pdir = {}, {}
    for s, df in data.items():
        if df is None:
            continue
        m, prev = {}, None
        for dt in sorted(df["date"].unique()):
            g = df[df["date"] == dt]
            m[dt] = prev
            if len(g):
                prev = dict(ph=float(g["high"].max()), pl=float(g["low"].min()),
                            pc=float(g.iloc[-1]["close"]))
        info[s] = m
        pm = {}
        for dt in sorted(df["date"].unique()):
            g = df[(df["date"] == dt) & (df["t"] < T(10, 30))]
            if len(g) >= 4:
                r = float(g.iloc[-1]["close"]) - float(g.iloc[0]["open"])
                pm[dt] = 1 if r > 0 else -1
        pdir[s] = pm
    STRATS = {
        "I1 SWEEP":  [dict(pad=0.25), dict(pad=0.5), dict(pad=0.5, end_h=13)],
        "I2 FVG":    [dict(disp=1.2), dict(disp=1.8)],
        "I3 SWEEPA": [dict(pad=0.25, need_align=True), dict(pad=0.5, need_align=True)],
    }
    for sym, cost in SYMS.items():
        df = data.get(sym)
        if df is None or df["date"].nunique() < 30:
            log(f"\n== {sym}: insufficient data - skipped")
            continue
        days = sorted(df["date"].unique())
        cut = int(len(days) * 0.7)
        tr_d, te_d = days[:cut], days[cut:]
        part = pdir.get(PARTNER[sym], {})
        log(f"\n== {sym}  days={len(days)} (train {len(tr_d)} / test {len(te_d)})")

        def run(dt, name, params):
            g = df[df["date"] == dt]
            if name == "I2 FVG":
                return s_fvg(g, info[sym].get(dt), cost, **params)
            return s_sweep(g, info[sym].get(dt), cost, align=part.get(dt), **params)

        for name, grid in STRATS.items():
            best, best_s = None, None
            for params in grid:
                st = stats([run(dt, name, params) for dt in tr_d])
                if st["n"] >= 10 and (best_s is None or st["exp"] > best_s["exp"]):
                    best, best_s = params, st
            if best is None:
                log(f"  {name:<9} - too few train trades, reject")
                continue
            st_t = stats([run(dt, name, best) for dt in te_d])
            verdict = ("LEAD" if st_t["n"] >= 8 and st_t["exp"] > 0 and st_t["pf"] > 1.1
                       else "reject")
            log(f"  {name:<9} {best}")
            log(f"      TRAIN {fmt(best_s)}")
            log(f"      TEST  {fmt(st_t)}   -> {verdict}")
    log("\nConservative fills, costs both sides, EOD flat. LEAD = validate deeper, not proof.")


if __name__ == "__main__":
    main()
