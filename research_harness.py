"""Strategy research harness - search for a REAL edge, honestly.

Method: every variant is judged on OUT-OF-SAMPLE data.
  - Pull deep history, clean it (also fixes the ETH/SOL NaN-ATR bug that made
    positions open with NaN stops and never close -> only a few trades).
  - Split each series 70% TRAIN (older) / 30% TEST (newer, unseen).
  - SMC structure (the expensive part) is computed ONCE per symbol and reused
    across all variants for speed.
  - A variant only "counts" if it stays profitable on the TEST slice it was
    never tuned on. In-sample-only wins are treated as overfitting and rejected.
"""
import time
import numpy as np
import pandas as pd

from apex.config import Config
from apex.strategy import compute_indicators
from apex import smc
from apex.risk import RiskManager

EXCHANGES = ["binanceus", "kraken"]
EXCHANGE_TIMEOUT_MS = 8000
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAME = "1h"
TARGET_CANDLES = 16000
TRAIN_FRAC = 0.70
LOOKBACK = 300
TF_MS = {"1h": 3_600_000, "15m": 900_000, "4h": 14_400_000, "1d": 86_400_000}


def session_of(ts):
    h = ts.hour
    if h >= 23 or h < 7:
        return "Asia"
    if 7 <= h < 13:
        return "London"
    return "NewYork"


def paginated_fetch(ex, symbol, timeframe, target=TARGET_CANDLES):
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
        if last <= since:
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
    df = df.dropna().drop_duplicates(subset="ts").reset_index(drop=True)
    df = df[(df["close"] > 0) & (df["high"] > 0) & (df["low"] > 0)].reset_index(drop=True)
    return df


def precompute_bias(df):
    """SMC bias per bar over a LOOKBACK window. Depends ONLY on OHLC, so compute
    once per symbol and reuse across variants. This is the expensive part."""
    n = len(df)
    bias = [None] * n
    for i in range(2, n):
        w = df.iloc[max(0, i - LOOKBACK): i + 1]
        bias[i] = smc.confluence(w)["bias"] if len(w) >= 5 else "neutral"
    return bias


def signal_series(df, cfg, bias):
    """Vectorized reproduction of apex.strategy.signal (long-only) + precomputed bias."""
    ef = df["ema_fast"].values
    es = df["ema_slow"].values
    c = df["close"].values
    htf = df["htf_ema"].values
    macd = df["macd"].values
    sg = df["macd_sig"].values
    rsi = df["rsi"].values
    n = len(df)
    out = [None] * n
    for i in range(1, n):
        cu = ef[i - 1] <= es[i - 1] and ef[i] > es[i]
        cd = ef[i - 1] >= es[i - 1] and ef[i] < es[i]
        if cu and c[i] > htf[i] and macd[i] > sg[i] and rsi[i] < 70 and bias[i] != "bear":
            out[i] = "long"
        elif cd:
            out[i] = "flat"
    return out


def replay(df, cfg, allowed_sessions=None, htf_margin_atr=0.0, bias=None):
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
            exit_px = reason = None
            if pos["direction"] == "long":
                if low[i] <= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif high[i] >= pos["take"]:
                    exit_px, reason = pos["take"], "take"
            else:
                if high[i] >= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif low[i] <= pos["take"]:
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
        if pos is None and sig in ("long", "short") and pd.notna(atr_v) and atr_v > 0:
            tstamp = pd.Timestamp(ts[i])
            if allowed_sessions and session_of(tstamp) not in allowed_sessions:
                continue
            if htf_margin_atr > 0 and pd.notna(htf[i]):
                if abs(price - htf[i]) < htf_margin_atr * atr_v:
                    continue
            if rm.check_daily_halt(equity):
                continue
            plan = rm.plan_trade(equity, price, atr_v, direction=sig)
            if plan:
                plan["session"] = session_of(tstamp)
                pos = plan
    return trades


def stats(trades):
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0, "expectancy_r": 0, "profit_factor": 0, "net": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    gp = sum(t["pnl"] for t in wins)
    pf = (gp / gl) if gl else (float("inf") if gp else 0.0)
    return {"trades": n, "win_rate": round(len(wins) / n, 3),
            "expectancy_r": round(sum(t["r"] for t in trades) / n, 3),
            "profit_factor": round(pf, 3) if pf != float("inf") else 99.9,
            "net": round(sum(t["pnl"] for t in trades), 2)}


def make_cfg(ema_fast=9, ema_slow=21, atr_mult=1.5, rr=2.0):
    cfg = Config()
    cfg.start_equity, cfg.risk_pct, cfg.allow_short = 100.0, 0.02, False
    cfg.ema_fast, cfg.ema_slow, cfg.atr_mult, cfg.rr = ema_fast, ema_slow, atr_mult, rr
    return cfg


VARIANTS = [
    ("baseline 9/21 2:1",         make_cfg(),                          None,               0.0),
    ("EMA 20/50 2:1",             make_cfg(ema_fast=20, ema_slow=50),  None,               0.0),
    ("skip NewYork",              make_cfg(),                          {"Asia", "London"}, 0.0),
    ("RR 3:1",                    make_cfg(rr=3.0),                    None,               0.0),
    ("trend-strength htf>0.5ATR", make_cfg(),                          None,               0.5),
]


def main():
    import ccxt
    print("APEX STRATEGY RESEARCH  -  out-of-sample validation (70% train / 30% test)")
    print(f"{TIMEFRAME}, long-only spot, 2% risk. A variant only counts if TEST stays >1.0 PF.\n")
    for symbol in SYMBOLS:
        raw, src = None, None
        for exid in EXCHANGES:
            try:
                ex = getattr(ccxt, exid)({"enableRateLimit": True, "timeout": EXCHANGE_TIMEOUT_MS})
                ex.load_markets()
                sym = symbol if symbol in ex.markets else symbol.replace("/USDT", "/USD")
                if sym not in ex.markets:
                    continue
                d = paginated_fetch(ex, sym, TIMEFRAME)
                if len(d) >= 1000:
                    raw, src = d, f"{exid}:{sym}"
                    break
            except Exception:
                continue
        print(f"================  {symbol}  ================")
        if raw is None:
            print("  no data\n")
            continue
        cut = int(len(raw) * TRAIN_FRAC)
        print(f"  {src}  candles={len(raw)}  span={raw['ts'].iloc[0].date()}..{raw['ts'].iloc[-1].date()}"
              f"  (train={cut}, test={len(raw)-cut})")
        print("  computing SMC structure once (shared across variants)...", flush=True)
        bias = precompute_bias(raw)
        print(f"  {'variant':<26} {'TRAIN pf/exp/n':<24} {'TEST pf/exp/n':<24} verdict")
        for name, cfg, sess, margin in VARIANTS:
            d = compute_indicators(raw, cfg)
            tr = stats(replay(d.iloc[:cut], cfg, sess, margin, bias=bias[:cut]))
            te = stats(replay(d.iloc[cut:], cfg, sess, margin, bias=bias[cut:]))
            keep = te["profit_factor"] > 1.05 and te["trades"] >= 15 and te["expectancy_r"] > 0
            verdict = "EDGE? keep" if keep else "reject"
            tr_s = f"{tr['profit_factor']}/{tr['expectancy_r']}/{tr['trades']}"
            te_s = f"{te['profit_factor']}/{te['expectancy_r']}/{te['trades']}"
            print(f"  {name:<26} {tr_s:<24} {te_s:<24} {verdict}", flush=True)
        print()
    print("===== RESEARCH DONE =====")


if __name__ == "__main__":
    main()
