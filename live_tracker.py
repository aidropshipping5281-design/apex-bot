"""APEX LIVE TRACKER — Layer 1 of the learning plan (APEX_LEARNING_PLAN.md).

What it does (the safe, valuable kind of "learning"):
  1. Records every CLOSED paper trade with its PnL and R, per sleeve
     (a sleeve = a strategy on a market, e.g. "NVDA:trend" or "scan:BTC-USD").
  2. Computes rolling live stats per sleeve: expectancy (R), win rate,
     profit factor, net PnL, sample size.
  3. AUTO-PAUSE kill-switch: if a sleeve's rolling expectancy goes negative
     over a meaningful sample, it is paused — the engines stop OPENING new
     trades on it (existing positions still manage/exit normally). If it
     recovers, it un-pauses. This protects capital from a decayed edge.
  4. Weekly Discord summary of how each sleeve is actually doing live.

Everything here is ADDITIVE and failure-isolated: a bug in the tracker must
never break the trading loop, so every public call swallows its own errors.
PAPER ONLY. This measures and protects; it does not place orders.
"""
import os
import csv
import json
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "closed_trades.csv")
PAUSED = os.path.join(HERE, "paused_sleeves.json")
TSTATE = os.path.join(HERE, "tracker_state.json")

# --- tuning (deliberately conservative; we've been burned by small samples) ---
WINDOW = 30            # rolling trades per sleeve used for live stats
MIN_SAMPLE_PAUSE = 20  # need at least this many closed trades before auto-pause can fire
SUMMARY_EVERY_DAYS = 7 # weekly Discord performance summary

TRADE_COLS = ["timestamp", "source", "sleeve", "entry", "exit",
              "size", "stop", "pnl", "r", "reason"]


# ----------------------------------------------------------------------------
# 1. RECORD a closed trade (called by paper_blend / scanner on every EXIT)
# ----------------------------------------------------------------------------
def record_close(source, sleeve, entry, exit_px, size, stop, reason, fee=0.0010):
    """Append one closed trade with computed PnL and R. Never raises."""
    try:
        entry, exit_px, size, stop = float(entry), float(exit_px), float(size), float(stop)
        # PnL net of round-trip fees (matches how the engines move cash)
        pnl = size * (exit_px - entry) - size * fee * (entry + exit_px)
        risk_dollars = size * (entry - stop)
        r = (pnl / risk_dollars) if risk_dollars > 0 else 0.0
        new = not os.path.exists(TRADES)
        with open(TRADES, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(TRADE_COLS)
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        source, sleeve, round(entry, 6), round(exit_px, 6),
                        round(size, 8), round(stop, 6), round(pnl, 4), round(r, 4), reason])
    except Exception as e:
        print(f"[tracker] record_close error (ignored): {e}")


# ----------------------------------------------------------------------------
# 2. STATS per sleeve from the closed-trades log
# ----------------------------------------------------------------------------
def _load_trades():
    rows = []
    if not os.path.exists(TRADES):
        return rows
    try:
        with open(TRADES, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    row["r"] = float(row["r"]); row["pnl"] = float(row["pnl"])
                    rows.append(row)
                except Exception:
                    continue
    except Exception as e:
        print(f"[tracker] read error (ignored): {e}")
    return rows


def sleeve_stats():
    """Return {sleeve: {sample, expectancy_r, win_rate, profit_factor, net_pnl}} over the rolling window."""
    by = {}
    for row in _load_trades():
        by.setdefault(row["sleeve"], []).append(row)
    out = {}
    for sleeve, trades in by.items():
        recent = trades[-WINDOW:]
        n = len(recent)
        if n == 0:
            continue
        rs = [t["r"] for t in recent]
        wins = [t["pnl"] for t in recent if t["pnl"] > 0]
        losses = [t["pnl"] for t in recent if t["pnl"] <= 0]
        gross_w, gross_l = sum(wins), -sum(losses)
        out[sleeve] = {
            "sample": n,
            "expectancy_r": round(sum(rs) / n, 3),
            "win_rate": round(len(wins) / n, 3),
            "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else float("inf"),
            "net_pnl": round(sum(t["pnl"] for t in recent), 2),
        }
    return out


# ----------------------------------------------------------------------------
# 3. AUTO-PAUSE kill-switch
# ----------------------------------------------------------------------------
def _read_paused():
    if os.path.exists(PAUSED):
        try:
            return set(json.load(open(PAUSED)))
        except Exception:
            return set()
    return set()


def is_paused(sleeve):
    """True if the engines should NOT open new trades on this sleeve. Never raises."""
    try:
        return sleeve in _read_paused()
    except Exception:
        return False


def update():
    """Recompute stats, refresh the paused set, persist. Returns (stats, paused). Never raises."""
    try:
        stats = sleeve_stats()
        paused = set()
        for sleeve, s in stats.items():
            if s["sample"] >= MIN_SAMPLE_PAUSE and s["expectancy_r"] < 0:
                paused.add(sleeve)
        prev = _read_paused()
        json.dump(sorted(paused), open(PAUSED, "w"), indent=2)
        # announce changes
        newly = paused - prev
        recovered = prev - paused
        from notify import notify
        for sl in sorted(newly):
            s = stats.get(sl, {})
            notify(f"AUTO-PAUSE {sl}: live expectancy {s.get('expectancy_r')}R over "
                   f"{s.get('sample')} trades — no new entries until it recovers.", prefix="LEARN")
        for sl in sorted(recovered):
            notify(f"UN-PAUSE {sl}: edge recovered, resuming entries.", prefix="LEARN")
        return stats, paused
    except Exception as e:
        print(f"[tracker] update error (ignored): {e}")
        return {}, set()


# ----------------------------------------------------------------------------
# 4. Weekly Discord performance summary
# ----------------------------------------------------------------------------
def summary_text():
    stats = sleeve_stats()
    if not stats:
        return "LEARN weekly: no closed paper trades yet — tracker is armed and watching."
    ranked = sorted(stats.items(), key=lambda kv: kv[1]["expectancy_r"], reverse=True)
    lines = ["LEARN weekly performance (rolling, per sleeve):"]
    for sleeve, s in ranked:
        flag = " [PAUSED]" if is_paused(sleeve) else ""
        lines.append(f"  {sleeve}: exp {s['expectancy_r']}R, win {int(s['win_rate']*100)}%, "
                     f"PF {s['profit_factor']}, net ${s['net_pnl']}, n={s['sample']}{flag}")
    return "\n".join(lines)


def maybe_weekly_summary():
    """Post a weekly summary to Discord if SUMMARY_EVERY_DAYS have passed. Never raises."""
    try:
        now = datetime.now(timezone.utc)
        last = None
        if os.path.exists(TSTATE):
            try:
                last = datetime.fromisoformat(json.load(open(TSTATE)).get("last_summary"))
            except Exception:
                last = None
        if last is not None and (now - last).total_seconds() < SUMMARY_EVERY_DAYS * 86400:
            return False
        from notify import notify
        notify(summary_text(), prefix="LEARN")
        json.dump({"last_summary": now.isoformat(timespec="seconds")}, open(TSTATE, "w"))
        return True
    except Exception as e:
        print(f"[tracker] weekly summary error (ignored): {e}")
        return False


if __name__ == "__main__":
    stats, paused = update()
    print(summary_text())
    print("paused sleeves:", sorted(paused) or "none")
