# Apex Alpha Bot

An automated crypto trading bot with a built-in learning loop. It pulls live
candles, trades a transparent strategy with hard risk controls, alerts your
phone (Telegram/Discord), journals every trade, and adapts its parameters as
results come in. It is designed to run unattended on a small server.

---

## Read this first (the honest part)

- **It ships in PAPER mode.** Simulated money, real prices, no API keys. This is
  where you find out whether the strategy actually works *before* risking a cent.
- **Automation is not an edge.** A bot only executes the strategy you give it. If
  the strategy has no edge, the bot loses money faster than you would by hand.
  The included strategy is a *starting template*, not a proven money-maker.
- **"Learning" means tested learning.** The bot re-optimizes on history with an
  out-of-sample check and pauses itself if it can't find a positive-expectancy
  setup. That improves the odds. It does **not** guarantee profit.
- **Go live only after paper results show a real edge** over a meaningful number
  of trades (aim for 50+). Then start with the smallest size you can.
- Trade only money you can afford to lose entirely. This is software, not advice.

---

## What's inside

```
apex_bot/
  run.py              entrypoint: paper | backtest | live
  requirements.txt
  .env.example        copy to .env and edit
  apex/
    config.py         loads + validates settings (enforces risk caps)
    strategy.py       EMA-cross + ATR stops (long-only spot)
    risk.py           %-of-equity sizing + daily-loss circuit breaker
    exchange.py       MarketData, PaperBroker, CcxtTrader, WebullTrader
    backtest.py       replay strategy on history; grid search
    learn.py          per-trade tracker + auto-tuner (the learning loop)
    bot.py            main loop
```

---

## Quick start (paper — do this today)

```bash
cd apex_bot
python3 -m venv venv && source venv/bin/activate     # (Windows: venv\Scripts\activate)
pip install -r requirements.txt
cp .env.example .env                                  # defaults are paper-safe
python run.py backtest        # score the strategy on recent history first
python run.py paper           # run it live on fake money
```

No keys are needed for paper mode. Leave it running for days/weeks and watch the
journal (`apex_trades.csv`) and the rolling stats it prints/sends.

---

## Phone alerts

