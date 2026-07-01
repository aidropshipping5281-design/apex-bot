"""APEX BLEND paper engine — runs TREND + MEAN-REVERSION together, once daily.

Each instrument runs its VALIDATED strategy (or both):
  crypto (BTC/ETH)      -> TREND
  indices/large-cap     -> TREND and MEAN-REVERSION (both validated on these)
Trend = low win / big winners / crash protection.
Mean-reversion = high win / small wins / fills the choppy stretches.
Together: more frequent wins + smoother curve + keep the protective trend edge.

One combined paper account, hard risk caps, Discord alerts on every move.
PAPER ONLY. Idempotent per day — safe to run on a schedule / in the always-on loop.
"""
import os, json, csv
from datetime import datetime, timezone
import numpy as np
from strategy_lab import fetch_daily, indicators, START_EQ, RISK_PCT, FEE
from notify import notify
import live_tracker

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "paper_blend_state.json")
JOURNAL = os.path.join(HERE, "paper_blend_journal.csv")
STOP_MULT = 3.0
MAX_POSITIONS = 8
MAX_TOTAL_RISK = 0.16          # 8 x 2%
RSI_BUY, RSI_EXIT = 10, 60

# (symbol, strategy) — the validated basket
PLAN = [
    ("BTC-USD", "trend"), ("ETH-USD", "trend"),
    ("QQQ", "trend"), ("QQQ", "meanrev"),
    ("AMD", "trend"), ("AMD", "meanrev"),
    ("NVDA", "trend"), ("NVDA", "meanrev"),
    ("SPY", "meanrev"),
    # futures (added 2026-07-01): NQ trend = validated (mirrors QQQ, passed WF);
    # ES meanrev = marginal (+0.09-0.20R OOS) — watch, don't trust with size yet
    ("NQ=F", "trend"), ("ES=F", "meanrev"),
    # FX majors (added 2026-07-01): RESEARCH SLEEVES ONLY — this engine FAILED OOS
    # on all 4 (both strategies). Expect negative expectancy; live_tracker will
    # auto-pause them once >=20 closed trades confirm it. Paper forward data only.
    ("EURUSD=X", "trend"), ("GBPUSD=X", "trend"),
    ("USDJPY=X", "trend"), ("AUDUSD=X", "trend"),
]


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"cash": START_EQ, "positions": {}, "last_date": None}


def save(s):
    json.dump(s, open(STATE, "w"), indent=2)


def jlog(rows):
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "action", "key", "strat", "price", "size", "cash", "equity", "note"])
        for r in rows:
            w.writerow(r)


def entry_ok(strat, r):
    if strat == "trend":
        return (not np.isnan(r["sma100"]) and r["close"] > r["sma100"]
                and not np.isnan(r["mom90"]) and r["mom90"] > 0)
    return (not np.isnan(r["sma200"]) and r["close"] > r["sma200"]
            and not np.isnan(r["rsi2"]) and r["rsi2"] < RSI_BUY)


def exit_ok(strat, r, pos):
    if r["low"] <= pos["stop"]:
        return "stop"
    if strat == "trend":
        return "trend-break" if (not np.isnan(r["sma100"]) and r["close"] < r["sma100"]) else None
    if r["rsi2"] > RSI_EXIT:
        return "rsi-recover"
    if not np.isnan(r["sma200"]) and r["close"] < r["sma200"]:
        return "below-200"
    return None


def main():
    st = load()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cache, log = {}, []
    bar_date = None
    for sym, strat in PLAN:
        if sym not in cache:
            d0 = fetch_daily(sym)
            cache[sym] = indicators(d0) if (d0 is not None and len(d0) > 220) else None
        d = cache[sym]
        if d is None:
            continue
        r = d.iloc[-1]
        bar_date = str(r["ts"].date())
        key = f"{sym}:{strat}"
        price, atr = float(r["close"]), float(r["atr"])
        if not np.isfinite(atr) or atr <= 0:
            continue
        # manage existing
        if key in st["positions"]:
            why = exit_ok(strat, r, st["positions"][key])
            if why:
                p = st["positions"][key]
                st["cash"] += p["size"] * price * (1 - FEE)
                live_tracker.record_close("blend", key, p["entry"], price, p["size"], p["stop"], why)
                log.append([now, "EXIT", key, strat, round(price, 4), round(p["size"], 6),
                            round(st["cash"], 2), "", why])
                del st["positions"][key]
        # open new (skip sleeves the tracker has auto-paused for negative live edge)
        elif entry_ok(strat, r):
            if live_tracker.is_paused(key):
                continue
            if len(st["positions"]) >= MAX_POSITIONS:
                continue
            if len(st["positions"]) * RISK_PCT + RISK_PCT > MAX_TOTAL_RISK:
                continue
            stop_dist = atr * STOP_MULT
            risk = st["cash"] * RISK_PCT
            size = risk / stop_dist
            if size * price > st["cash"]:
                size = st["cash"] / price
            if size <= 0:
                continue
            st["cash"] -= size * price * (1 + FEE)
            st["positions"][key] = {"entry": price, "size": size, "stop": price - stop_dist}
            log.append([now, "ENTER", key, strat, round(price, 4), round(size, 6),
                        round(st["cash"], 2), "", "trend up" if strat == "trend" else "oversold dip"])

    # mark to market
    eq = st["cash"]
    for key, p in st["positions"].items():
        sym = key.split(":")[0]
        d = cache.get(sym)
        px = float(d.iloc[-1]["close"]) if d is not None else p["entry"]
        eq += p["size"] * px
    st["last_date"] = bar_date
    save(st)
    if log:
        for row in log:
            row[7] = round(eq, 2)
        jlog(log)
        for r in log:
            notify(f"{r[1]} {r[2]} @ {r[4]} ({r[8]})", prefix="BLEND")
    held = ", ".join(st["positions"].keys()) or "none"
    line = (f"[{bar_date}] BLEND scan: equity ${eq:,.2f}, {len(st['positions'])}/{MAX_POSITIONS} open "
            f"({held}). {len(log)} action(s).")
    print(line)
    notify(line, prefix="BLEND")


if __name__ == "__main__":
    main()
