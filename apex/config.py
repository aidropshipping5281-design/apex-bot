"""Loads configuration from environment / .env file."""
import os
from dataclasses import dataclass
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _f(key, default):
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(key, default):
    try:
        return int(float(os.getenv(key, default)))
    except (TypeError, ValueError):
        return int(default)


@dataclass
class Config:
    mode: str = os.getenv("MODE", "paper").lower()
    exchange: str = os.getenv("EXCHANGE", "kraken").lower()
    symbol: str = os.getenv("SYMBOL", "BTC/USD")
    timeframe: str = os.getenv("TIMEFRAME", "15m")
    api_key: str = os.getenv("API_KEY", "")
    api_secret: str = os.getenv("API_SECRET", "")

    start_equity: float = _f("START_EQUITY", 100)
    risk_pct: float = _f("RISK_PCT", 0.02)
    max_daily_loss_pct: float = _f("MAX_DAILY_LOSS_PCT", 0.04)
    rr: float = _f("RR", 2.0)
    atr_mult: float = _f("ATR_MULT", 1.5)
    max_leverage: float = _f("MAX_LEVERAGE", 1.0)

    ema_fast: int = _i("EMA_FAST", 9)
    ema_slow: int = _i("EMA_SLOW", 21)
    atr_period: int = _i("ATR_PERIOD", 14)
    poll_seconds: int = _i("POLL_SECONDS", 60)
    allow_short: bool = os.getenv("ALLOW_SHORT", "false").lower() == "true"
    use_smc: bool = os.getenv("USE_SMC", "true").lower() == "true"
    symbols: str = os.getenv("SYMBOLS", "BTC/USD,ETH/USD")
    max_positions: int = _i("MAX_POSITIONS", 3)
    max_total_risk_pct: float = _f("MAX_TOTAL_RISK_PCT", 0.06)
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    webhook_port: int = _i("WEBHOOK_PORT", 8080)
    webhook_stop_pct: float = _f("WEBHOOK_STOP_PCT", 0.01)

    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat: str = os.getenv("TELEGRAM_CHAT_ID", "")
    discord_webhook: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN", "")

    def validate(self):
        errs = []
        if self.mode not in ("paper", "live"):
            errs.append("MODE must be 'paper' or 'live'")
        if self.mode == "live" and (not self.api_key or not self.api_secret):
            errs.append("live mode needs API_KEY and API_SECRET")
        if self.risk_pct <= 0 or self.risk_pct > 0.05:
            errs.append("RISK_PCT must be >0 and <=0.05 (5%) — capital survival rule")
        if self.rr < 2.0:
            errs.append("RR must be >= 2.0 — minimum reward:risk rule")
        if self.ema_fast >= self.ema_slow:
            errs.append("EMA_FAST must be < EMA_SLOW")
        if self.max_leverage < 1.0:
            errs.append("MAX_LEVERAGE must be >= 1.0")
        return errs
