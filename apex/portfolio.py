"""Multi-position portfolio: trade several symbols at once with shared capital
and global risk caps (max concurrent positions + max total risk on the book)."""
import logging, time
from .strategy import compute_indicators, signal
from .risk import RiskManager
from .notifier import Notifier
from .journal import Journal
from .learn import PerformanceTracker

log = logging.getLogger("apex.portfolio")


class MultiPaperBroker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cash = cfg.start_equity
        self.positions = {}      # symbol -> dict(direction,size,entry,stop,take,risk)

    def equity(self, prices):
        eq = self.cash
        for sym, p in self.positions.items():
            px = prices.get(sym, p["entry"])
            eq += (p["size"] * px if p["direction"] == "long"
                   else p["size"] * (p["entry"] - px) + p["size"] * p["entry"])
        return eq

    def has(self, sym):
        return sym in self.positions

    def count(self):
        return len(self.positions)

    def open_risk(self):
        return sum(p["risk"] for p in self.positions.values())

    def open(self, sym, direction, size, price, stop, take, risk):
        self.cash -= size * price
        self.positions[sym] = {"direction": direction, "size": size, "entry": price,
                               "stop": stop, "take": take, "risk": risk}

    def on_price(self, sym, price):
        p = self.positions.get(sym)
        if not p:
            return None
        hit = None
        if p["direction"] == "long":
            if price <= p["stop"]:
                hit = ("stop", p["stop"])
            elif price >= p["take"]:
                hit = ("take", p["take"])
        else:
            if price >= p["stop"]:
                hit = ("stop", p["stop"])
            elif price <= p["take"]:
                hit = ("take", p["take"])
        return self.close(sym, hit[1], hit[0]) if hit else None

    def close(self, sym, price, reason):
        p = self.positions.pop(sym, None)
        if not p:
            return None
        if p["direction"] == "short":
            pnl = (p["entry"] - price) * p["size"]
            self.cash += p["size"] * p["entry"] + pnl
        else:
            pnl = (price - p["entry"]) * p["size"]
            self.cash += p["size"] * price
        return {"symbol": sym, "exit": price, "pnl": pnl, "reason": reason, **p}


class PortfolioEngine:
    def __init__(self, cfg, broker=None):
        self.cfg = cfg
        self.symbols = [s.strip() for s in cfg.symbols.split(",") if s.strip()]
        self.broker = broker or MultiPaperBroker(cfg)
        self.rm = RiskManager(cfg)
        self.notify = Notifier(cfg)
        self.journal = Journal()
        self.tracker = PerformanceTracker()
        self.closed = []
        self.paused = False

    def step(self, dfs):
        """dfs: {symbol: dataframe}. Process one cycle across all symbols."""
        prices = {s: float(d["close"].iloc[-1]) for s, d in dfs.items()}
        # 1) manage open positions
        for sym in list(self.broker.positions):
            if sym not in dfs:
                continue
            di = compute_indicators(dfs[sym], self.cfg)
            res = self.broker.on_price(sym, prices[sym])
            if res is None and signal(di, self.cfg) == "flat":
                res = self.broker.close(sym, prices[sym], "signal")
            if res:
                self._on_close(res, prices)
        # 2) daily halt
        equity = self.broker.equity(prices)
        if self.rm.check_daily_halt(equity):
            return {"action": "halted_daily", "equity": equity}
        # 3) look for new entries within global caps
        opened = []
        for sym in self.symbols:
            if self.broker.has(sym) or self.broker.count() >= self.cfg.max_positions:
                continue
            if self.paused or sym not in dfs:
                continue
            di = compute_indicators(dfs[sym], self.cfg)
            sig = signal(di, self.cfg)
            if sig not in ("long", "short"):
                continue
            risk_budget = equity * self.cfg.risk_pct
            if self.broker.open_risk() + risk_budget > equity * self.cfg.max_total_risk_pct:
                continue                              # would exceed total-risk cap
            plan = self.rm.plan_trade(equity, prices[sym], float(di["atr"].iloc[-1]),
                                      direction=sig, risk_budget=risk_budget)
            if not plan:
                continue
            self.broker.open(sym, sig, plan["size"], prices[sym],
                             plan["stop"], plan["take"], plan["risk_dollars"])
            opened.append((sym, sig))
            self.notify.send(
                f"APEX ▶ {sig.upper()} {sym} @ {prices[sym]:.2f} | stop {plan['stop']:.2f} "
                f"| tp {plan['take']:.2f} | risk ${plan['risk_dollars']:.2f} "
                f"| open {self.broker.count()}/{self.cfg.max_positions} | eq ${equity:.2f}")
        return {"action": "cycle", "equity": equity, "opened": opened,
                "open_positions": self.broker.count()}

    def _on_close(self, res, prices):
        risk = res.get("risk") or 1
        r = res["pnl"] / risk if risk else 0
        self.closed.append({"pnl": res["pnl"], "r": r})
        self.tracker.update({"pnl": res["pnl"], "r": r})
        equity = self.broker.equity(prices)
        self.journal.record(symbol=res["symbol"], side=res["direction"],
                            entry=round(res["entry"], 2), stop=round(res["stop"], 2),
                            take=round(res["take"], 2), exit=round(res["exit"], 2),
                            size=round(res["size"], 6), pnl=round(res["pnl"], 2),
                            r_multiple=round(r, 2), reason=res["reason"],
                            equity_after=round(equity, 2))
        st = self.tracker.stats()
        self.notify.send(f"APEX ■ CLOSE {res['symbol']} {res['direction'].upper()} "
                         f"{res['reason'].upper()} @ {res['exit']:.2f} | P/L ${res['pnl']:.2f} "
                         f"({r:+.2f}R) | eq ${equity:.2f} | rolling exp {st['expectancy_r']:+.2f}R")

    def run(self, max_cycles=None):
        from .exchange import MarketData
        feeds = {}
        for s in self.symbols:
            c = type(self.cfg)()
            c.__dict__.update(self.cfg.__dict__)
            c.symbol = s
            feeds[s] = MarketData(c)
        self.notify.send(f"APEX portfolio online — {len(self.symbols)} symbols, "
                         f"max {self.cfg.max_positions} positions. Survival first.")
        n = 0
        while max_cycles is None or n < max_cycles:
            try:
                dfs = {s: feeds[s].fetch(limit=200) for s in self.symbols}
                self.step(dfs)
            except Exception as e:
                log.exception("portfolio cycle error: %s", e)
            n += 1
            if max_cycles is None or n < max_cycles:
                time.sleep(self.cfg.poll_seconds)
