"""Evidence-based, market-matched strategy lab  (research -> applied).

WHY A NEW LAB
  Our one strategy (intraday EMA-cross + SMC, tight ATR stop) lost to friction:
  on 1h the per-trade cost (~0.29R) exceeds the per-trade edge (~0.1R). The
  research literature says two things clearly:

   1. The most DURABLE, cross-asset edge is TIME-SERIES MOMENTUM / trend-following
      at LOW frequency (Moskowitz-Ooi-Pedersen 2012; AQR 'Trends Everywhere':
      ~1.0 Sharpe pre-cost across 58-67 futures, 1880-2016). Low frequency =
      tiny cost drag. This fits crypto, gold, and index trends.
   2. EQUITIES/ETFs mean-REVERT short term: buy overs" (RSI-2 low) WITHIN a
      long-term uptrend (price > 200d MA). >60% win rate in studies. The opposite
      of what we were doing to stocks.

  So we stop applying one strategy to everything. We test the RIGHT family per
  market, at DAILY frequency (where costs barely matter), with honest OOS + costs.

STRATEGIES
  TSMOM (trend):     long when close > SMA(trend_ma) AND mom(mom_lookback) > 0;
                     exit when close < SMA(trend_ma). Catastrophic stop at
                     stop_mult*ATR. Low turnover.
  MEANREV (revert):  long when close > SMA(200) (uptrend) AND RSI(2) < rsi_buy;
                     exit when RSI(2) > rsi_exit OR close < SMA(200). Short holds.

DATA
  All DAILY, free via yfinance (stocks, gold GLD, AND crypto via *-USD tickers) —
  one unified source, years of history. Realistic costs ALWAYS modeled.

  Research-only. No live, no keys. A winner here is a LEAD to walk-forward next.
"""
import numpy as np
import pandas as pd

from research_harness import stats
from stock_research import normalize  # yfinance frame -> ohlcv

# market -> tickers (yfinance). Crypto via -USD, gold via GLD ETF.
MARKETS = {
    "CRYPTO":   ["BTC-USD", "ETH-USD", "SOL-USD"],
    "EQUITIES": ["SPY", "QQQ", "AAPL", "NVDA"],
    "GOLD":     ["GLD"],
}
TRAIN_FRAC = 0.70
FEE, SLIP = 0.0010, 0.0005      # realistic per side (daily turnover is low)
RISK_PCT = 0.02
START_EQ = 100.0


def indicators(df):
    c = df["close"]
    df = df.copy()
    df["sma200"] = c.rolling(200).mean()
    df["sma100"] = c.rolling(100).mean()
    df["mom90"] = c / c.shift(90) - 1.0
    # ATR(14) via simple true range
    h, l, pc = df["high"], df["low"], c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    # RSI(2)
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1/2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/2, adjust=False).mean()
    df["rsi2"] = 100 - 100 / (1 + up / dn.replace(0, 1e-9))
    return df


def tsmom_signal(row, trend_col="sma100"):
    if np.isnan(row[trend_col]) or np.isnan(row["mom90"]):
        return None
    if row["close"] > row[trend_col] and row["mom90"] > 0:
        return "long"
    if row["close"] < row[trend_col]:
        return "flat"
    return None


def backtest(df, kind, stop_mult=3.0, rsi_buy=10, rsi_exit=60):
    """Daily long-only backtest with realistic costs. Returns trade list."""
    equity = START_EQ
    pos, trades = None, []
    rows = df.reset_index(drop=True)
    for i in range(1, len(rows)):
        r = rows.iloc[i]
        price, atr_v = r["close"], r["atr"]
        if np.isnan(atr_v) or atr_v <= 0:
            continue
        # ---- exits ----
        if pos:
            exit_px = None
            if r["low"] <= pos["stop"]:
                exit_px = pos["stop"] * (1 - SLIP)          # stop = market fill
            else:
                if kind == "tsmom":
                    flat = (not np.isnan(r["sma100"])) and price < r["sma100"]
                else:  # meanrev
                    flat = r["rsi2"] > rsi_exit or (not np.isnan(r["sma200"]) and price < r["sma200"])
                if flat:
                    exit_px = price * (1 - SLIP)             # signal exit = market
            if exit_px is not None:
                gross = (exit_px - pos["entry"]) * pos["size"]
                fees = FEE * pos["size"] * (pos["entry"] + exit_px)
                pnl = gross - fees
                equity += pnl
                trades.append({"pnl": pnl, "r": pnl / pos["risk"] if pos["risk"] else 0})
                pos = None
        # ---- entries ----
        if pos is None:
            if kind == "tsmom":
                sig = tsmom_signal(r) == "long"
            else:
                sig = (not np.isnan(r["sma200"]) and price > r["sma200"]
                       and not np.isnan(r["rsi2"]) and r["rsi2"] < rsi_buy)
            if sig:
                stop_dist = atr_v * stop_mult
                risk_dollars = equity * RISK_PCT
                size = risk_dollars / stop_dist
                entry_fill = price * (1 + SLIP)
                pos = {"entry": entry_fill, "stop": price - stop_dist,
                       "size": size, "risk": risk_dollars}
    return trades


def fetch_daily(ticker):
    import yfinance as yf
    try:
        raw = yf.download(ticker, interval="1d", period="10y",
                          auto_adjust=True, progress=False, threads=False)
        return normalize(raw)
    except Exception as e:
        print(f"    fetch error {ticker}: {e}")
        return None


# strategy spec per market (from the research): trend for crypto/gold, revert for equities.
PLAN = {
    "CRYPTO":   [("TSMOM trend", "tsmom"), ("MeanRev (control)", "meanrev")],
    "EQUITIES": [("MeanRev RSI2", "meanrev"), ("TSMOM (control)", "tsmom")],
    "GOLD":     [("TSMOM trend", "tsmom"), ("MeanRev (control)", "meanrev")],
}


def main():
    print("APEX STRATEGY LAB  -  evidence-based, market-matched (daily, OOS, costs)")
    print("Trend-following for crypto/gold, mean-reversion for equities. Realistic costs on.\n")
    for market, tickers in MARKETS.items():
        print(f"################  {market}  ################")
        for ticker in tickers:
            d0 = fetch_daily(ticker)
            if d0 is None or len(d0) < 400:
                print(f"  {ticker}: not enough data\n"); continue
            d = indicators(d0)
            cut = int(len(d) * TRAIN_FRAC)
            print(f"  --- {ticker}  bars={len(d)} span={d['ts'].iloc[0].date()}..{d['ts'].iloc[-1].date()} "
                  f"(train={cut}/test={len(d)-cut}) ---")
            print(f"      {'strategy':<20} {'TRAIN pf/exp/n':<22} {'TEST pf/exp/n':<22} verdict")
            for label, kind in PLAN[market]:
                tr = stats(backtest(d.iloc[:cut], kind))
                te = stats(backtest(d.iloc[cut:], kind))
                keep = te["profit_factor"] > 1.1 and te["expectancy_r"] > 0 and te["trades"] >= 12
                verdict = "EDGE? keep" if keep else "reject"
                ts = f"{tr['profit_factor']}/{tr['expectancy_r']}/{tr['trades']}"
                es = f"{te['profit_factor']}/{te['expectancy_r']}/{te['trades']}"
                print(f"      {label:<20} {ts:<22} {es:<22} {verdict}", flush=True)
            print()
    print("===== STRATEGY LAB DONE =====")
    print("Daily costs are tiny, so a 'keep' here is a REAL lead (unlike the 1h runs).")
    print("Confirm any keeper with multi-fold walk-forward before paper-trading.")


if __name__ == "__main__":
    main()
