"""Telegram + Discord push notifications. Silently no-ops if unconfigured.
These run on YOUR machine/VPS and post to your own bot/webhook."""
import logging
import requests

log = logging.getLogger("apex.notify")


class Notifier:
    def __init__(self, cfg):
        self.cfg = cfg

    def send(self, text):
        log.info("ALERT: %s", text.replace("\n", " | "))
        self._telegram(text)
        self._discord(text)

    def _telegram(self, text):
        if not (self.cfg.telegram_token and self.cfg.telegram_chat):
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                json={"chat_id": self.cfg.telegram_chat, "text": text},
                timeout=10,
            )
        except Exception as e:
            log.warning("telegram failed: %s", e)

    def _discord(self, text):
        if not self.cfg.discord_webhook:
            return
        try:
            requests.post(self.cfg.discord_webhook, json={"content": text}, timeout=10)
        except Exception as e:
            log.warning("discord failed: %s", e)
