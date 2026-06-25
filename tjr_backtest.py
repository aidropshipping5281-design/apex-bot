"""Systematic backtest of TJR's liquidity-sweep / break-of-structure strategy.

WHAT THIS IS
  TJR's video is discretionary (ICT/SMC: "liquidity sweeps, fair value gaps,
  break of structure"). You can't backtest hand-waving, so this codifies the
  OBJECTIVE core of the setup into fixed rules and tests them over hundreds of
  intraday bars with costs — turning "a guy on YouTube said" into data.

OBJECTIVE RULES (one timeframe, RTH only — a faithful simplification)
  1. LIQUIDITY SWEEP: price pokes through recent liquidity then reclaims it.
       - sell-side sweep (LONG bias): bar's low < lowest low of last LB bars AND
         it closes back ABOVE that level (stop-run below, then reclaim).
       - buy-side sweep (SHORT bias): bar's high > highest high of last LB bars
         AND it closes back BELOW that level.
  2. BREAK OF STRUCTURE (confirmation): within the next MAXBOS bars, a candle
       CLOSES beyond the sweep bar (above the sweep high for longs / below the
       sweep low for shorts) = the reversal is confirmed. Enter at that close.
  3. STOP = the sweep extreme (the wick that ran the liquidity).
  4. TARGET = the OPPOSING liquidity (the recent high for longs / low for shorts)
       — exactly TJR's "exit at the other draw on liquidity."
  Trades are intraday only (closed at session end). Realistic costs modeled.

  Also tests TJR's INDEX-ALIGNMENT filter: only keep a trade if the partner index
  (QQQ for SPY, NQ for ES) fires the SAME-direction setup within a few bars.

HONEST LIMITS
  - This captures the SPIRIT, not TJR's exact discretion (which FVG/swing he'd
    pick). If it fails, it doesn't "disprove TJR" — it shows the objective rules
    have no edge. If it works, that's real, transferable signal.
  - Free intraday data = only ~60 days of 5m bars (recent regime only). Suggestive,
    not definitive. Research-only; no live, no keys.
"""
import numpy as np
import pandas as pd

LB = 24          # lookback bars defining "recent liquidity" (~2h on 5m)
MAXBOS = 8       # confirm break-of-structure within this many bars after a sweep
FEE = 0.0001     # 1 bp per side
SLIP = 0.0002    # 2 bp slippage on market fills (entry + stop)
ALIGN_BARS = 3   # partner index must fire same-direction within +/- this many bars

PAIRS = [("SPY", "QQQ"), ("ES=F", "NQ=F")]


def fetch_5m(ticker):
    import yfinance as yf
    from stock_research import normalize
    try:
        raw = yf.download(ticker, interval="5m", period="60d",
                          auto_adjust=True, progress=False, threads=False)
        d = normalize(raw)
    except Exception as e:
        print(f"  fetch error {ticker}: {e}")
        return None
    if d is None or d.empty:
        return None
    et = pd.to_datetime(d["ts"]).dt.tz_convert("America/New_York")
    mins = et.dt.hour * 60 + et.dt.minute
    d = d[(mins >= 9 * 60 + 30) & (mins < 16 * 60)].reset_index(drop=True)
    d["day"] = pd.to_datetime(d["ts"]).dt.tz_convert("America/New_York").dt.normalize()
    return d


def detect(df):
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    day = df["day"].values
    n = len(df)
    trades = []
    i = LB
    while i < n - 1:
        if day[i - LB] != day[i]:            # liquidity window must be same session
            i += 1; continue
        rh = high[i - LB:i].max()
        rl = low[i - LB:i].min()
        took = None
        if low[i] < rl and close[i] > rl:
            took = "long"
        elif high[i] > rh and close[i] < rh:
            took = "short"
        if took:
            hit = False
            for j in range(i + 1, min(i + 1 + MAXBOS, n)):
                if day[j] != day[i]:
                    break
                if took == "long" and close[j] > high[i]:
                    entry, stop, target = close[j], low[i], rh
                    if entry > stop and target > entry:
                        trades.append({"dir": "long", "e": j, "entry": entry,
                                       "stop": stop, "target": target, "ts": df["ts"].values[j]})
                    i = j; hit = True; break
                if took == "short" and close[j] < low[i]:
                    entry, stop, target = close[j], high[i], rl
                    if entry < stop and target < entry:
                        trades.append({"dir": "short", "e": j, "entry": entry,
                                       "stop": stop, "target": target, "ts": df["ts"].values[j]})
                    i = j; hit = True; break
            if not hit:
                i += 1
        else:
            i += 1
    return trades


