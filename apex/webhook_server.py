"""TradingView -> Apex bridge.

Receives alert webhooks from a TradingView indicator (e.g. Tradevisor V2),
validates a shared secret, and converts each buy/sell into a RISK-MANAGED
paper trade through the existing engine. The indicator decides WHEN; Apex
still decides HOW MUCH and enforces the stop, the caps, and the journal.

TradingView alert message (JSON) should look like:
  {"secret":"YOUR_SECRET","action":"buy","symbol":"AAPL","price":{{close}}}
optional: "stop" and "tp" to override Apex's default stop placement.

Run:  python run.py webhook      (paper by default; never auto-sends live orders)
"""
import logging
from .config import Config
from .risk import RiskManager
from .portfolio import MultiPaperBroker
from .notifier import Notifier
from .journal import Journal
from .learn import PerformanceTracker

log = logging.getLogger("apex.webhook")


class TradingViewBridge:
    def __init__(self, cfg=None, broker=None):
        self.cfg = cfg or Config()
        self.broker = broker or MultiPaperBroker(self.cfg)
        self.rm = RiskManager(self.cfg)
        self.notify = Notifier(self.cfg)
        self.journal = Journal()
        self.tracker = PerformanceTracker()
        self.paused = False

    def handle(self, payload):
        action = str(payload.get("action", "")).lower()
        symbol = str(payload.get("symbol", self.cfg.symbol)).upper()
        try:
            price = float(payload.get("price"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "missing/invalid price"}
        if price <= 0:
            return {"ok": False, "error": "price must be > 0"}

        prices = {symbol: price}
        # exits first
        if action in ("sell", "close", "exit", "flat"):
            res = self.broker.close(symbol, price, "tv_signal") if self.broker.has(symbol) else None
            if res:
                self._on_close(res, prices)
                return {"ok": True, "did": "closed", "symbol": symbol}
            if not (self.cfg.allow_short and action == "sell"):
                return {"ok": True, "did": "noop (no open position)", "symbol": symbol}
            direction = "short"               # sell with nothing open + shorts allowed -> open short
        elif action in ("buy", "long"):
            direction = "long"
        elif action == "short" and self.cfg.allow_short:
            direction = "short"
        else:
            return {"ok": False, "error": f"unknown action '{action}'"}

        if self.paused:
            return {"ok": True, "did": "ignored (paused)", "symbol": symbol}
        if self.broker.has(symbol):
            return {"ok": True, "did": "already in position", "symbol": symbol}
        if self.broker.count() >= self.cfg.max_positions:
            return {"ok": True, "did": "skipped (max positions)", "symbol": symbol}

        equity = self.broker.equity(prices)
        risk_budget = equity * self.cfg.risk_pct
        if self.broker.open_risk() + risk_budget > equity * self.cfg.max_total_risk_pct:
            return {"ok": True, "did": "skipped (total-risk cap)", "symbol": symbol}

        # stop: use payload stop if given, else a default % of price
        stop = payload.get("stop")
        if stop:
            stop = float(stop); stop_dist = abs(price - stop)
        else:
            stop_dist = price * self.cfg.webhook_stop_pct
            stop = price - stop_dist if direction == "long" else price + stop_dist
        if stop_dist <= 0:
            return {"ok": False, "error": "stop distance is zero"}
        take = payload.get("tp")
        take = float(take) if take else (
            price + stop_dist * self.cfg.rr if direction == "long"
            else price - stop_dist * self.cfg.rr)
        size = risk_budget / stop_dist

        self.broker.open(symbol, direction, size, price, stop, take, risk_budget)
        self.notify.send(
            f"APEX◀TV {direction.upper()} {symbol} @ {price:.2f} | stop {stop:.2f} "
            f"| tp {take:.2f} | risk ${risk_budget:.2f} | open {self.broker.count()} | eq ${equity:.2f}")
        return {"ok": True, "did": f"opened {direction}", "symbol": symbol,
                "size": size, "stop": stop, "take": take}

    def _on_close(self, res, prices):
        risk = res.get("risk") or 1
        r = res["pnl"] / risk if risk else 0
        self.tracker.update({"pnl": res["pnl"], "r": r})
        equity = self.broker.equity(prices)
        self.journal.record(symbol=res["symbol"], side=res["direction"],
                            entry=round(res["entry"], 2), stop=round(res["stop"], 2),
                            take=round(res["take"], 2), exit=round(res["exit"], 2),
                            size=round(res["size"], 6), pnl=round(res["pnl"], 2),
                            r_multiple=round(r, 2), reason=res["reason"],
                            equity_after=round(equity, 2))
        st = self.tracker.stats()
        self.notify.send(f"APEX■ CLOSE {res['symbol']} {res['direction'].upper()} @ "
                         f"{res['exit']:.2f} | P/L ${res['pnl']:.2f} ({r:+.2f}R) "
                         f"| eq ${equity:.2f} | rolling exp {st['expectancy_r']:+.2f}R")


def create_app(bridge):
    from flask import Flask, request, jsonify
    app = Flask(__name__)

    @app.post("/webhook")
    def webhook():
        data = request.get_json(force=True, silent=True) or {}
        if bridge.cfg.webhook_secret and data.get("secret") != bridge.cfg.webhook_secret:
            return jsonify({"ok": False, "error": "bad secret"}), 401
        return jsonify(bridge.handle(data))

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "open_positions": bridge.broker.count()})

    return app


def run(cfg=None):
    cfg = cfg or Config()
    bridge = TradingViewBridge(cfg)
    if not cfg.webhook_secret:
        log.warning("WEBHOOK_SECRET is empty — set one so only YOUR alerts are accepted.")
    bridge.notify.send(f"APEX webhook online on :{cfg.webhook_port} — paper mode, "
                       f"waiting for TradingView alerts.")
    create_app(bridge).run(host="0.0.0.0", port=cfg.webhook_port)
