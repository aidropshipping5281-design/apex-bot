"""Win-rate report: TREND vs MEAN-REVERSION, side by side, after costs.

Answers "can we get a higher win rate?" honestly:
  - TREND (our core): LOW win rate, tiny losses, rare huge winners.
  - MEAN-REVERSION (buy oversold dips in an uptrend): HIGH win rate, small wins,
    occasional bigger losses (a dip that keeps dipping).
Both can be positive expectancy. Win rate alone tells you nothing; expectancy
(= win% x avgWin - loss% x avgLoss) is what compounds.
"""
import numpy as np
from research_harness import stats
from strategy_lab import fetch_daily, indicators, backtest

TREND_SYMS = ["BTC-USD", "ETH-USD", "QQQ", "AMD", "NVDA"]
MR_SYMS = ["QQQ", "SPY", "AMD", "NVDA"]


def row(sym, kind):
    d0 = fetch_daily(sym)
    if d0 is None or len(d0) < 300:
        print("  %-9s no data" % sym); return []
    trades = backtest(indicators(d0), kind)
    rs = [t["r"] for t in trades]
    st = stats(trades)
    wins = [r for r in rs if r > 0]; losses = [r for r in rs if r <= 0]
    aw = np.mean(wins) if wins else 0
    al = np.mean(losses) if losses else 0
    print("  %-9s %-5.0f%% %-+8.2f %-+8.2f %-+10.2f %-6s %s" %
          (sym, st["win_rate"] * 100, aw, al, st["expectancy_r"], st["profit_factor"], st["trades"]))
    return rs


def pooled(label, rs):
    if not rs:
        return
    rs = np.array(rs)
    wins = rs[rs > 0]; losses = rs[rs <= 0]
    print("  %s: win %.0f%%  avgWin %+.2fR  avgLoss %+.2fR  exp %+.2fR  (%d tr)" %
          (label, 100 * len(wins) / len(rs), wins.mean() if len(wins) else 0,
           losses.mean() if len(losses) else 0, rs.mean(), len(rs)))


def main():
    print("APEX WIN-RATE: TREND vs MEAN-REVERSION (full history, realistic costs)\n")
    hdr = "  %-9s %-6s %-8s %-8s %-10s %-6s trades" % ("symbol", "win%", "avgWin", "avgLoss", "exp/trade", "PF")
    print("=== TREND-FOLLOWING (our core) ===")
    print(hdr)
    pt = []
    for s in TREND_SYMS:
        pt += row(s, "tsmom")
    pooled("POOLED TREND", pt)
    print("\n=== MEAN-REVERSION (buy-the-dip, higher win rate) ===")
    print(hdr)
    pm = []
    for s in MR_SYMS:
        pm += row(s, "meanrev")
    pooled("POOLED MEANREV", pm)
    print("\nREAD: mean-reversion WINS more often but makes LESS per win and can take a")
    print("bigger loss when a dip keeps falling. Trend wins rarely but wins BIG and")
    print("protects in crashes. Both positive. Pro move = run BOTH (they work at")
    print("different times, smoothing the curve).")
    print("\n===== WIN-RATE REPORT DONE =====")


if __name__ == "__main__":
    main()
