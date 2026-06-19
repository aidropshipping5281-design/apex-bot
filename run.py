#!/usr/bin/env python3
"""Apex Alpha entrypoint.
  python run.py paper       simulated money, single symbol (START HERE)
  python run.py portfolio   simulated money, multiple symbols at once
  python run.py backtest    score the strategy on history
  python run.py research    search + out-of-sample validate strategy params
  python run.py discord     two-way Discord control + live paper trading
  python run.py webhook     receive TradingView (Tradevisor) alerts -> paper trades
  python run.py live        real orders (only after paper proves an edge)
"""
import logging, sys
from apex.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "paper"
    cfg = Config()

    if mode == "backtest":
        from apex.exchange import MarketData
        from apex.backtest import backtest
        st = backtest(MarketData(cfg).fetch(limit=1000), cfg)
        print("\n=== BACKTEST", cfg.symbol, cfg.timeframe, "===")
        for k, v in st.items():
            print(f"  {k:16}: {v:.4f}" if isinstance(v, float) else f"  {k:16}: {v}")
        return

    if mode == "research":
        from apex.exchange import MarketData
        from apex.research import research_report
        rep = research_report(MarketData(cfg).fetch(limit=1500), cfg, cfg.symbol)
        print("\n=== RESEARCH", cfg.symbol, "===")
        print("context:", rep.get("context") or "(no LLM/news key — backtest-only mode)")
        print("verdict:", rep["reason"])
        for s in rep["promoted"]:
            print("  promoted:", s)
        return

    if mode == "webhook":
        from apex.webhook_server import run as run_webhook
        run_webhook(cfg)
        return

    if mode == "portfolio":
        from apex.portfolio import PortfolioEngine
        PortfolioEngine(cfg).run()
        return

    if mode == "discord":
        from apex.portfolio import PortfolioEngine
        from apex.discord_bot import run_with_discord
        run_with_discord(PortfolioEngine(cfg), cfg)
        return

    from apex.bot import Bot
    cfg.mode = "live" if mode == "live" else "paper"
    if cfg.mode == "live":
        print("LIVE MODE — real money. Ctrl-C within 5s to abort.")
        import time; time.sleep(5)
    Bot(cfg).run()


if __name__ == "__main__":
    main()
