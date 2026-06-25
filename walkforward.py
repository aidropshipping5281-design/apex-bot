"""Walk-forward + transaction-cost validation of the 'skip New York' lead.

WHY THIS EXISTS
  research_harness.py found ONE survivor on a single 70/30 split: trading only
  the Asia + London sessions (skipping New York) stayed profitable out-of-sample
  on ETH and SOL. Two weaknesses make that a LEAD, not proof:
    1. It modeled ZERO transaction costs. A 2:1 strategy on 1h crypto turns over
       enough that fees + slippage can erase a thin edge.
    2. It used ONE split. A single lucky window fools you (see: the 'BTC New York
       edge' that vanished on deep data).

  This harness fixes both:
    - K-FOLD WALK-FORWARD: cut the full history into K consecutive out-of-sample
      windows and score the rule on each. A real edge is positive in MOST folds,
      not one. A mirage is positive in one and red in the rest.
    - REALISTIC COSTS: every fill pays a taker fee per side, and every MARKET
      fill (entry, stop, signal-exit) eats slippage. Limit take-profits fill at
      the level. We sweep 0 / realistic / stress cost scenarios.

  We test ETH + SOL (the leads) and BTC (control, expected to fail). For each we
  compare BASELINE (all sessions) vs SKIP-NY (Asia+London only) so we can see
  whether skipping NY actually *helps* or just trades less.

  Honest bar: SKIP-NY only graduates to paper-trading if, at REALISTIC costs, it
  is positive in a clear majority of folds AND beats baseline on the same data.
"""
import sys
import numpy as np
import pandas as pd

# Reuse the vetted pieces from the research harness (one source of truth).
from research_harness import (
    paginated_fetch, precompute_bias, signal_series, session_of,
    make_cfg, stats, EXCHANGES, EXCHANGE_TIMEOUT_MS, TIMEFRAME, TARGET_CANDLES,
)
from apex.strategy import compute_indicators
from apex.risk import RiskManager

SYMBOLS = ["ETH/USDT", "SOL/USDT", "BTC/USDT"]   # leads first, BTC = control
N_FOLDS = 6

# Cost scenarios: (label, taker_fee_per_side, slippage_per_side) as fractions.
#   realistic ~ binanceus/kraken taker 0.10% + 0.05% slippage on market fills.
#   stress doubles both to see how fragile the edge is.
COST_SCENARIOS = [
    ("zero  (sanity)", 0.0,    0.0),
    ("realistic",      0.0010, 0.0005),
    ("stress",         0.0020, 0.0010),
]


def replay_costed(df, cfg, fee_rate, slip_rate, allowed_sessions=None, bias=None):
    """Same fill logic as research_harness.replay, but every fill pays costs.

    Market fills (entry, stop-out, signal-exit) are pushed against us by
    slip_rate; limit take-profits fill at the level. A taker fee_rate is charged
    on the notional of BOTH the entry and the exit.
    """
    equity = cfg.start_equity
    rm = RiskManager(cfg)
    if bias is None:
        bias = precompute_bias(df)
    sigs = signal_series(df, cfg, bias)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    atr = df["atr"].values
    htf = df["htf_ema"].values
    ts = df["ts"].values
    pos, trades = None, []

    for i in range(2, len(df)):
        price = close[i]
        atr_v = atr[i]
        sig = sigs[i]
        if pos:
            raw_exit = reason = None
            if pos["direction"] == "long":
                if low[i] <= pos["stop"]:
                    raw_exit, reason = pos["stop"], "stop"
                elif high[i] >= pos["take"]:
                    raw_exit, reason = pos["take"], "take"
            else:
                if high[i] >= pos["stop"]:
                    raw_exit, reason = pos["stop"], "stop"
                elif low[i] <= pos["take"]:
                    raw_exit, reason = pos["take"], "take"
            if raw_exit is None and sig == "flat":
                raw_exit, reason = price, "signal"
            if raw_exit is not None:
                # Apply slippage to MARKET exits (stop, signal). Take = limit, no slip.
                if pos["direction"] == "long":
                    exit_fill = raw_exit * (1 - slip_rate) if reason != "take" else raw_exit
                else:
                    exit_fill = raw_exit * (1 + slip_rate) if reason != "take" else raw_exit
                gross = ((exit_fill - pos["entry_fill"]) if pos["direction"] == "long"
                         else (pos["entry_fill"] - exit_fill)) * pos["size"]
                fees = fee_rate * pos["size"] * (pos["entry_fill"] + exit_fill)
                pnl = gross - fees
                equity += pnl
                r = pnl / pos["risk_dollars"] if pos["risk_dollars"] else 0
                trades.append({"pnl": pnl, "r": r, "session": pos["session"]})
                pos = None
        if pos is None and sig in ("long", "short") and pd.notna(atr_v) and atr_v > 0:
            tstamp = pd.Timestamp(ts[i])
            if allowed_sessions and session_of(tstamp) not in allowed_sessions:
                continue
            if rm.check_daily_halt(equity):
                continue
            plan = rm.plan_trade(equity, price, atr_v, direction=sig)
            if plan:
                # Entry is a market fill -> pay slippage against us.
                plan["entry_fill"] = (plan["entry"] * (1 + slip_rate) if sig == "long"
                                      else plan["entry"] * (1 - slip_rate))
                plan["session"] = session_of(tstamp)
                pos = plan
    return trades


