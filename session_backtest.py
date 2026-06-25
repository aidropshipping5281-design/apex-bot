"""Session-aware backtest with paginated (deep) history.

Crypto trades 24/7 - there are no real Asia/London/NY 'session closes' like forex.
These are just UTC time-of-day buckets that line up with when each region is awake
(and volume tends to cluster). This script:
  1. Pulls as much FREE history as the exchange allows (paginated, not just 720).
  2. Replays the live strategy (EMA-cross + MTF + RSI/MACD + SMC) bar by bar.
  3. Reports results OVERALL and broken down by session (Asia / London / New York).

Truth-filter: tiny trade counts mean nothing. We want hundreds of trades before
drawing any conclusion about an edge.
"""
import time
import pandas as pd

from apex.config import Config
from apex.strategy import compute_indicators, signal
from apex.risk import RiskManager

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
         "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

# Free, US-accessible exchanges to try in order (first with enough data wins).
# binanceus gives deep history; kraken is a fast reliable fallback (shallower).
EXCHANGES = ["binanceus", "kraken"]
EXCHANGE_TIMEOUT_MS = 8000          # hard cap so a slow/blocked API can't hang us
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAMES = ["1h"]                 # 1h only — 15m proved to be noise/losses
TARGET_CANDLES = 20000             # ~2.3 years of 1h for 100+ trades per session


def session_of(ts) -> str:
    """Map a UTC timestamp to a trading session (rough, non-overlapping)."""
    h = ts.hour
    if h >= 23 or h < 7:      # 23:00-06:59 UTC
        return "Asia"
    if 7 <= h < 13:           # 07:00-12:59 UTC
        return "London"
    return "NewYork"          # 13:00-22:59 UTC


def paginated_fetch(ex, symbol, timeframe, target=TARGET_CANDLES) -> pd.DataFrame:
    tf_ms = TF_MS[timeframe]
    now = ex.milliseconds()
    since = now - target * tf_ms
    rows = []
    while since < now:
        batch = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        last = batch[-1][0]
        if last <= since:                      # exchange isn't paginating back
            break
        since = last + tf_ms
        time.sleep((getattr(ex, "rateLimit", 200) or 200) / 1000.0)
        if len(rows) >= target * 1.5:
            break
    seen, out = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            out.append(r)
    out.sort(key=lambda r: r[0])
    df = pd.DataFrame(out, columns=["ts", "open", "high", "low", "close", "volume"])
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def replay(df, cfg):
    df = compute_indicators(df, cfg)
    equity = cfg.start_equity
    rm = RiskManager(cfg)
    pos, trades = None, []
    # Indicators are precomputed on the full df above, so a capped trailing window
    # keeps the EMA/RSI/MACD columns correct while keeping the SMC recompute O(1)
    # per bar (it only needs recent structure). This makes the whole replay O(n).
    LOOKBACK = 300
    for i in range(2, len(df)):
        window = df.iloc[max(0, i - LOOKBACK): i + 1]
        row = window.iloc[-1]
        price = row["close"]
        sig = signal(window, cfg)
        if pos:
            exit_px = reason = None
            if pos["direction"] == "long":
                if row["low"] <= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif row["high"] >= pos["take"]:
                    exit_px, reason = pos["take"], "take"
            else:
                if row["high"] >= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif row["low"] <= pos["take"]:
                    exit_px, reason = pos["take"], "take"
            if exit_px is None and sig == "flat":
                exit_px, reason = price, "signal"
            if exit_px is not None:
                pnl = ((exit_px - pos["entry"]) if pos["direction"] == "long"
                       else (pos["entry"] - exit_px)) * pos["size"]
                equity += pnl
                r = pnl / pos["risk_dollars"] if pos["risk_dollars"] else 0
                trades.append({"pnl": pnl, "r": r, "session": pos["session"]})
                pos = None
        if pos is None and sig in ("long", "short") and not rm.check_daily_halt(equity):
            plan = rm.plan_trade(equity, price, row["atr"], direction=sig)
            if plan:
                plan["session"] = session_of(row["ts"])
                pos = plan
    return trades, equity


def stats(trades):
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = (gp / gl) if gl else (float("inf") if gp else 0.0)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 3),
        "expectancy_r": round(sum(t["r"] for t in trades) / n, 3),
        "profit_factor": round(pf, 3) if pf != float("inf") else "inf",
        "net_pnl": round(sum(t["pnl"] for t in trades), 2),
    }


def main():
    import ccxt
    cfg = Config()
    cfg.start_equity, cfg.risk_pct, cfg.rr, cfg.atr_mult = 100.0, 0.02, 2.0, 1.5
    cfg.allow_short = False
    print("APEX SESSION BACKTEST  -  deep free history, long-only spot, 2% risk, 2:1")
    print("Sessions (UTC): Asia 23-07, London 07-13, NewYork 13-23\n")

    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            df, used = None, None
            for exid in EXCHANGES:
                try:
                    ex = getattr(ccxt, exid)({"enableRateLimit": True,
                                              "timeout": EXCHANGE_TIMEOUT_MS})
                    print(f"  [{symbol} {timeframe}] trying {exid}...", flush=True)
                    ex.load_markets()
                    sym = symbol if symbol in ex.markets else symbol.replace("/USDT", "/USD")
                    if sym not in ex.markets:
                        continue
                    d = paginated_fetch(ex, sym, timeframe)
                    if len(d) >= 300:
                        df, used = d, f"{exid}:{sym}"
                        break
                except Exception:
                    continue

            print(f"################  {symbol}  {timeframe}  ################")
            if df is None or len(df) < 300:
                print("  No sufficient free history from any exchange tried.\n")
                continue
            cfg.symbol, cfg.timeframe = symbol, timeframe
            trades, equity = replay(df, cfg)
            print(f"  data: {used}  candles={len(df)}  "
                  f"span={df['ts'].iloc[0].date()} .. {df['ts'].iloc[-1].date()}")
            print(f"  OVERALL : {stats(trades)}")
            for sess in ("Asia", "London", "NewYork"):
                print(f"   {sess:8}: {stats([t for t in trades if t['session'] == sess])}")
            print()
    print("===== SESSION BACKTEST DONE =====")


if __name__ == "__main__":
    main()
