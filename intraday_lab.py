"""APEX INTRADAY LAB v1 — day-trading strategy screen (futures focus).

Tests 5 evidence-backed intraday strategies on 5m bars (last ~60 trading days,
RTH 09:30-16:00 ET only, ALWAYS flat by 15:55 — true day trading), with
conservative per-side costs, 70/30 train/test split by day.
R-based accounting: 1R = entry-to-stop distance. PAPER RESEARCH ONLY.

Strategies (see APEX research notes 2026-07-01):
  S1 ORB   — opening range breakout (Zarattini/Aziz 2023; TORB index-futures paper)
  S2 IM    — market intraday momentum: first 30m ret -> trade last 30m
             (Gao/Han/Li/Zhou JFE 2018; Baltussen 2021, 60+ futures)
  S3 VWAPR — VWAP band mean-reversion, lunch/afternoon, non-trend days
  S4 GAPF  — small overnight gap fade toward prior close (gap-fill stats)
  S5 TDC   — trend-day continuation: strong first hour -> ride with VWAP trail
"""
import os
from datetime import time as T
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "intraday_lab.txt")
SYMS = {"NQ=F": 0.0001, "ES=F": 0.0001, "QQQ": 0.0002}  # per-side cost (pct)
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


def add_vwap(g):
    tp = (g["high"] + g["low"] + g["close"]) / 3.0
    v = g["volume"].astype(float)
    if v.sum() <= 0:
        v = pd.Series(1.0, index=g.index)
    g = g.copy()
    g["vwap"] = (tp * v).cumsum() / v.cumsum()
    return g


def sim(bars, d, entry, stop, target=None, vwap_trail=False, min_hold=0):
    """Walk bars after entry. Conservative: stop checked before target each bar.
    Returns exit price."""
    for i, (_, b) in enumerate(bars.iterrows()):
        if d > 0 and b["low"] <= stop:
            return stop
        if d < 0 and b["high"] >= stop:
            return stop
        if target is not None:
            if d > 0 and b["high"] >= target:
                return target
            if d < 0 and b["low"] <= target:
                return target
        if vwap_trail and i >= min_hold:
            if d > 0 and b["close"] < b["vwap"]:
                return float(b["close"])
            if d < 0 and b["close"] > b["vwap"]:
                return float(b["close"])
        if b["t"] >= FLAT:
            return float(b["close"])
    return float(bars.iloc[-1]["close"]) if len(bars) else None


def r_val(entry, exit_, stop, d, cost):
    risk = abs(entry - stop)
    if risk <= 0 or exit_ is None:
        return None
    raw = (exit_ - entry) / risk * d
    fees = (entry + exit_) * cost / risk
    return raw - fees


# ---------------- strategies (return one R value or None per day) -----------