def fetch_symbol(symbol):
    import ccxt
    for exid in EXCHANGES:
        try:
            ex = getattr(ccxt, exid)({"enableRateLimit": True, "timeout": EXCHANGE_TIMEOUT_MS})
            ex.load_markets()
            sym = symbol if symbol in ex.markets else symbol.replace("/USDT", "/USD")
            if sym not in ex.markets:
                continue
            d = paginated_fetch(ex, sym, TIMEFRAME)
            if len(d) >= 1000:
                return d, f"{exid}:{sym}"
        except Exception:
            continue
    return None, None


def fold_bounds(n, k):
    """k consecutive, non-overlapping index windows covering [0, n)."""
    edges = np.linspace(0, n, k + 1, dtype=int)
    return [(edges[j], edges[j + 1]) for j in range(k)]


def main():
    print("APEX WALK-FORWARD + COST VALIDATION")
    print(f"{TIMEFRAME}, long-only spot, 2% risk, {N_FOLDS} walk-forward folds.")
    print("Question: does 'skip New York' survive REAL costs across MANY windows?\n")
    cfg = make_cfg()  # baseline and skip-NY share the same 9/21 2:1 cfg

    for symbol in SYMBOLS:
        print(f"================  {symbol}  ================", flush=True)
        raw, src = fetch_symbol(symbol)
        if raw is None:
            print("  no data\n")
            continue
        d = compute_indicators(raw, cfg)
        print(f"  {src}  candles={len(d)}  span={d['ts'].iloc[0].date()}..{d['ts'].iloc[-1].date()}")
        print("  computing SMC structure once...", flush=True)
        bias = precompute_bias(d)
        folds = fold_bounds(len(d), N_FOLDS)

        for label, fee, slip in COST_SCENARIOS:
            print(f"\n  --- costs: {label}  (fee/side={fee:.2%}, slip/side={slip:.2%}) ---")
            print(f"    {'fold (UTC span)':<26} {'BASELINE pf/exp/n':<22} {'SKIP-NY pf/exp/n':<22}")
            base_all, skip_all = [], []
            base_pos = skip_pos = 0
            for (a, b) in folds:
                seg = d.iloc[a:b]
                sb = bias[a:b]
                span = f"{seg['ts'].iloc[0].date()}..{seg['ts'].iloc[-1].date()}"
                bt = stats(replay_costed(seg, cfg, fee, slip, None, bias=sb))
                st = stats(replay_costed(seg, cfg, fee, slip, {"Asia", "London"}, bias=sb))
                base_all += [bt]; skip_all += [st]
                if bt["net"] > 0: base_pos += 1
                if st["net"] > 0: skip_pos += 1
                bs = f"{bt['profit_factor']}/{bt['expectancy_r']}/{bt['trades']}"
                ss = f"{st['profit_factor']}/{st['expectancy_r']}/{st['trades']}"
                print(f"    {span:<26} {bs:<22} {ss:<22}")
            # Aggregate across all folds (pooled trades).
            def pooled(rows):
                tot_n = sum(r["trades"] for r in rows)
                tot_net = round(sum(r["net"] for r in rows), 2)
                # weighted expectancy by trade count
                exp = (round(sum(r["expectancy_r"] * r["trades"] for r in rows) / tot_n, 3)
                       if tot_n else 0)
                return tot_n, tot_net, exp
            bn, bnet, bexp = pooled(base_all)
            sn, snet, sexp = pooled(skip_all)
            print(f"    {'POOLED':<26} "
                  f"net=${bnet} exp={bexp} n={bn} (+{base_pos}/{N_FOLDS} folds)   ||   "
                  f"net=${snet} exp={sexp} n={sn} (+{skip_pos}/{N_FOLDS} folds)")
        print(flush=True)
    print("===== WALK-FORWARD DONE =====")
    print("Read: SKIP-NY graduates only if, at 'realistic' costs, it is net-positive")
    print("in a clear majority of folds AND beats BASELINE. Otherwise it's noise.")


if __name__ == "__main__":
    main()
