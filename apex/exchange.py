"""Market data + execution venues.

- MarketData: pulls OHLCV candles (ccxt public endpoints; no keys in paper).
- PaperBroker: simulates fills on live prices. Default. No money at risk.
- WebullTrader / CcxtTrader: live adapters (used only when MODE=live).

All traders expose the same interface: equity(price), buy(...), on_price(price),
position_open, so the bot loop doesn't care which venue is underneath.
"""
import logging
import pandas as pd

log = logging.getLogger("apex.exchange")


class MarketData:
    def __init__(self, cfg, ohlcv_provider=None):
        self.cfg = cfg
        self._provider = ohlcv_provider  # injectable for tests/backtests
        self._ex = None
        if ohlcv_provider is None:
            try:
                import ccxt
                self._ex = getattr(ccxt, cfg.exchange)({"enableRateLimit": True})
            except Exception as e:
                log.warning("ccxt init failed (%s); inject an ohlcv_provider for offline use", e)

    def fetch(self, limit=200):
        if self._provider is not None:
            return self._provider(limit)
        rows = self._ex.fetch_ohlcv(self.cfg.symbol, timeframe=self.cfg.timeframe, limit=limit)
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df

    def last_price(self):
        if self._provider is not None:
            return float(self._provider(2)["close"].iloc[-1])
        t = self._ex.fetch_ticker(self.cfg.symbol)
        return float(t["last"])


class PaperBroker:
    """Simulated broker. Holds cash + one position (long or short) with a bracket."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.cash = cfg.start_equity
        self.size = 0.0
        self.entry = self.stop = self.take = 0.0
        self.direction = "long"
        self.position_open = False

    def equity(self, price):
        if not self.position_open:
            return self.cash
        if self.direction == "short":
            return self.cash + self.size * (self.entry - price)   # short MTM
        return self.cash + self.size * price

    def open(self, direction, size, price, stop, take):
        if direction == "long":
            cost = size * price
            if cost > self.cash:            # no leverage on longs
                size = self.cash / price
                cost = size * price
            self.cash -= cost
        else:                               # short: reserve margin = notional
            self.cash -= size * price
        self.size, self.entry, self.stop, self.take = size, price, stop, take
        self.direction = direction
        self.position_open = True
        return {"size": size, "price": price, "direction": direction}

    def buy(self, size, price, stop, take):       # backward-compatible long helper
        return self.open("long", size, price, stop, take)

    def on_price(self, price):
        """Check the bracket. Returns a close dict if stop/take hit, else None."""
        if not self.position_open:
            return None
        hit = None
        if self.direction == "long":
            if price <= self.stop:
                hit = ("stop", self.stop)
            elif price >= self.take:
                hit = ("take", self.take)
        else:
            if price >= self.stop:
                hit = ("stop", self.stop)
            elif price <= self.take:
                hit = ("take", self.take)
        return self._close(hit[1], hit[0]) if hit else None

    def close_market(self, price, reason="signal"):
        return self._close(price, reason) if self.position_open else None

    def _close(self, price, reason):
        if self.direction == "short":
            pnl = (self.entry - price) * self.size
            self.cash += self.size * self.entry + pnl   # release margin + pnl
        else:
            pnl = (price - self.entry) * self.size
            self.cash += self.size * price
        res = {"exit": price, "pnl": pnl, "reason": reason, "direction": self.direction,
               "size": self.size, "entry": self.entry, "stop": self.stop, "take": self.take}
        self.size = 0.0
        self.position_open = False
        return res


class CcxtTrader(PaperBroker):
    """LIVE crypto via ccxt. Mirrors PaperBroker but sends real orders.
    Only constructed when MODE=live. Keys required."""
    def __init__(self, cfg):
        super().__init__(cfg)
        import ccxt
        self._ex = getattr(ccxt, cfg.exchange)({
            "apiKey": cfg.api_key, "secret": cfg.api_secret, "enableRateLimit": True,
        })
        bal = self._ex.fetch_balance()
        quote = cfg.symbol.split("/")[1]
        self.cash = float(bal.get(quote, {}).get("free", cfg.start_equity) or cfg.start_equity)

    def buy(self, size, price, stop, take):
        self._ex.create_order(self.cfg.symbol, "market", "buy", size)
        return super().buy(size, price, stop, take)

    def _close(self, price, reason):
        self._ex.create_order(self.cfg.symbol, "market", "sell", self.size)
        return super()._close(price, reason)


class WebullTrader(PaperBroker):
    """LIVE Webull via official OpenAPI Python SDK (webull-openapi).
    Requires app_key/app_secret + account id. Stocks/ETFs/crypto.
    Finalize once your Webull API keys are configured."""
    def __init__(self, cfg):
        super().__init__(cfg)
        try:
            from webullsdktrade.api import API            # webull official sdk
            from webullsdkcore.client import ApiClient
        except Exception as e:
            raise RuntimeError(
                "Webull SDK not installed. Run: pip install webull-python-sdk-trade "
                "webull-python-sdk-core. Then add your app_key/app_secret/account_id."
            ) from e
        # NOTE: wiring app_key/secret/account_id is finalized with your real keys.
        self._api = None
        log.warning("WebullTrader scaffolded — supply API credentials to enable live orders.")

    # buy()/_close() to call self._api.place_order(...) once credentials are set.
