"""Backtester: replay a strategy over historical candles and score it.
This is truth-filter #1 — most strategy ideas die here, by design."""
import copy
import pandas as pd
from .strategy import compute_indicators, signal
from .risk import RiskManager


def backtest(df, cfg):
    df = compute_indicators(df, cfg)
    equity = cfg.start_equity
    rm = RiskManager(cfg)
    pos = None
    trades = []
    peak = equity
    max_dd = 0.0
    for i in range(2, len(df)):
        window = df.iloc[: i + 1]
        row = window.iloc[-1]
        price = row["close"]
        sig = signal(window, cfg)
        if pos:                                   # manage open trade against this candle
            exit_px = reason = None
            if pos["direction"] == "long":
                if row["low"] <= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif row["high"] >= pos["take"]:
                    exit_px, reason = pos["take"], "take"
            else:                                 # short
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
                trades.append({"pnl": pnl, "r": r, "reason": reason})
                pos = None
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
        if pos is None and sig in ("long", "short") and not rm.check_daily_halt(equity):
            plan = rm.plan_trade(equity, price, row["atr"], direction=sig)
            if plan:
                pos = plan
    return _stats(trades, equity, cfg.start_equity, max_dd)


def _stats(trades, equity, start, max_dd):
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    exp_r = sum(t["r"] for t in trades) / n if n else 0
    pf = (gp / gl) if gl else (float("inf") if gp else 0)
    return {
        "trades": int(n),
        "win_rate": float(len(wins) / n) if n else 0.0,
        "expectancy_r": float(exp_r),
        "profit_factor": float(pf),
        "return_pct": float((equity - start) / start) if start else 0.0,
        "max_drawdown": float(max_dd),
        "end_equity": float(equity),
    }


def grid_search(df, cfg, fast_grid=(5, 8, 9, 12), slow_grid=(20, 21, 26, 34)):
    """Try EMA pairs, return them ranked by expectancy (out-of-sample aware
    when called on a validation slice). Overfitting guard lives in learn.py."""
    results = []
    for f in fast_grid:
        for s in slow_grid:
            if f >= s:
                continue
            c = copy.copy(cfg)
            c.ema_fast, c.ema_slow = f, s
            st = backtest(df, c)
            if st["trades"] >= 5:                 # ignore tiny samples
                results.append(((f, s), st))
    results.sort(key=lambda x: x[1]["expectancy_r"], reverse=True)
    return results
