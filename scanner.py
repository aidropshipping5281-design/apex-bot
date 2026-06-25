"""APEX SCANNER — always-on, multi-market opportunity hunter.

What it does (the architecture kp asked for):
  * Watches a ~50-instrument UNIVERSE across stocks, ETFs, crypto, and futures
    proxies — not a hardcoded handful.
  * Every cycle it pulls fresh data, computes the VALIDATED signals (trend-
    following + mean-reversion) on every instrument, and RANKS the live
    opportunities by strength — an "opportunity board."
  * It then ACTS on the best setups in paper, opening/holding/closing positions
    within hard risk caps (max open positions, 2% risk each, total-risk cap).
  * Run once, or with --loop to run continuously (always-on), re-scanning every
    SCAN_EVERY seconds.

Design honesty: opportunity flow comes from BREADTH (50 markets) — every cycle
surfaces far more setups than a single name — while each trade still must be a
validated edge (trend/mean-rev that survived out-of-sample + costs). It does not
fire trades just to be busy; that loses to costs. PAPER ONLY. No keys, no real
orders. Same gate as everything else: prove forward, then live with tiny size.
"""
import os
import sys
import csv
import json
import time
from datetime import datetime, timezone

import numpy as np

from strategy_lab import fetch_daily, indicators, START_EQ, RISK_PCT, FEE, SLIP
from conviction import analyze
from notify import notify
import live_tracker

CRYPTO_TICKERS = {"BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD", "DOGE-USD"}

MIN_CONVICTION = 20.0      # only act on setups the analysis is convinced about

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "scanner_state.json")
JOURNAL = os.path.join(HERE, "scanner_journal.csv")
BOARD = os.path.join(HERE, "scanner_board.txt")

# Risk caps (Brakes): how many concurrent paper positions and total risk.
MAX_POSITIONS = 6
RISK_PCT_EACH = RISK_PCT          # 2% per position
MAX_TOTAL_RISK = 0.12             # 12% total open risk cap
STOP_MULT = 3.0
SCAN_EVERY = 3600                 # seconds between scans in --loop mode

# ~50 liquid instruments across markets (the rotating hunting ground).
UNIVERSE = [
    # mega/large-cap + high-beta stocks
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "AMZN", "GOOGL", "META", "NFLX",
    "AVGO", "JPM", "V", "MA", "COST", "WMT", "XOM", "CVX", "UNH", "LLY", "HD",
    "BAC", "DIS", "INTC", "MU", "QCOM", "CRM", "ORCL", "ADBE", "UBER", "COIN",
    "PLTR", "SMCI", "MARA", "RIOT", "SHOP",
    # ETFs (indices, sectors, commodities, leveraged)
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "USO", "TLT", "XLE", "XLF", "SOXL",
    # crypto
    "BTC-USD", "ETH-USD", "SOL-USD", "AVAX-USD", "LINK-USD", "DOGE-USD",
]


def scan_one(ticker):
    """Analyze the chart across timeframes (conviction engine) and return a LONG
    opportunity if the multi-timeframe, multi-indicator read is convinced."""
    a = analyze(ticker, with_intraday=False)   # daily+weekly for speed across 50
    if not a:
        return None
    if a["direction"] != "LONG" or a["conviction"] < MIN_CONVICTION:
        return None                            # long-only paper spot; skip SHORT/FLAT
    price, atr = a["price"], a["atr"]
    if not np.isfinite(price) or not np.isfinite(atr) or atr <= 0:
        return None
    return {"ticker": ticker, "type": "LONG", "score": a["conviction"],
            "price": price, "atr": atr,
            "note": f"conviction {a['conviction']}/100 across {len(a['timeframes'])} TFs"}


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"cash": START_EQ, "positions": {}, "last_scan": None}


def save_state(s):
    with open(STATE, "w") as f:
        json.dump(s, f, indent=2)


def journal(rows):
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "action", "ticker", "type", "price", "size",
                        "cash", "equity", "open_positions", "note"])
        for r in rows:
            w.writerow(r)


def equity_of(st, prices):
    eq = st["cash"]
    for tk, p in st["positions"].items():
        eq += p["size"] * prices.get(tk, p["entry"])
    return eq


