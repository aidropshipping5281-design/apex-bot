"""Lightweight Discord notifier — used by the standalone bots (paper, scanner,
report) so every move pushes to your Discord channel.

Reads DISCORD_WEBHOOK_URL from the environment or from apex_bot/.env. If it's not
set, it just prints (no-op) — nothing breaks. PAPER moves only; no orders here.

To turn it on: create an Incoming Webhook in your Discord server
(Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL) and put
it in apex_bot/.env as:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/....
"""
import os

try:
    import requests
except Exception:
    requests = None

_HERE = os.path.dirname(os.path.abspath(__file__))


def _webhook_url():
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        return url.strip()
    envp = os.path.join(_HERE, ".env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("DISCORD_WEBHOOK_URL"):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and "discord.com/api/webhooks" in val:
                    return val
    return None


def notify(text, prefix="APEX"):
    """Post text to Discord if configured; always echo to stdout. Returns bool sent."""
    msg = f"**{prefix}** | {text}" if prefix else text
    print(f"[notify] {text}")
    url = _webhook_url()
    if not url or requests is None:
        return False
    try:
        r = requests.post(url, json={"content": msg[:1900]}, timeout=10)
        return r.status_code < 300
    except Exception as e:
        print(f"[notify] discord failed: {e}")
        return False


def is_configured():
    return _webhook_url() is not None


if __name__ == "__main__":
    print("Discord webhook configured:", is_configured())
    notify("Test message from Apex — if you see this in Discord, notifications work.",
           prefix="APEX TEST")
