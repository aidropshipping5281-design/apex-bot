"""APEX SHADOW TUNER — Layer 2 of the learning plan (APEX_LEARNING_PLAN.md).

The "proposes, does not act" brain. On a weekly cadence it asks, per sleeve:
  "Is there a parameter set that beats the live fixed params OUT-OF-SAMPLE?"

How (strict, anti-overfit):
  * Re-fit the tunable knobs on a TRAIN slice (older 70% of history).
  * Validate the winner on a held-out VALID slice (recent 30%) it never saw.
  * Only PROPOSE a change if, out-of-sample, the candidate has positive
    expectancy, a minimum trade count, AND beats the incumbent by a margin
    (hysteresis — so it doesn't chase noise). We have been burned by recent-
    data mirages before; this guards against exactly that.

CRITICAL: this module changes NOTHING the live bot does. It only computes and
reports proposals to Discord/log. Acting on them (a forward shadow paper account,
then bounded auto-promotion) is Layer 2b/3 — built only after we see the
proposals are real. Read-only w.r.t. trading. Failure-isolated.

Tunable knobs (the ones strategy_lab.backtest already exposes, so we reuse
validated code rather than reinventing it):
  trend (tsmom):  stop_mult
  mean-rev:       stop_mult, rsi_buy, rsi_exit
(Trend SMA lookback / momentum lookback are a later extension — they'd need new
indicator columns; kept out of scope here on purpose.)
"""
import os
import json
import itertools
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROPOSALS = os.path.join(HERE, "shadow_proposals.json")
SSTATE = os.path.join(HERE, "shadow_state.json")

TRAIN_FRAC = 0.70
RUN_EVERY_DAYS = 7
MIN_VALID_TRADES = 8       # need this many OOS trades to trust a candidate
MARGIN_R = 0.10            # candidate must beat incumbent OOS expectancy by >= this (R)
MIN_HISTORY = 260          # ~1yr of daily bars before we tune anything

# Incumbent = the live fixed params (must match paper_blend / strategy_lab defaults)
INCUMBENT = {"trend": {"stop_mult": 3.0},
             "meanrev": {"stop_mult": 3.0, "rsi_buy": 10, "rsi_exit": 60}}

# Search grids
TREND_GRID = [{"stop_mult": s} for s in (2.0, 2.5, 3.0, 3.5, 4.0)]
MEANREV_GRID = [{"stop_mult": s, "rsi_buy": b, "rsi_exit": x}
                for s, b, x in itertools.product((2.0, 3.0, 4.0), (5, 10, 15), (55, 60, 70))]


def _stats(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0, "exp": 0.0, "pf": 0.0}
    rs = [t["r"] for t in trades]
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {"n": n, "exp": round(sum(rs) / n, 3),
            "pf": round(wins / losses, 2) if losses > 0 else float("inf")}


def _grid_for(strat):
    return TREND_GRID if strat == "trend" else MEANREV_GRID


def _kind(strat):
    return "tsmom" if strat == "trend" else "meanrev"


def _propose_one(df, strat):
    """Return a proposal dict for one sleeve, or None on error / not enough data."""
    from strategy_lab import backtest
    if df is None or len(df) < MIN_HISTORY:
        return None
    split = int(len(df) * TRAIN_FRAC)
    train, valid = df.iloc[:split], df.iloc[split:]
    kind = _kind(strat)

    # 1) optimise on TRAIN
    best, best_exp = None, -1e9
    for params in _grid_for(strat):
        s = _stats(backtest(train, kind, **params))
        if s["n"] >= 8 and s["exp"] > best_exp:
            best, best_exp = params, s["exp"]
    if best is None:
        return None

    # 2) validate winner + incumbent on the held-out VALID slice
    v_best = _stats(backtest(valid, kind, **best))
    v_inc = _stats(backtest(valid, kind, **INCUMBENT[strat]))

    change = best != INCUMBENT[strat]
    beats = (v_best["exp"] > 0 and v_best["n"] >= MIN_VALID_TRADES
             and (v_best["exp"] - v_inc["exp"]) >= MARGIN_R)
    return {
        "incumbent": INCUMBENT[strat],
        "candidate": best,
        "oos_incumbent_exp": v_inc["exp"], "oos_incumbent_n": v_inc["n"],
        "oos_candidate_exp": v_best["exp"], "oos_candidate_n": v_best["n"],
        "propose": bool(change and beats),
    }


def run():
    """Compute proposals for every blend sleeve. Reports; changes nothing. Never raises."""
    try:
        from strategy_lab import fetch_daily, indicators
        from paper_blend import PLAN
        results = {}
        for sym, strat in PLAN:
            try:
                d0 = fetch_daily(sym)
                df = indicators(d0) if (d0 is not None and len(d0) > MIN_HISTORY) else None
                p = _propose_one(df, strat)
                if p:
                    results[f"{sym}:{strat}"] = p
            except Exception as e:
                print(f"[shadow] {sym}:{strat} skipped: {e}")
        json.dump({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "results": results}, open(PROPOSALS, "w"), indent=2)
        _report(results)
        return results
    except Exception as e:
        print(f"[shadow] run error (ignored): {e}")
        return {}


def _report(results):
    from notify import notify
    proposals = {k: v for k, v in results.items() if v["propose"]}
    if not proposals:
        notify(f"SHADOW tuner ran over {len(results)} sleeves — no parameter set beat the "
               f"live config out-of-sample. Keeping fixed params (this is the common, healthy "
               f"result).", prefix="LEARN")
        return
    lines = ["SHADOW tuner found OOS-validated parameter PROPOSALS (not yet applied — review):"]
    for sleeve, v in proposals.items():
        lines.append(f"  {sleeve}: {v['incumbent']} -> {v['candidate']} | "
                     f"OOS exp {v['oos_incumbent_exp']}R -> {v['oos_candidate_exp']}R "
                     f"(n={v['oos_candidate_n']})")
    lines.append("These are PROPOSALS only. Next step is to forward-test them in a shadow paper "
                 "account before anything touches the live bot.")
    notify("\n".join(lines), prefix="LEARN")


def maybe_run():
    """Run weekly (gated by timestamp). Never raises."""
    try:
        now = datetime.now(timezone.utc)
        last = None
        if os.path.exists(SSTATE):
            try:
                last = datetime.fromisoformat(json.load(open(SSTATE)).get("last_run"))
            except Exception:
                last = None
        if last is not None and (now - last).total_seconds() < RUN_EVERY_DAYS * 86400:
            return False
        run()
        json.dump({"last_run": now.isoformat(timespec="seconds")}, open(SSTATE, "w"))
        return True
    except Exception as e:
        print(f"[shadow] maybe_run error (ignored): {e}")
        return False


if __name__ == "__main__":
    res = run()
    print(json.dumps(res, indent=2, default=str))
