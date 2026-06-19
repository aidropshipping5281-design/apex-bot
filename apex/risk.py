"""Position sizing and daily-loss circuit breaker."""
from datetime import datetime, timezone


class RiskManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self._day = None
        self._day_start_equity = None
        self.halted_today = False

    def _roll_day(self, equity):
        today = datetime.now(timezone.utc).date()
        if self._day != today:
            self._day = today
            self._day_start_equity = equity
            self.halted_today = False

    def check_daily_halt(self, equity):
        """Returns True if trading must stop for the day."""
        self._roll_day(equity)
        loss = self._day_start_equity - equity
        limit = self._day_start_equity * self.cfg.max_daily_loss_pct
        if loss >= limit:
            self.halted_today = True
        return self.halted_today

    def plan_trade(self, equity, entry, atr_value, direction="long", risk_budget=None):
        """Compute stop, take-profit, and size for long OR short. dict or None."""
        stop_dist = atr_value * self.cfg.atr_mult
        if stop_dist <= 0:
            return None
        if direction == "short":
            stop = entry + stop_dist
            take = entry - stop_dist * self.cfg.rr
        else:
            stop = entry - stop_dist
            take = entry + stop_dist * self.cfg.rr
        risk_dollars = (risk_budget if risk_budget is not None
                        else equity * self.cfg.risk_pct)
        size = risk_dollars / stop_dist
        notional = size * entry
        max_notional = equity * self.cfg.max_leverage
        if notional > max_notional:                 # cap to no-leverage limit
            size = max_notional / entry
            notional = size * entry
        if size <= 0:
            return None
        return {
            "entry": entry, "stop": stop, "take": take, "direction": direction,
            "size": size, "notional": notional,
            "risk_dollars": min(risk_dollars, stop_dist * size),
            "stop_dist": stop_dist,
        }
