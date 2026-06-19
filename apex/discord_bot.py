"""Two-way Discord control. Runs the trading engine in a background thread and a
Discord client in the foreground so you can command the bot from your phone:

  status | pnl | stats | positions | pause | resume | flat | help

Needs DISCORD_BOT_TOKEN (create a bot at discord.com/developers, invite to your
server) and `pip install discord.py`. Works with either the single-symbol Bot
or the multi-symbol PortfolioEngine.
"""
import logging, threading
log = logging.getLogger("apex.discord")


def _summary(engine):
    st = engine.tracker.stats()
    paused = getattr(engine, "paused", False)
    return (f"net P/L ${st['net_pnl']:.2f} | trades {st['sample']} | "
            f"win {st['win_rate']*100:.0f}% | exp {st['expectancy_r']:+.2f}R | "
            f"{'PAUSED' if paused else 'ACTIVE'}")


def _positions(engine):
    br = engine.broker
    if hasattr(br, "positions"):                       # portfolio
        if not br.positions:
            return "flat (no open positions)"
        return "\n".join(f"{s}: {p['direction']} size {p['size']:.4f} @ {p['entry']:.2f} "
                         f"(stop {p['stop']:.2f}/tp {p['take']:.2f})"
                         for s, p in br.positions.items())
    if getattr(br, "position_open", False):            # single
        return (f"{engine.cfg.symbol}: {br.direction} size {br.size:.4f} @ {br.entry:.2f} "
                f"(stop {br.stop:.2f}/tp {br.take:.2f})")
    return "flat (no open position)"


def handle(engine, cmd):
    cmd = cmd.strip().lower().lstrip("!")
    if cmd in ("status", "stats", "pnl"):
        return "📊 " + _summary(engine)
    if cmd in ("positions", "pos"):
        return "📌 " + _positions(engine)
    if cmd == "pause":
        engine.paused = True
        return "⏸ Paused — no new entries. Open trades still managed to their stops/targets."
    if cmd == "resume":
        engine.paused = False
        return "▶️ Resumed — new entries enabled."
    if cmd == "flat":
        return "🔻 " + _flatten(engine)
    if cmd in ("help", "commands"):
        return ("Commands: status | pnl | stats | positions | pause | resume | flat\n"
                "(pause stops NEW entries; flat closes everything now.)")
    return None


def _flatten(engine):
    br = engine.broker
    closed = 0
    if hasattr(br, "positions"):
        for sym in list(br.positions):
            px = br.positions[sym]["entry"]
            br.close(sym, px, "manual"); closed += 1
    elif getattr(br, "position_open", False):
        br.close_market(br.entry, "manual"); closed += 1
    engine.paused = True
    return f"Closed {closed} position(s) at last price and paused. Resume when ready."


def run_with_discord(engine, cfg):
    try:
        import discord
    except Exception as e:
        raise RuntimeError("pip install discord.py to use two-way Discord control") from e
    if not cfg.discord_bot_token:
        raise RuntimeError("Set DISCORD_BOT_TOKEN in .env")

    # trade engine in the background; Discord client in the foreground
    threading.Thread(target=engine.run, daemon=True).start()

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        log.info("Discord control online as %s", client.user)

    @client.event
    async def on_message(msg):
        if msg.author == client.user:
            return
        reply = handle(engine, msg.content)
        if reply:
            await msg.channel.send(reply)

    client.run(cfg.discord_bot_token)
