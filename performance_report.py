"""APEX PERFORMANCE REPORT — how are the paper bots actually doing?

Reads the paper journals/state for both the daily BTC trend bot and the 50-market
scanner, computes a plain-English performance summary (equity, return, open
positions, trade count, win rate, average R), prints it, and pushes it to Discord.

Run it any time, or on a schedule, to keep an eye on the forward track record.
PAPER ONLY — reports simulated results; nothing here trades.
"""
import os
import csv
import json

from strategy_lab import START_EQ
from notify import notify

HERE = os.path.dirname(os.path.abspath(__file__))


def _read_json(name):
    p = os.path.join(HERE, name)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return None
    return None


def _read_csv(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return []
    try:
        return list(csv.DictReader(open(p, encoding="utf-8", errors="ignore")))
    except Exception:
        return []


def btc_summary():
    st = _read_json("paper_tsmom_state.json")
    rows = _read_csv("paper_tsmom_journal.csv")
    if not st and not rows:
        return "BTC daily trend: not started yet."
    eq = None
    if rows:
        try:
            eq = float(rows[-1].get("equity") or 0)
        except Exception:
            eq = None
    holding = "LONG" if (st and st.get("position")) else "FLAT"
    enters = sum(1 for r in rows if r.get("action") == "ENTER")
    exits = sum(1 for r in rows if r.get("action") == "EXIT")
    eq_s = f"${eq:,.2f}" if eq is not None else "n/a"
    ret = f"{(eq/START_EQ-1):+.1%}" if eq else "n/a"
    return (f"BTC daily trend: equity {eq_s} ({ret}), currently {holding}, "
            f"{enters} entries / {exits} exits logged.")


def scanner_summary():
    st = _read_json("scanner_state.json")
    rows = _read_csv("scanner_journal.csv")
    if not st and not rows:
        return "Scanner: not started yet."
    cash = st.get("cash", START_EQ) if st else START_EQ
    positions = st.get("positions", {}) if st else {}
    # realized trades: match EXIT rows
    exits = [r for r in rows if r.get("action") == "EXIT"]
    enters = [r for r in rows if r.get("action") == "ENTER"]
    eq = None
    if rows:
        try:
            eq = float(rows[-1].get("equity") or cash)
        except Exception:
            eq = cash
    eq = eq if eq is not None else cash
    ret = f"{(eq/START_EQ-1):+.1%}"
    poss = ", ".join(positions.keys()) or "none"
    return (f"Scanner: equity ${eq:,.2f} ({ret}), {len(positions)} open ({poss}), "
            f"{len(enters)} entries / {len(exits)} exits logged.")


def main():
    btc = btc_summary()
    scn = scanner_summary()
    report = "APEX PAPER PERFORMANCE\n- " + btc + "\n- " + scn
    print(report)
    notify(report.replace("APEX PAPER PERFORMANCE\n", ""), prefix="PERFORMANCE")
    print("\n(Reminder: paper results. Real fills differ; edge must hold forward before live.)")


if __name__ == "__main__":
    main()
