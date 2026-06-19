"""The learning loop — honest version.

Two parts:
1. PerformanceTracker: updates rolling stats after EVERY trade (your
   'learn with each trade'). Knows the live edge in real time.
2. AutoTuner: periodically re-optimises strategy params using a
   train/validation split on recent history, and PAUSES the strategy
   if it can't find a positive-expectancy configuration out-of-sample.

Guardrails matter: the tuner validates on data it did NOT optimise on,
and requires a minimum sample, so it adapts without curve-fitting noise.
Learning improves the odds; it does not guarantee profit.
"""
import logging
from collections import deque
from .backtest import grid_search, backtest

log = logging.getLogger("apex.learn")


class PerformanceTracker:
    def __init__(self, window=50):
        self.rs = deque(maxlen=window)
        self.all_pnl = 0.0
        self.n = 0
        self.wins = 0

    def update(self, trade):
        self.rs.append(trade["r"])
        self.all_pnl += trade["pnl"]
        self.n += 1
        if trade["pnl"] > 0:
            self.wins += 1

    def stats(self):
        n = len(self.rs)
        exp = sum(self.rs) / n if n else 0
        wr = self.wins / self.n if self.n else 0
        return {"sample": n, "expectancy_r": exp, "win_rate": wr, "net_pnl": self.all_pnl}


class AutoTuner:
    def __init__(self, cfg, every=10, min_sample=5):
        self.cfg = cfg
        self.every = every          # re-tune after this many closed trades
        self.min_sample = min_sample
        self._since = 0

    def maybe_tune(self, df, closed_trades):
        """Call after each closed trade. Returns a decision dict or None."""
        self._since += 1
        if self._since < self.every or len(df) < 100:
            return None
        self._since = 0
        split = int(len(df) * 0.6)
        train, valid = df.iloc[:split], df.iloc[split:]
        ranked = grid_search(train, self.cfg)          # optimise on TRAIN
        for (f, s), _ in ranked[:4]:                    # validate top candidates OOS
            c = _clone(self.cfg, f, s)
            v = backtest(valid, c)
            if v["trades"] >= self.min_sample and v["expectancy_r"] > 0:
                changed = (f, s) != (self.cfg.ema_fast, self.cfg.ema_slow)
                self.cfg.ema_fast, self.cfg.ema_slow = f, s
                return {"action": "retune", "params": (f, s), "changed": changed,
                        "valid_expectancy_r": round(v["expectancy_r"], 3)}
        return {"action": "pause", "reason": "no positive-expectancy config out-of-sample"}


def _clone(cfg, f, s):
    import copy
    c = copy.copy(cfg)
    c.ema_fast, c.ema_slow = f, s
    return c
