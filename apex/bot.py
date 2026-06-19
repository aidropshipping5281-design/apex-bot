"""Apex Alpha main loop. Ties strategy + risk + execution + alerts + learning."""
import json, logging, os, time
from .config import Config
from .exchange import MarketData, PaperBroker, CcxtTrader, WebullTrader
from .strategy import compute_indicators, signal
from .risk import RiskManager
from .notifier import Notifier
from .journal import Journal
from .learn import PerformanceTracker, AutoTuner

log = logging.getLogger("apex.bot")
STATE_FILE = "apex_state.json"


def build_trader(cfg):
    if cfg.mode == "paper":
        return PaperBroker(cfg)
    if cfg.exchange == "webull":
        return WebullTrader(cfg)
    return CcxtTrader(cfg)


class Bot:
    def __init__(self, cfg=None, market=None, trader=None):
        self.cfg = cfg or Config()
        errs = self.cfg.validate()
        if errs:
            raise SystemExit("Config errors: " + "; ".join(errs))
        self.market = market or MarketData(self.cfg)
        self.trader = trader or build_trader(self.cfg)
        self.rm = RiskManager(self.cfg)
        self.notify = Notifier(self.cfg)
        self.journal = Journal()
        self.tracker = PerformanceTracker()
        self.tuner = AutoTuner(self.cfg)
        self.closed = []
        self.paused = False
        self._load_state()

    # ---- persistence ----
    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                s = json.load(open(STATE_FILE))
                self.cfg.ema_fast = s.get("ema_fast", self.cfg.ema_fast)
                self.cfg.ema_slow = s.get("ema_slow", self.cfg.ema_slow)
                self.paused = s.get("paused", False)
            except Exception:
                pass

    def _save_state(self, price):
        try:
            json.dump({"ema_fast": self.cfg.ema_fast, "ema_slow": self.cfg.ema_slow,
                       "paused": self.paused, "equity": self.trader.equity(price)},
                      open(STATE_FILE, "w"))
        except Exception:
            pass

    # ---- one iteration (separated so tests can step it) ----
    def step(self, df):
        df = compute_indicators(df, self.cfg)
        price = float(df["close"].iloc[-1])
        atr_val = float(df["atr"].iloc[-1])

        # 1) manage an open position
        if self.trader.position_open:
            res = self.trader.on_price(price)                     # stop / take bracket
            if res is None and signal(df, self.cfg) == "flat":
                res = self.trader.close_market(price, "signal")   # trend flip exit
            if res:
                self._on_close(res, df, price)

        # 2) daily loss circuit breaker
        equity = self.trader.equity(price)
        if self.rm.check_daily_halt(equity):
            self._save_state(price)
            return {"action": "halted_daily", "equity": equity}

        # 3) new entry
        if not self.trader.position_open and not self.paused and signal(df, self.cfg) == "long":
            plan = self.rm.plan_trade(equity, price, atr_val)
            if plan:
                self.trader.buy(plan["size"], price, plan["stop"], plan["take"])
                self.notify.send(
                    f"APEX ▶ LONG {self.cfg.symbol} @ {price:.2f}\n"
                    f"stop {plan['stop']:.2f} | tp {plan['take']:.2f} | "
                    f"size {plan['size']:.6f} | risk ${plan['risk_dollars']:.2f} | equity ${equity:.2f}")
                self._save_state(price)
                return {"action": "entry", "price": price, "plan": plan}
        self._save_state(price)
        return {"action": "hold", "equity": equity}

    def _on_close(self, res, df, price):
        risk_dollars = (res["entry"] - res["stop"]) * res["size"]
        r = res["pnl"] / risk_dollars if risk_dollars else 0
        trade = {"pnl": res["pnl"], "r": r}
        self.closed.append(trade)
        self.tracker.update(trade)
        equity = self.trader.equity(price)
        self.journal.record(symbol=self.cfg.symbol, side="long", entry=round(res["entry"], 2),
                            stop=round(res["stop"], 2), take=round(res["take"], 2),
                            exit=round(res["exit"], 2), size=round(res["size"], 6),
                            pnl=round(res["pnl"], 2), r_multiple=round(r, 2),
                            reason=res["reason"], equity_after=round(equity, 2))
        st = self.tracker.stats()
        self.notify.send(
            f"APEX ■ CLOSE {self.cfg.symbol} {res['reason'].upper()} @ {res['exit']:.2f}\n"
            f"P/L ${res['pnl']:.2f} ({r:+.2f}R) | equity ${equity:.2f}\n"
            f"rolling: {st['sample']} trades, win {st['win_rate']*100:.0f}%, exp {st['expectancy_r']:+.2f}R")
        # learning: adapt after each close
        decision = self.tuner.maybe_tune(df, self.closed)
        if decision:
            if decision["action"] == "retune" and decision["changed"]:
                self.paused = False
                self.notify.send(f"APEX ⟳ LEARN: new EMA params {decision['params']} "
                                 f"(OOS exp {decision['valid_expectancy_r']:+}R)")
            elif decision["action"] == "pause":
                self.paused = True
                self.notify.send("APEX ⏸ LEARN: no positive edge out-of-sample — "
                                 "pausing new entries until a valid config is found.")

    # ---- live loop ----
    def run(self, max_cycles=None):
        self.notify.send(f"APEX online — {self.cfg.mode.upper()} mode, {self.cfg.symbol} "
                         f"{self.cfg.timeframe}. Capital survival first.")
        n = 0
        while max_cycles is None or n < max_cycles:
            try:
                df = self.market.fetch(limit=200)
                self.step(df)
            except Exception as e:
                log.exception("cycle error: %s", e)
            n += 1
            if max_cycles is None or n < max_cycles:
                time.sleep(self.cfg.poll_seconds)
