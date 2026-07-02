"""APEX DAY TRADER — the validated day-trade sleeves, PAPER, aggressive profile.

Strategies (backtested 2026-07-01, see intraday_lab*_results files):
  FVG  — strong-displacement fair-value-gap continuation (disp >= 1.8x day-ATR),
         entry on first retrace into the gap, stop 0.25*ATR beyond the far edge,
         2R target. NQ, ES, YM, RTY. Window 10:00-14:30 ET, one per symbol/day.
  IM   — market intraday momentum: direction = prev close -> 10:00 ET return;
         enter 15:30 ET, flat 15:55. NQ, ES.

Aggressive profile: 3% risk/trade, max 2 concurrent FVG positions (+ IM),
ALWAYS flat by 15:55 ET. Every action -> Discord + journal + live_tracker
(sleeves auto-pause on proven negative live expectancy).

Called every cycle by always_on (5-min cadence during RTH). Idempotent.
PAPER ONLY — no real orders anywhere in this file.
"""
import os, json, csv
from datetime import time as T
import numpy as np
import pandas as pd
from notify import notify
import live_tracker

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "day_state.json")
JOURNAL = os.path.join(HERE, "day_journal.csv")

FVG_SYMS = ["NQ=F", "ES=F", "YM=F", "RTY=F"]
IM_SYMS = ["NQ=F", "ES=F"]
DISP = 1.8                 # displacement threshold x day-ATR (validated)
RISK = 0.03                # aggressive: 3% per trade (paper)
MAX_FVG_POS = 2
FEE = 0.0001               # per side, futures paper parity with backtests
START_EQ = 100.0
RTH_O, FLAT = T(9, 30), T(15, 55)


def now_et():
    return pd.Timestamp.now(tz="America/New_York")


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"cash": START_EQ, "positions": {}, "taken": {}}


def save(s):
    json.dump(s, open(STATE, "w"), indent=2)


def jlog(row):
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "action", "key", "dir", "price", "size", "cash", "note"])
        w.writerow(row)


