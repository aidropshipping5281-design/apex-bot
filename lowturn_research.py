"""Lower-turnover / cost-aware strategy search.

THE DIAGNOSIS (why 1h died)
  Walk-forward showed the strategy's zero-cost edge (~+0.1 to +0.15R/trade) is
  REAL-ish but smaller than the cost of trading it. In R-terms, the round-trip
  friction is:

        cost_R  =  2 * (fee + slip)  /  ( atr_mult * (ATR/price) )

  On 1h crypto, ATR/price ~ 0.7%, so with realistic costs cost_R ~ 0.29R/trade.
  Edge 0.10R  -  friction 0.29R  =  net loss. Death by friction.

  The lever: friction shrinks as (ATR/price) grows. Higher timeframes have a
  much larger ATR/price, so the SAME percentage fee eats a SMALLER fraction of
  the per-trade risk. 1h->4h->1d cuts cost_R roughly 2x then 4x. Wider stops
  (bigger atr_mult) help too. Fewer, bigger trades = less total friction.

WHAT THIS RUNS
  The exact same strategy + risk engine, but on 4h and 1d (and 1h as the control
  that we know loses), at two stop widths, with realistic costs ALWAYS modeled.
  For each combo it prints the zero-cost expectancy, the realistic-cost
  expectancy, and the gap between them (= the average friction per trade in R).
  A combo is interesting only if realistic-cost expectancy and net are POSITIVE.

  This is research-only. No live, no keys. Any winner is a LEAD that then needs
  the full walk-forward (walkforward.py) before it means anything.
"""
import numpy as np
import pandas as pd

from research_harness import (
    paginated_fetch, precompute_bias, stats, make_cfg,
    EXCHANGES, EXCHANGE_TIMEOUT_MS, TF_MS,
)
from walkforward import replay_costed
from apex.strategy import compute_indicators

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
# (timeframe, candles to pull). 1h kept as the known-losing control.
TIMEFRAMES = [("1h", 16000), ("4h", 9000), ("1d", 2500)]
STOP_WIDTHS = [1.5, 2.5]                 # atr_mult: normal vs wide
FEE, SLIP = 0.0010, 0.0005               # realistic per side


def fetch(symbol, timeframe, target):
    import ccxt
    for exid in EXCHANGES:
        try:
            ex = getattr(ccxt, exid)({"enableRateLimit": True, "timeout": EXCHANGE_TIMEOUT_MS})
            ex.load_markets()
            sym = symbol if symbol in ex.markets else symbol.replace("/USDT", "/USD")
            if sym not in ex.markets:
                continue
            d = paginated_fetch(ex, sym, timeframe, target=target)
            if len(d) >= 300:
                return d, f"{exid}:{sym}"
        except Exception:
            continue
    return None, None


def main():
    print("APEX LOWER-TURNOVER / COST-AWARE SEARCH")
    print("Same strategy, higher timeframes, realistic costs ALWAYS on.")
    print("Goal: find a timeframe/stop where per-trade EDGE > per-trade FRICTION.\n")
    for symbol in SYMBOLS:
        print(f"================  {symbol}  ================", flush=True)
        for tf, target in TIMEFRAMES:
            raw, src = fetch(symbol, tf, target)
            if raw is None:
                print(f"  {tf}: no data")
                continue
            atr_price = float((raw['high'].sub(raw['low'])).div(raw['close']).tail(500).mean())
            print(f"  --- {tf}  ({src}, candles={len(raw)}, "
                  f"span={raw['ts'].iloc[0].date()}..{raw['ts'].iloc[-1].date()}, "
                  f"avg bar range/price~{atr_price:.2%}) ---")
            bias = precompute_bias(raw)
            print(f"      {'stop':<10} {'zero exp/n':<16} {'real exp/n':<16} "
                  f"{'friction/trade':<16} {'real net$':<10} verdict")
            for mult in STOP_WIDTHS:
                cfg = make_cfg(atr_mult=mult)
                d = compute_indicators(raw, cfg)
                z = stats(replay_costed(d, cfg, 0.0, 0.0, bias=bias))
                r = stats(replay_costed(d, cfg, FEE, SLIP, bias=bias))
                fric = round(z["expectancy_r"] - r["expectancy_r"], 3)
                good = r["expectancy_r"] > 0 and r["net"] > 0 and r["trades"] >= 15
                verdict = "LEAD?" if good else "no"
                zs = f"{z['expectancy_r']}/{z['trades']}"
                rs = f"{r['expectancy_r']}/{r['trades']}"
                print(f"      {str(mult)+'xATR':<10} {zs:<16} {rs:<16} "
                      f"{str(fric)+'R':<16} {r['net']:<10} {verdict}", flush=True)
            print()
        print(flush=True)
    print("===== LOWER-TURNOVER SEARCH DONE =====")
    print("A 'LEAD?' = positive AFTER realistic costs. Confirm with walkforward.py")
    print("(multi-fold) before trusting it. Everything else: friction still wins.")


if __name__ == "__main__":
    main()