def s_orb(g, prev_close, cost, or_min=15, tgt_r=None):
    cut = T(9 + (30 + or_min) // 60, (30 + or_min) % 60)
    orr = g[g["t"] < cut]
    rest = g[g["t"] >= cut]
    if len(orr) < or_min // 5 or len(rest) < 12:
        return None
    hi, lo = float(orr["high"].max()), float(orr["low"].min())
    rng = hi - lo
    if rng <= 0:
        return None
    for i, (_, b) in enumerate(rest.iterrows()):
        if b["t"] >= FLAT:
            return None
        d = 1 if b["high"] > hi else (-1 if b["low"] < lo else 0)
        if d:
            entry = hi if d > 0 else lo
            stop = lo if d > 0 else hi
            tgt = entry + d * tgt_r * rng if tgt_r else None
            after = rest.iloc[i:]
            exit_ = sim(after, d, entry, stop, target=tgt)
            return r_val(entry, exit_, stop, d, cost)
    return None


def s_im(g, prev_close, cost, thr=0.0):
    if prev_close is None:
        return None
    early = g[g["t"] < T(10, 0)]
    late = g[g["t"] >= T(15, 30)]
    if len(early) < 4 or len(late) < 4:
        return None
    p10 = float(early.iloc[-1]["close"])
    ret = p10 / prev_close - 1.0
    if abs(ret) < thr:
        return None
    d = 1 if ret > 0 else -1
    entry = float(late.iloc[0]["open"])
    risk = float((g["high"] - g["low"]).mean()) * 3.0
    if risk <= 0:
        return None
    stop = entry - d * risk
    exit_ = sim(late.iloc[1:], d, entry, stop) if len(late) > 1 else float(late.iloc[-1]["close"])
    return r_val(entry, exit_, stop, d, cost)


def s_vwapr(g, prev_close, cost, band=2.0):
    g = add_vwap(g)
    open_ = float(g.iloc[0]["open"])
    pre11 = g[g["t"] < T(11, 0)]
    if len(pre11) < 12:
        return None
    r_to_11 = float(pre11.iloc[-1]["close"]) / open_ - 1.0
    if abs(r_to_11) > 0.005:          # skip trend days — reversion fails there
        return None
    dev = g["close"] - g["vwap"]
    sig = dev.expanding(12).std()
    zone = g[(g["t"] >= T(11, 0)) & (g["t"] < T(15, 0))]
    for i, (idx, b) in enumerate(zone.iterrows()):
        s = sig.loc[idx]
        if not np.isfinite(s) or s <= 0:
            continue
        dv = dev.loc[idx]
        d = 1 if dv < -band * s else (-1 if dv > band * s else 0)
        if d:
            entry = float(b["close"])
            stop = entry - d * float(s)
            after = g[g["ts"] > b["ts"]]
            # dynamic target = vwap touch
            exit_ = None
            for _, a in after.iterrows():
                if d > 0 and a["low"] <= stop:
                    exit_ = stop; break
                if d < 0 and a["high"] >= stop:
                    exit_ = stop; break
                if d > 0 and a["high"] >= a["vwap"]:
                    exit_ = float(a["vwap"]); break
                if d < 0 and a["low"] <= a["vwap"]:
                    exit_ = float(a["vwap"]); break
                if a["t"] >= FLAT:
                    exit_ = float(a["close"]); break
            if exit_ is None and len(after):
                exit_ = float(after.iloc[-1]["close"])
            return r_val(entry, exit_, stop, d, cost)
    return None


def s_gapf(g, prev_close, cost, max_gap=0.004):
    if prev_close is None:
        return None
    open_ = float(g.iloc[0]["open"])
    gap = open_ / prev_close - 1.0
    if not (0.0005 <= abs(gap) <= max_gap):
        return None
    d = -1 if gap > 0 else 1
    entry = open_
    target = prev_close
    stop = entry - d * abs(open_ - prev_close)   # risk 1x the gap distance
    exit_ = sim(g.iloc[1:], d, entry, stop, target=target)
    return r_val(entry, exit_, stop, d, cost)


def s_tdc(g, prev_close, cost, thr=0.0035):
    g = add_vwap(g)
    open_ = float(g.iloc[0]["open"])
    fh = g[g["t"] < T(10, 30)]
    rest = g[g["t"] >= T(10, 30)]
    if len(fh) < 8 or len(rest) < 12:
        return None
    r1 = float(fh.iloc[-1]["close"]) / open_ - 1.0
    if abs(r1) < thr:
        return None
    d = 1 if r1 > 0 else -1
    entry = float(rest.iloc[0]["open"])
    atr = float((fh["high"] - fh["low"]).mean())
    if atr <= 0:
        return None
    stop = entry - d * 3.0 * atr
    exit_ = sim(rest.iloc[1:], d, entry, stop, vwap_trail=True, min_hold=6)
    return r_val(entry, exit_, stop, d, cost)


STRATS = {
    "S1 ORB":   (s_orb,   [dict(or_min=5), dict(or_min=15), dict(or_min=30),
                           dict(or_min=15, tgt_r=2.0)]),
    "S2 IM":    (s_im,    [dict(thr=0.0), dict(thr=0.001)]),
    "S3 VWAPR": (s_vwapr, [dict(band=1.5), dict(band=2.0)]),
    "S4 GAPF":  (s_gapf,  [dict(max_gap=0.003), dict(max_gap=0.005)]),
    "S5 TDC":   (s_tdc,   [dict(thr=0.0025), dict(thr=0.005)]),
}


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
    log("APEX INTRADAY LAB v1 — 5m RTH, EOD-flat, costs on, 70/30 OOS by day")
    log(f"run: {pd.Timestamp.now()}  symbols: {list(SYMS)}")
    for sym, cost in SYMS.items():
        df = fetch(sym)
        if df is None or df["date"].nunique() < 30:
            log(f"\n== {sym}: insufficient data — skipped")
            continue
        days = sorted(df["date"].unique())
        cut = int(len(days) * 0.7)
        train_days, test_days = days[:cut], days[cut:]
        prev_close, pc_map = None, {}
        for dt in days:
            pc_map[dt] = prev_close
            g = df[df["date"] == dt]
            if len(g):
                prev_close = float(g.iloc[-1]["close"])
        log(f"\n== {sym}  days={len(days)} (train {len(train_days)} / test {len(test_days)})  "
            f"cost={cost*100:.2f}%/side")
        for name, (fn, grid) in STRATS.items():
            best, best_s = None, None
            for params in grid:
                rs = [fn(df[df["date"] == dt], pc_map[dt], cost, **params)
                      for dt in train_days]
                st = stats(rs)
                if st["n"] >= 10 and (best_s is None or st["exp"] > best_s["exp"]):
                    best, best_s = params, st
            if best is None:
                log(f"  {name:<9} — too few train trades, reject")
                continue
            rs_t = [fn(df[df["date"] == dt], pc_map[dt], cost, **best)
                    for dt in test_days]
            st_t = stats(rs_t)
            verdict = ("LEAD" if st_t["n"] >= 8 and st_t["exp"] > 0 and st_t["pf"] > 1.1
                       else "reject")
            log(f"  {name:<9} {best}")
            log(f"      TRAIN {fmt(best_s)}")
            log(f"      TEST  {fmt(st_t)}   -> {verdict}")
    log("\nRules held: EOD flat, conservative stop-first fills, costs both sides.")
    log("A LEAD here = candidate for deeper validation (more history, walk-forward),")
    log("NOT a proven edge. 60d of 5m data is the free-feed ceiling — first screen only.")


if __name__ == "__main__":
    main()
