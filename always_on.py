"""APEX always-on daemon — runs the BLEND (trend+mean-reversion) and the 50-market
SCANNER on a loop, independent of Claude. Resilient: one failure can't kill the
loop (so a network blip doesn't stop the bot). PAPER until live keys are set.

Run via "25) ALWAYS ON (no Claude needed).bat", or register it to auto-start
(see that .bat for the one-time schtasks command). Posts to Discord each cycle.
"""
import time
import traceback
import paper_blend
import scanner
import live_tracker
import shadow_tuner
from notify import notify

CYCLE_SECONDS = 3600   # re-evaluate every hour (daily signals; hourly catch is plenty)


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
    print("APEX always-on daemon starting. Ctrl+C to stop.")
    notify("Always-on daemon started — blend + scanner looping, no Claude needed.", prefix="APEX")
    while True:
        safe("blend", paper_blend.main)
        st = scanner.load_state()
        safe("scanner", scanner.one_scan, st)
        safe("tracker", live_tracker.update)              # refresh live stats + auto-pause set
        safe("weekly-summary", live_tracker.maybe_weekly_summary)
        safe("shadow-tuner", shadow_tuner.maybe_run)      # weekly OOS param proposals (reports only)
        print(f"[always_on] cycle complete; sleeping {CYCLE_SECONDS}s")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()