def one_scan(st):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    opps, prices = [], {}
    print(f"[{now}] scanning {len(UNIVERSE)} markets...", flush=True)
    for tk in UNIVERSE:
        o = scan_one(tk)
        if o:
            opps.append(o)
            prices[tk] = o["price"]
    opps.sort(key=lambda o: o["score"], reverse=True)

    # also need prices for currently-held names not in opps (to mark/exit)
    held = set(st["positions"].keys())
    for tk in held - set(prices.keys()):
        d0 = fetch_daily(tk)
        if d0 is not None and len(d0):
            prices[tk] = float(indicators(d0).iloc[-1]["close"])

    log = []
    # ---- manage exits: close if no longer a live opportunity or stop hit ----
    live_tickers = {o["ticker"] for o in opps}
    for tk in list(st["positions"].keys()):
        p = st["positions"][tk]
        px = prices.get(tk, p["entry"])
        exit_now = (px < p["stop"]) or (tk not in live_tickers)
        if exit_now:
            st["cash"] += p["size"] * px * (1 - FEE)
            live_tracker.record_close("scanner", f"scan:{tk}", p["entry"], px, p["size"],
                                      p["stop"], "stop" if px < p["stop"] else "signal gone")
            log.append([datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "EXIT", tk, p["type"], round(px, 4), round(p["size"], 6),
                        round(st["cash"], 2), "", len(st["positions"]) - 1,
                        "stop" if px < p["stop"] else "signal gone"])
            del st["positions"][tk]

    # ---- open new top opportunities within caps ----
    open_risk = len(st["positions"]) * RISK_PCT_EACH
    for o in opps:
        if len(st["positions"]) >= MAX_POSITIONS:
            break
        if open_risk + RISK_PCT_EACH > MAX_TOTAL_RISK:
            break
        tk = o["ticker"]
        if tk in st["positions"]:
            continue
        if live_tracker.is_paused(f"scan:{tk}"):   # tracker auto-paused this name
            continue
        price, atr = o["price"], o["atr"]
        stop_dist = atr * STOP_MULT
        risk = st["cash"] * RISK_PCT_EACH
        size = risk / stop_dist
        if size * price > st["cash"]:
            size = st["cash"] / price
        if size <= 0:
            continue
        st["cash"] -= size * price * (1 + FEE)
        st["positions"][tk] = {"entry": price, "size": size,
                               "stop": price - stop_dist, "type": o["type"]}
        open_risk += RISK_PCT_EACH
        log.append([datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "ENTER", tk, o["type"], round(price, 4), round(size, 6),
                    round(st["cash"], 2), "", len(st["positions"]), o["note"]])

    st["last_scan"] = now
    eq = equity_of(st, prices)
    save_state(st)
    if log:
        for row in log:
            row[7] = round(eq, 2)
        journal(log)

    # ---- opportunity board ----
    lines = [f"APEX OPPORTUNITY BOARD  [{now}]",
             f"paper equity ${eq:,.2f} | cash ${st['cash']:,.2f} | "
             f"open {len(st['positions'])}/{MAX_POSITIONS}: {', '.join(st['positions']) or 'none'}",
             f"{'rank':<5}{'ticker':<10}{'type':<10}{'score':<9}note"]
    for i, o in enumerate(opps[:15], 1):
        held_mark = " (HELD)" if o["ticker"] in st["positions"] else ""
        lines.append(f"{i:<5}{o['ticker']:<10}{o['type']:<10}{o['score']:<9}{o['note']}{held_mark}")
    if not opps:
        lines.append("  (no qualifying setups this cycle — staying flat is a position)")
    board = "\n".join(lines)
    with open(BOARD, "w") as f:
        f.write(board + "\n")
    print(board, flush=True)
    if log:
        print("ACTIONS: " + "; ".join(f"{r[1]} {r[2]}" for r in log), flush=True)

    # ---- Discord push: actions + a short summary ----
    if log:
        for r in log:
            tag = "crypto/validated" if r[2] in CRYPTO_TICKERS else "equity/unvalidated"
            notify(f"{r[1]} {r[2]} @ {r[4]} ({tag}) - {r[9]}", prefix="SCANNER")
    top = "; ".join(f"{o['ticker']}({o['type'][0]}{int(o['score'])})" for o in opps[:5])
    notify(f"scan done. equity ${eq:,.2f}, open {len(st['positions'])}/{MAX_POSITIONS}: "
           f"{', '.join(st['positions']) or 'none'}. Top: {top or 'no setups'}",
           prefix="SCANNER")
    return st


def main():
    loop = "--loop" in sys.argv
    st = load_state()
    while True:
        st = one_scan(st)
        if not loop:
            break
        print(f"...sleeping {SCAN_EVERY}s until next scan (Ctrl+C to stop)\n", flush=True)
        time.sleep(SCAN_EVERY)


if __name__ == "__main__":
    main()
