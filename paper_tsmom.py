"""Daily BTC trend-following (TSMOM) PAPER trader — one decision per day.

This is the forward test of the strategy strategy_lab/tsmom_walkforward validate.
It makes the SAME daily long/flat decision on BTC and tracks a PAPER account so
we can watch live-forward whether the backtested edge shows up in real time.

  RULE (long/flat, daily close):
    enter long  if flat   and close > SMA(100) and 90-day momentum > 0
    exit  flat  if long   and (close < SMA(100) or close < entry - 3*ATR)

  State persists in paper_tsmom_state.json; every run appends a line to
  paper_tsmom_journal.csv. Idempotent per day — safe to run on a daily schedule.
  PAPER ONLY. Never sends a real order. No keys.
"""
import json
import os
import csv
from datetime import datetime, timezone

import numpy as np

from strategy_lab import indicators, fetch_daily, START_EQ, RISK_PCT, FEE, SLIP
from notify import notify

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "paper_tsmom_state.json")
JOURNAL = os.path.join(HERE, "paper_tsmom_journal.csv")
SYMBOL = "BTC-USD"
STOP_MULT = 3.0


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"cash": START_EQ, "position": None, "last_date": None}


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2)


def journal(row):
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "bar_date", "action", "price", "size",
                        "cash", "equity", "note"])
        w.writerow(row)


def main():
    st = load_state()
    d0 = fetch_daily(SYMBOL)
    if d0 is None or len(d0) < 200:
        print("No data; aborting (no state change).")
        return
    d = indicators(d0)
    last = d.iloc[-1]
    bar_date = str(last["ts"].date())
    price = float(last["close"])
    sma = float(last["sma100"]) if not np.isnan(last["sma100"]) else None
    mom = float(last["mom90"]) if not np.isnan(last["mom90"]) else None
    atr = float(last["atr"]) if not np.isnan(last["atr"]) else None
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if st.get("last_date") == bar_date:
        eq = st["cash"] + (st["position"]["size"] * price if st["position"] else 0)
        print(f"Already processed {bar_date}. Holding={'yes' if st['position'] else 'no'}. "
              f"Paper equity ${eq:.2f}. No action.")
        return

    action, note = "hold", ""
    pos = st["position"]

    # ---- exit check ----
    if pos:
        stop_hit = price < pos["stop"]
        trend_break = sma is not None and price < sma
        if stop_hit or trend_break:
            st["cash"] += pos["size"] * price * (1 - FEE)
            action = "EXIT"
            note = "stop" if stop_hit else "trend-break (close<SMA100)"
            pos = None
    # ---- entry check ----
    elif sma is not None and mom is not None and atr is not None and atr > 0:
        if price > sma and mom > 0:
            stop_dist = atr * STOP_MULT
            risk = st["cash"] * RISK_PCT
            size = risk / stop_dist
            if size * price > st["cash"]:           # no leverage
                size = st["cash"] / price
            st["cash"] -= size * price * (1 + FEE)
            pos = {"entry": price, "size": size, "stop": price - stop_dist}
            action = "ENTER"
            note = f"close>{sma:.0f} SMA100 & mom90>0"

    st["position"] = pos
    st["last_date"] = bar_date
    save_state(st)
    equity = st["cash"] + (pos["size"] * price if pos else 0)
    journal([now, bar_date, action, round(price, 2),
             round(pos["size"], 6) if pos else 0, round(st["cash"], 2),
             round(equity, 2), note])
    held = "LONG" if pos else "FLAT"
    line = (f"[{bar_date}] BTC ${price:,.0f}  SMA100 {sma:,.0f}  mom90 "
            f"{mom:+.1%}  ->  {action} ({held})  paper equity ${equity:,.2f}  {note}")
    print(line)
    if action in ("ENTER", "EXIT"):
        notify(line, prefix="BTC PAPER")


if __name__ == "__main__":
    main()