def fetch_today(sym):
    import yfinance as yf
    raw = yf.download(sym, interval="5m", period="2d", auto_adjust=False,
                      progress=False, threads=False, prepost=False)
    if raw is None or len(raw) == 0:
        return None, None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [str(c[0]).lower() for c in raw.columns]
    else:
        raw.columns = [str(c).lower() for c in raw.columns]
    df = raw.reset_index()
    df = df.rename(columns={df.columns[0]: "ts"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
    df["date"] = df["ts"].dt.date
    df["t"] = df["ts"].dt.time
    df = df[(df["t"] >= RTH_O) & (df["t"] < T(16, 0))].reset_index(drop=True)
    days = sorted(df["date"].unique())
    if not len(days):
        return None, None
    today = df[df["date"] == days[-1]].reset_index(drop=True)
    prev_close = None
    if len(days) > 1:
        prev = df[df["date"] == days[-2]]
        if len(prev):
            prev_close = float(prev.iloc[-1]["close"])
    return today, prev_close


def find_fvg(g):
    """First strong-displacement FVG of the day (validated codification).
    Returns dict(dir, entry, stop, target_mult_base) or None."""
    atr = float((g["high"] - g["low"]).mean())
    if atr <= 0 or len(g) < 4:
        return None
    for j in range(2, len(g)):
        b1, b2, b3 = g.iloc[j - 2], g.iloc[j - 1], g.iloc[j]
        if not (T(10, 0) <= b3["t"] < T(14, 30)):
            continue
        if float(b2["high"] - b2["low"]) < DISP * atr:
            continue
        if b2["close"] > b2["open"] and b1["high"] < b3["low"]:
            d, top, bot = 1, float(b3["low"]), float(b1["high"])
        elif b2["close"] < b2["open"] and b1["low"] > b3["high"]:
            d, top, bot = -1, float(b1["low"]), float(b3["high"])
        else:
            continue
        entry = top if d > 0 else bot
        stop = (bot - 0.25 * atr) if d > 0 else (top + 0.25 * atr)
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        return dict(d=d, entry=entry, stop=stop, target=entry + d * 2.0 * risk,
                    sig_idx=j)
    return None


def mark_equity(st, last_px):
    """cash + (margin parked at entry) + open P&L. Works long and short and is
    exactly reversed by close_pos, so the paper books always balance."""
    eq = st["cash"]
    for k, p in st["positions"].items():
        px = last_px.get(p["sym"], p["entry"])
        eq += p["size"] * p["entry"] + p["dir"] * p["size"] * (px - p["entry"])
    return eq


def close_pos(st, key, px, why, ts):
    p = st["positions"].pop(key)
    proceeds = p["size"] * p["entry"] + p["dir"] * p["size"] * (px - p["entry"])
    st["cash"] += proceeds * (1 - FEE)
    live_tracker.record_close("day", key, p["entry"], px, p["size"] * p["dir"],
                              p["stop"], why)
    jlog([ts, "EXIT", key, p["dir"], round(px, 4), round(p["size"], 6),
          round(st["cash"], 2), why])
    notify(f"EXIT {key} @ {px:.2f} ({why})", prefix="DAY")


def run():
    t = now_et()
    if t.weekday() >= 5 or not (T(9, 40) <= t.time() <= T(16, 5)):
        return
    st = load()
    ts = t.isoformat(timespec="seconds")
    today_key = str(t.date())
    taken = st["taken"].setdefault(today_key, [])
    # drop stale taken-days
    st["taken"] = {k: v for k, v in st["taken"].items() if k == today_key}
    data, last_px = {}, {}
    for sym in set(FVG_SYMS + IM_SYMS):
        g, pc = fetch_today(sym)
        if g is not None and len(g):
            data[sym] = (g, pc)
            last_px[sym] = float(g.iloc[-1]["close"])
    # 1) manage open positions (stop/target/EOD on latest completed bar)
    for key in list(st["positions"].keys()):
        p = st["positions"][key]
        sym = p["sym"]
        if sym not in data:
            continue
        g, _ = data[sym]
        b = g.iloc[-1]
        d = p["dir"]
        if t.time() >= FLAT:
            close_pos(st, key, float(b["close"]), "eod-flat", ts)
        elif d > 0 and float(b["low"]) <= p["stop"]:
            close_pos(st, key, p["stop"], "stop", ts)
        elif d < 0 and float(b["high"]) >= p["stop"]:
            close_pos(st, key, p["stop"], "stop", ts)
        elif p.get("target") and ((d > 0 and float(b["high"]) >= p["target"]) or
                                  (d < 0 and float(b["low"]) <= p["target"])):
            close_pos(st, key, p["target"], "target", ts)
    eq = mark_equity(st, last_px)
    # 2) FVG entries (10:05-14:35 ET)
    if T(10, 5) <= t.time() <= T(14, 35):
        for sym in FVG_SYMS:
            key = f"FVG:{sym}"
            n_fvg = sum(1 for k in st["positions"] if k.startswith("FVG"))
            if (key in taken or key in st["positions"] or n_fvg >= MAX_FVG_POS
                    or sym not in data or live_tracker.is_paused(key)):
                continue
            g, _ = data[sym]
            sig = find_fvg(g)
            if not sig:
                continue
            after = g.iloc[sig["sig_idx"] + 1:]
            if not len(after):
                continue
            d = sig["d"]
            touched = ((d > 0 and float(after["low"].min()) <= sig["entry"]) or
                       (d < 0 and float(after["high"].max()) >= sig["entry"]))
            if not touched:
                continue
            risk_d = abs(sig["entry"] - sig["stop"])
            size = eq * RISK / risk_d
            st["cash"] -= size * sig["entry"] * (1 + FEE)
            st["positions"][key] = dict(sym=sym, strat="FVG", dir=d,
                                        entry=sig["entry"], stop=sig["stop"],
                                        target=sig["target"], size=size,
                                        date=today_key)
            taken.append(key)
            jlog([ts, "ENTER", key, d, round(sig["entry"], 4), round(size, 6),
                  round(st["cash"], 2), f"FVG disp>={DISP}"])
            notify(f"ENTER {key} {'LONG' if d>0 else 'SHORT'} @ {sig['entry']:.2f} "
                   f"stop {sig['stop']:.2f} tgt {sig['target']:.2f} (3% risk)",
                   prefix="DAY")
    # 3) IM entries (15:30-15:45 ET)
    if T(15, 30) <= t.time() <= T(15, 45):
        for sym in IM_SYMS:
            key = f"IM:{sym}"
            if key in taken or key in st["positions"] or sym not in data \
                    or live_tracker.is_paused(key):
                continue
            g, pc = data[sym]
            early = g[g["t"] < T(10, 0)]
            if pc is None or not len(early):
                continue
            ret = float(early.iloc[-1]["close"]) / pc - 1.0
            if ret == 0:
                continue
            d = 1 if ret > 0 else -1
            entry = float(g.iloc[-1]["close"])
            risk_d = float((g["high"] - g["low"]).mean()) * 3.0
            if risk_d <= 0:
                continue
            stop = entry - d * risk_d
            size = eq * RISK / risk_d
            st["cash"] -= size * entry * (1 + FEE)
            st["positions"][key] = dict(sym=sym, strat="IM", dir=d, entry=entry,
                                        stop=stop, target=None, size=size,
                                        date=today_key)
            taken.append(key)
            jlog([ts, "ENTER", key, d, round(entry, 4), round(size, 6),
                  round(st["cash"], 2), f"IM ret10={ret:+.3%}"])
            notify(f"ENTER {key} {'LONG' if d>0 else 'SHORT'} @ {entry:.2f} "
                   f"(last-30min momentum, flat 15:55)", prefix="DAY")
    save(st)
    eq = mark_equity(st, last_px)
    held = ", ".join(st["positions"].keys()) or "flat"
    print(f"[day_trader] {ts} equity ${eq:,.2f} | {held}")


if __name__ == "__main__":
    run()
