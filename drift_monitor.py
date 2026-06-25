"""Edge drift monitor — re-validates the live strategy on a schedule.

A backtested edge can decay as markets change. This re-runs the out-of-sample
check on the LIVE strategy (daily BTC TSMOM) on its most recent data and writes a
status line. If recent out-of-sample expectancy turns negative, it flags DEGRADED
so we know to pause paper/live and re-research — instead of trusting a stale edge.

Designed to be run on a schedule (e.g., weekly). Appends to drift_log.csv.
Research/monitoring only; never trades.
"""
import os
import csv
from datetime import datetime, timezone

from research_harness import stats
from strategy_lab import fetch_daily, indicators
from tsmom_walkforward import run_tsmom

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "drift_log.csv")
SYMBOL = "BTC-USD"
RECENT_FRAC = 0.30          # judge the most-recent 30% (the "live-like" slice)
MIN_EXP = 0.0               # degraded if recent expectancy <= this


def main():
    d0 = fetch_daily(SYMBOL)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if d0 is None or len(d0) < 400:
        print("drift_monitor: no data"); return
    d = indicators(d0)
    cut = int(len(d) * (1 - RECENT_FRAC))
    recent_trades, _ = run_tsmom(d.iloc[cut:])
    s = stats(recent_trades)
    status = "OK" if (s["expectancy_r"] > MIN_EXP and s["profit_factor"] > 1.0) else "DEGRADED"
    span = f"{d['ts'].iloc[cut].date()}..{d['ts'].iloc[-1].date()}"
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "symbol", "recent_span", "pf", "expectancy_r", "trades", "status"])
        w.writerow([now, SYMBOL, span, s["profit_factor"], s["expectancy_r"], s["trades"], status])
    print(f"drift_monitor [{SYMBOL}] recent {span}: PF {s['profit_factor']} "
          f"exp {s['expectancy_r']}R n={s['trades']}  ->  {status}")
    if status == "DEGRADED":
        print("  ** EDGE DEGRADED — pause paper/live and re-research before trusting it. **")


if __name__ == "__main__":
    main()
