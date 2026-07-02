"""APEX always-on daemon — runs the BLEND (trend+mean-reversion) and the 50-market
SCANNER on a loop, independent of Claude. Resilient: one failure can't kill the
loop (so a network blip doesn't stop the bot). PAPER until live keys are set.

Run via "25) ALWAYS ON (no Claude needed).bat", or register it to auto-start
(see that .bat for the one-time schtasks command). Posts to Discord each cycle.
"""
import time
import traceback
import live_tracker
import day_trader
from notify import notify

# 2026-07-01: kp's mandate — PURE DAY TRADER. Swing engines (paper_blend,
# scanner, shadow_tuner) removed from the loop. Code + state files remain in
# the repo, dormant, in case they're ever re-enabled.
CYCLE_SECONDS = 3600     # slow lane: tracker stats + weekly summary
DAY_SECONDS = 300        # fast lane during US RTH: day_trader (5m bars)


def rth_now():
    import pandas as pd
    from datetime import time as T
    t = pd.Timestamp.now(tz="America/New_York")
    return t.weekday() < 5 and T(9, 30) <= t.time() <= T(16, 10)


def safe(label, fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"[always_on] {label} ERROR: {e}")
        traceback.print_exc()
        try:
            notify(f"{label} hit an error: {e} (loop continues)", prefix="APEX WARN")
        except Exception:
            pass


def main():
    print("APEX day-trader daemon starting. Ctrl+C to stop.")
    notify("DAY TRADER daemon started — FVG + IM on index futures, "
           "3% risk, EOD-flat. Pure day trading, no Claude needed.", prefix="APEX")
    last_slow = 0.0
    while True:
        if time.time() - last_slow >= CYCLE_SECONDS:
            safe("tracker", live_tracker.update)          # live stats + auto-pause
            safe("weekly-summary", live_tracker.maybe_weekly_summary)
            last_slow = time.time()
        safe("day-trader", day_trader.run)                # the whole show
        sleep_s = DAY_SECONDS if rth_now() else 900   # never oversleep the open
        print(f"[always_on] sleeping {sleep_s}s")
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