**Telegram (recommended):**
1. In Telegram, message **@BotFather** → `/newbot` → copy the bot **token**.
2. Message your new bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy your **chat id**.
3. Put both in `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

**Discord:** Channel → Edit → Integrations → Webhooks → New Webhook → copy URL →
paste into `DISCORD_WEBHOOK_URL` in `.env`.

Both are optional; the bot logs alerts to the console regardless.

---

## Going live (only after paper proves it)

**Crypto via a dedicated exchange (Kraken/Coinbase/etc.):**
1. Create API keys on the exchange (enable *trade*, NOT *withdraw*).
2. `.env`: set `EXCHANGE`, `API_KEY`, `API_SECRET`, `MODE=live`.

**Crypto or stocks via Webull (PDT $25k rule was removed June 2026):**
1. Apply for Webull OpenAPI access; install the SDK:
   `pip install webull-python-sdk-trade webull-python-sdk-core`.
2. Set `EXCHANGE=webull` and your Webull credentials. The `WebullTrader` adapter
   is scaffolded — we finalize the order calls together once you have keys, since
   it needs your real app_key/app_secret/account_id to test.

Live mode waits 5 seconds on start so you can abort. It trades real money.

---

## Run it while your computer is off (VPS)

Scheduled/desktop tools only run while your machine is on. For true 24/7, put it
on a cheap always-on box (~$4–6/mo: DigitalOcean, Hetzner, Linode, etc.).

```bash
# on a fresh Ubuntu VPS
sudo apt update && sudo apt install -y python3-venv git
git clone <your repo or scp the apex_bot folder up>
cd apex_bot && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env        # set MODE, keys, alerts
```

Keep it running forever with systemd:

```ini
# /etc/systemd/system/apex.service
[Unit]
Description=Apex Alpha Bot
After=network-online.target
[Service]
WorkingDirectory=/home/youruser/apex_bot
ExecStart=/home/youruser/apex_bot/venv/bin/python run.py paper
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now apex
journalctl -u apex -f      # watch it live
```

Now it trades and texts you while you sleep — on paper until you decide it's earned the right to go live.

---

## Risk controls baked in (you can tune in .env)

- Per-trade risk capped at a % of *current* equity (default 2%, hard cap 5%).
- Daily-loss circuit breaker halts trading for the day.
- Minimum 2:1 reward:risk enforced; trades below it aren't taken.
- No leverage by default (spot only); position never exceeds available cash.
- Auto-pause when the strategy loses its edge out-of-sample.

*Not financial advice. You are responsible for every order this software places.
Crypto and stocks can lose value fast. Trade only what you can afford to lose.*

---

# v2 — Upgraded capabilities

New modules: `smc.py` (Smart Money Concepts / ICT-style detectors),
`portfolio.py` (multi-position engine), `discord_bot.py` (two-way control),
`research.py` (strategy discovery), plus `Dockerfile` / `docker-compose.yml` / `deploy.sh`.

### Run modes
```bash
python run.py paper       # single symbol, sim money (start here)
python run.py portfolio   # trade several symbols at once (max positions + total-risk caps)
python run.py backtest    # score current params on history
python run.py research     # search params + validate out-of-sample (the 'learning')
python run.py discord     # two-way control from your phone + trading
python run.py live        # real orders — only after paper proves an edge
```

### New skills baked in
- **Shorting** (set `ALLOW_SHORT=true`) — long and short, not just buy.
- **Multi-timeframe trend filter** — entries must agree with the higher-timeframe trend.
- **More chart reading** — RSI, MACD, plus SMC structure / fair-value-gaps / liquidity sweeps as confluence.
- **Multiple positions** — trade a watchlist (`SYMBOLS=BTC/USD,ETH/USD,...`) with `MAX_POSITIONS` and `MAX_TOTAL_RISK_PCT` caps.
- **Two-way Discord** — `status | pnl | stats | positions | pause | resume | flat` from your phone (needs `DISCORD_BOT_TOKEN` + `pip install discord.py`).
- **Research/learning** — proposes strategy params and **only promotes ones that pass out-of-sample backtests**. Optional news + Gemini context with `NEWS_API_KEY` / `GEMINI_API_KEY`.

### One-command hosting (Docker)
```bash
./deploy.sh          # builds + runs in the background, restarts on reboot
docker compose logs -f apex
```

### The honest line on 'research that builds winning strategies'
The research engine generates ideas (and can read news/LLM context if you add
keys), but **nothing is trusted until it survives an out-of-sample backtest**.
That's deliberate: it's the only thing standing between "a strategy that looks
amazing on past data" and "a strategy that loses your money live." When the
validator says *no edge survived*, that's it protecting you — not failing.

---

# TradingView / Tradevisor integration

Let an indicator (e.g. Tradevisor V2) drive the bot. The indicator decides WHEN;
Apex still sizes the trade, sets the stop, enforces the caps, and journals it.

1. `pip install flask`, set `WEBHOOK_SECRET=somethingrandom` in `.env`, then
   `python run.py webhook` (listens on port 8080, paper mode).
2. Expose it so TradingView can reach it: a tunnel like `ngrok http 8080` for
   testing, or run it on your VPS with a public URL.
3. In TradingView, create an alert on the Tradevisor signal. Set **Webhook URL**
   to `https://<your-url>/webhook` and the **message** to:
   ```json
   {"secret":"somethingrandom","action":"buy","symbol":"{{ticker}}","price":{{close}}}
   ```
   Make a second alert for sells with `"action":"sell"`. Optional: add
   `"stop":<price>` / `"tp":<price>` to override Apex's default stop.

Notes: TradingView webhook alerts require a paid TradingView plan (Pro+).
The bridge runs in PAPER mode — it never sends live orders. Live execution
through Webull is a separate, deliberate step after the signals prove out.
