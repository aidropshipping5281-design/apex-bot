# APEX — DEPLOY THE AUTONOMOUS BOT ON THE GCP VM (no PC, no Claude)

Goal: get `always_on.py` (blend + 52-market scanner, **paper**) running 24/7 in the
cloud so it trades and posts to Discord while you sleep — even with your PC off.

This bot pulls its own data from Yahoo and posts out to Discord. **Nothing connects
in** — no webhook, no Caddy, no DuckDNS, no HTTPS, no exchange keys. Ignore the old
`docker-compose.yml` / `DEPLOY_CLOUD.md` (those were for the TradingView webhook path).

**Your VM:** GCP e2-micro, `us-central1-a`, external IP **35.188.183.157**, status RUNNING.
**Your repo:** https://github.com/aidropshipping5281-design/apex-bot (private)

---

## STEP 0 — Push current code first (on your PC)
The repo is stale. Run **`3) PUSH APEX TO GITHUB.bat`** in the Apex v1 folder so the
VM clones the latest engine. (GCM is already authorized, so this should go smoothly —
if it asks you to click an "Authorize" button in the browser, click it.)

---

## STEP 1 — Open the VM terminal
GCP Console → Compute Engine → VM instances → click **SSH** next to your instance.
A browser terminal opens. Run everything below in that terminal.

---

## STEP 2 — Install Docker (one time, ~1 min)
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```
Then **close the SSH tab and reopen it** (so the group change takes effect). Verify:
```bash
docker --version && docker compose version
```

### (e2-micro is small — add 2GB swap so the image build doesn't run out of memory)
```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## STEP 3 — Get the code
The repo is private, so the clone needs a read token. Easiest secure way:

1. On your PC, go to GitHub → Settings → Developer settings → **Fine-grained tokens**
   → Generate new token → Repository access: **Only select repositories → apex-bot**
   → Permissions: **Contents = Read-only** → Generate. Copy the token (starts `github_pat_`).
2. In the VM terminal, paste this (swap in your token):
```bash
git clone https://YOUR_TOKEN_HERE@github.com/aidropshipping5281-design/apex-bot.git
cd apex-bot/apex_bot
```
*(Quick alternative if the token is fussy: make the repo Public for two minutes,
`git clone` with no token, then flip it back to Private. The repo has no secrets —
`.env` is git-ignored — so this is safe.)*

---

## STEP 4 — Create the .env (your Discord URL goes here, not in chat)
```bash
cp .env.example .env
nano .env
```
In nano, set just this one line to your real Discord webhook URL:
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/XXXX/YYYY
```
Leave `MODE=paper` and everything else as-is. Save: **Ctrl+O, Enter, Ctrl+X**.

---

## STEP 5 — Launch it
```bash
docker compose -f docker-compose.cloud.yml up -d --build
```
First build takes a few minutes (installing pandas/numpy/yfinance). When it finishes,
the bot is live and auto-restarts on crash or VM reboot.

---

## STEP 6 — Confirm it's alive
```bash
docker compose -f docker-compose.cloud.yml logs -f
```
You should see `APEX always-on daemon starting` and a cycle running. Within a moment
your Discord gets **"Always-on daemon started — blend + scanner looping, no Claude needed."**
Press **Ctrl+C** to stop watching logs (the bot keeps running). Check status anytime:
```bash
docker ps
```

---

## RUNNING IT FORWARD
- **It survives reboots** (`restart: unless-stopped` + Docker starts on boot).
- **Update after a code change:** push from your PC, then on the VM:
  ```bash
  cd ~/apex-bot && git pull && cd apex_bot && docker compose -f docker-compose.cloud.yml up -d --build
  ```
- **Stop / start:**
  ```bash
  docker compose -f docker-compose.cloud.yml down
  docker compose -f docker-compose.cloud.yml up -d
  ```

## WATCH-ITEMS (honest)
- **Yahoo from a datacenter IP:** yfinance occasionally gets throttled from cloud IPs.
  If the logs show repeated `fetch error` lines, that's this — tell me and we'll switch
  the feed. (Works fine from your home PC, so this only matters on the VM.)
- **Still paper.** This deploy changes *where* the bot runs, not *what risk it takes*.
  Live money stays off until you flip it, after forward paper confirms the edge.
- **Paper state resets only on a `--build` redeploy** (not on reboot). Fine for paper;
  we can add persistence later if you want continuous P&L across redeploys.
