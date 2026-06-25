"""Out-of-sample strategy research on STOCKS + GOLD, using free data (yfinance).

WHY
  kp asked: does the Apex strategy show any edge beyond crypto? This runs the
  SAME honest out-of-sample test (70% train / 30% test, a variant only counts if
  it stays profitable on the unseen test slice) on a basket of liquid stocks plus
  gold (GLD ETF). Free data, NO broker keys, research-only -- nothing here trades.

NOTES / HONEST LIMITS
  - Source = Yahoo Finance via yfinance (free). Intraday (60m) history is capped
    at ~730 days by Yahoo, so stock samples are smaller than the crypto 1h runs.
    We also run a DAILY pass (years of history) as a second look.
  - The 'skip New York' idea is crypto-only: stocks trade *during* US hours, so
    that session filter is dropped here. We test the price-action variants only.
  - Same skeptical bar as crypto: an in-sample-only win is overfitting -> reject.
    Anything that survives is a LEAD requiring walk-forward + costs before it
    means anything (see walkforward.py).
"""
import sys
import numpy as np
import pandas as pd

from research_harness import precompute_bias, replay, stats, make_cfg
from apex.strategy import compute_indicators

# Liquid, fractionable names that fit a micro account + gold via GLD.
SYMBOLS = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA", "GLD"]
TRAIN_FRAC = 0.70

# Stock-relevant variants (no session filter -- US session is the whole day).
VARIANTS = [
    ("baseline 9/21 2:1",         make_cfg()),
    ("EMA 20/50 2:1",             make_cfg(ema_fast=20, ema_slow=50)),
    ("RR 3:1",                    make_cfg(rr=3.0)),
    ("RR 1.5:1",                  make_cfg(rr=1.5)),
]


def normalize(df):
    """yfinance frame -> the OHLCV shape the apex engine expects."""
    if df is None or df.empty:
        return None
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):           # single-symbol download
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    tcol = "Datetime" if "Datetime" in df.columns else "Date"
    out = pd.DataFrame({
        "ts": pd.to_datetime(df[tcol], utc=True),
        "open": df["Open"].astype(float),
        "high": df["High"].astype(float),
        "low": df["Low"].astype(float),
        "close": df["Close"].astype(float),
        "volume": df["Volume"].astype(float),
    })
    out = out.dropna().drop_duplicates(subset="ts").reset_index(drop=True)
    out = out[(out["close"] > 0) & (out["high"] > 0) & (out["low"] > 0)].reset_index(drop=True)
    return out


def fetch(symbol, interval, period):
    import yfinance as yf
    try:
        raw = yf.download(symbol, interval=interval, period=period,
                          auto_adjust=True, progress=False, threads=False)
        return normalize(raw)
    except Exception as e:
        print(f"    fetch error {symbol} {interval}: {e}")
        return None


def run_pass(label, interval, period):
    print(f"\n############  {label}  (interval={interval}, period={period})  ############")
    for symbol in SYMBOLS:
        d0 = fetch(symbol, interval, period)
        print(f"================  {symbol}  ================")
        if d0 is None or len(d0) < 600:
            print(f"  not enough data ({0 if d0 is None else len(d0)} bars)\n")
            continue
        cut = int(len(d0) * TRAIN_FRAC)
        print(f"  bars={len(d0)}  span={d0['ts'].iloc[0].date()}..{d0['ts'].iloc[-1].date()}"
              f"  (train={cut}, test={len(d0)-cut})")
        print("  computing SMC structure once...", flush=True)
        bias = precompute_bias(d0)
        print(f"  {'variant':<22} {'TRAIN pf/exp/n':<24} {'TEST pf/exp/n':<24} verdict")
        for name, cfg in VARIANTS:
            d = compute_indicators(d0, cfg)
            tr = stats(replay(d.iloc[:cut], cfg, None, 0.0, bias=bias[:cut]))
            te = stats(replay(d.iloc[cut:], cfg, None, 0.0, bias=bias[cut:]))
            keep = te["profit_factor"] > 1.05 and te["trades"] >= 15 and te["expectancy_r"] > 0
            verdict = "EDGE? keep" if keep else "reject"
            tr_s = f"{tr['profit_factor']}/{tr['expectancy_r']}/{tr['trades']}"
            te_s = f"{te['profit_factor']}/{te['expectancy_r']}/{te['trades']}"
            print(f"  {name:<22} {tr_s:<24} {te_s:<24} {verdict}", flush=True)
        print()


def main():
    print("APEX STOCK + GOLD RESEARCH  -  out-of-sample validation (free data)")
    print("long-only, 2% risk. A variant counts only if TEST stays >1.05 PF, n>=15, exp>0.\n")
    # Intraday (closest to the crypto 1h test, but capped ~2yr by Yahoo)...
    run_pass("INTRADAY 1h", "60m", "730d")
    # ...and a deep DAILY pass for many years of history.
    run_pass("DAILY", "1d", "10y")
    print("\n===== STOCK RESEARCH DONE =====")
    print("Any 'EDGE? keep' is a LEAD only -> must clear walk-forward + costs next.")


if __name__ == "__main__":
    main()