def simulate(df, trades):
    high, low, close = df["high"].values, df["low"].values, df["close"].values
    day = df["day"].values
    n = len(df)
    rs = []
    for t in trades:
        e, entry, stop, target, d = t["e"], t["entry"], t["stop"], t["target"], t["dir"]
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        entry_fill = entry * (1 + SLIP) if d == "long" else entry * (1 - SLIP)
        exit_px = None
        k = e
        for k in range(e + 1, n):
            if day[k] != day[e]:
                exit_px = close[k - 1] * (1 - SLIP if d == "long" else 1 + SLIP)
                break
            if d == "long":
                if low[k] <= stop:
                    exit_px = stop * (1 - SLIP); break
                if high[k] >= target:
                    exit_px = target; break
            else:
                if high[k] >= stop:
                    exit_px = stop * (1 + SLIP); break
                if low[k] <= target:
                    exit_px = target; break
        if exit_px is None:
            exit_px = close[min(k, n - 1)] * (1 - SLIP if d == "long" else 1 + SLIP)
        gross = (exit_px - entry_fill) if d == "long" else (entry_fill - exit_px)
        pnl = gross - FEE * (entry_fill + exit_px)
        rs.append(pnl / risk)
    return rs


def align_filter(trades_a, trades_b):
    """Keep trades in A that have a same-direction B trade within ALIGN_BARS*5min."""
    tol = np.timedelta64(ALIGN_BARS * 5, "m")
    b_by_dir = {"long": [], "short": []}
    for t in trades_b:
        b_by_dir[t["dir"]].append(np.datetime64(t["ts"]))
    out = []
    for t in trades_a:
        ts = np.datetime64(t["ts"])
        if any(abs(ts - bt) <= tol for bt in b_by_dir[t["dir"]]):
            out.append(t)
    return out


def stats(rs):
    n = len(rs)
    if n == 0:
        return None
    rs = np.array(rs)
    wins = rs[rs > 0]; losses = rs[rs <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    return {"n": n, "win_rate": len(wins) / n, "avg_win": wins.mean() if len(wins) else 0,
            "avg_loss": losses.mean() if len(losses) else 0,
            "expectancy": rs.mean(), "pf": pf}


def show(label, rs):
    s = stats(rs)
    if not s:
        print(f"  {label:<26} no trades"); return
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    print(f"  {label:<26} n={s['n']:<4} win={s['win_rate']:.0%}  "
          f"avgWin={s['avg_win']:+.2f}R avgLoss={s['avg_loss']:+.2f}R  "
          f"exp={s['expectancy']:+.2f}R  PF={pf}")


def main():
    print("APEX — SYSTEMATIC TJR BACKTEST (liquidity sweep -> BOS reversal, 5m RTH, costs)")
    print(f"Rules: sweep last {LB} bars, confirm BOS within {MAXBOS} bars, stop=sweep, target=opposing liquidity.")
    print("Compare to TJR's claimed ~64% win / ~1.23 avg R:R.\n")
    for a, b in PAIRS:
        da, db = fetch_5m(a), fetch_5m(b)
        print(f"================  {a} / {b}  ================")
        if da is None or db is None or len(da) < 200 or len(db) < 200:
            print("  not enough intraday data\n"); continue
        ta, tb = detect(da), detect(db)
        print(f"  data: {a} {len(da)} bars / {b} {len(db)} bars  "
              f"span {da['day'].min().date()}..{da['day'].max().date()}")
        show(f"{a} standalone", simulate(da, ta))
        show(f"{b} standalone", simulate(db, tb))
        show(f"{a} (aligned w/ {b})", simulate(da, align_filter(ta, tb)))
        show(f"{b} (aligned w/ {a})", simulate(db, align_filter(tb, ta)))
        print()
    print("===== TJR BACKTEST DONE =====")
    print("Edge if: expectancy clearly > 0 AND win/R:R near his claim AND alignment helps.")
    print("Reminder: objective simplification of a discretionary method; ~60d sample only.")


if __name__ == "__main__":
    main()
