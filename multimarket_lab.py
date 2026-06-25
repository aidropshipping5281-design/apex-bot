"""MULTI-MARKET LAB — apply the right strategy to EVERY market, then validate.

The goal: one bot trading crypto, stocks, gold, forex, and futures. The method is
the same one that proved out on BTC — match the strategy to the market, then only
keep what survives OUT-OF-SAMPLE with realistic costs.

Strategy per market (from the research):
  CRYPTO  / GOLD / FUTURES  -> TREND-FOLLOWING (TSMOM): the durable cross-asset
                               edge (Moskowitz-Ooi-Pedersen; AQR). Trends persist.
  STOCKS  / FOREX           -> MEAN-REVERSION (RSI-2 inside a long-term uptrend):
                               equities/FX revert short-term; we also run trend as
                               a control to see which actually wins per name.

Data: ALL free via yfinance — crypto (*-USD), stocks, gold (GLD + GC=F),
forex (EURUSD=X etc.), futures (ES=F, NQ=F, CL=F, GC=F). Daily bars, deep history.
Costs always modeled. A market/instrument "graduates" only if it survives OOS.

Options are handled separately (options_overlay): they trade DIRECTIONALLY off a
validated underlying signal here — you don't get a free options-price backtest, so
the edge must come from the underlying, with options adding defined-risk leverage.
"""
from research_harness import stats
from strategy_lab import fetch_daily, indicators, backtest

TRAIN_FRAC = 0.70

# market -> (tickers, preferred strategy from research)
MARKETS = {
    "CRYPTO":  (["BTC-USD", "ETH-USD", "SOL-USD"],            "tsmom"),
    "STOCKS":  (["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "AMD"], "meanrev"),
    "GOLD":    (["GLD", "GC=F"],                               "tsmom"),
    "FOREX":   (["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X"], "meanrev"),
    "FUTURES": (["ES=F", "NQ=F", "CL=F", "GC=F"],              "tsmom"),
}
KIND_LABEL = {"tsmom": "TREND", "meanrev": "MEAN-REV"}


def evaluate(d):
    cut = int(len(d) * TRAIN_FRAC)
    out = {}
    for kind in ("tsmom", "meanrev"):
        tr = stats(backtest(d.iloc[:cut], kind))
        te = stats(backtest(d.iloc[cut:], kind))
        keep = te["profit_factor"] > 1.1 and te["expectancy_r"] > 0 and te["trades"] >= 10
        out[kind] = (tr, te, keep)
    return out


def main():
    print("APEX MULTI-MARKET LAB  -  every market, matched strategy, OOS + costs\n")
    survivors = []
    for market, (tickers, preferred) in MARKETS.items():
        print(f"################  {market}   (research pick: {KIND_LABEL[preferred]})  ################")
        for t in tickers:
            d0 = fetch_daily(t)
            if d0 is None or len(d0) < 400:
                print(f"  {t:<10} no/short data")
                continue
            d = indicators(d0)
            res = evaluate(d)
            print(f"  {t:<10} span {d['ts'].iloc[0].date()}..{d['ts'].iloc[-1].date()}")
            for kind in ("tsmom", "meanrev"):
                tr, te, keep = res[kind]
                star = " <= research pick" if kind == preferred else ""
                tag = "EDGE? keep" if keep else "reject"
                te_s = f"PF {te['profit_factor']} / {te['expectancy_r']}R / n{te['trades']}"
                print(f"       {KIND_LABEL[kind]:<9} TEST {te_s:<28} {tag}{star}")
                if keep:
                    survivors.append((market, t, KIND_LABEL[kind], te["profit_factor"], te["expectancy_r"], te["trades"]))
            print()
        print(flush=True)

    print("==================  SURVIVORS (OOS edge after costs)  ==================")
    if not survivors:
        print("  none cleared the bar.")
    else:
        survivors.sort(key=lambda r: r[4], reverse=True)
        for m, t, k, pf, exp, n in survivors:
            print(f"  {m:<8} {t:<10} {k:<9} PF {pf:<6} exp {exp:<7} n {n}")
    print("\n===== MULTI-MARKET LAB DONE =====")
    print("Each survivor is a per-market LEAD -> confirm with walk-forward + bear test,")
    print("then paper, then live with tiny size. Same gate as BTC. No survivor = no trade.")


if __name__ == "__main__":
    main()
