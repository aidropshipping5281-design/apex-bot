# ☁️ APEX — FREE 24/7 CLOUD DEPLOY (always-on, $0)

Goal: run the Apex webhook bot on a free cloud VM that never sleeps, with a free
HTTPS address TradingView can reach — so signals fire even when your PC is off.

**Cost:** $0/month. (Both hosts below need a credit card *for identity verification only* —
you stay inside the always-free tier and are not charged. Set a $0 budget alert to be safe.)

**What you'll end up with:** a public URL like `https://apex-kp.duckdns.org/webhook`
that you paste into your two Tradevisor alerts. No ngrok, no PC required.

I (Apex) have already prepared every file in this `apex_bot/` folder for this. Your part
is the account/click steps below — they need your logins, so they're yours to do. Paste me
anything (IP, domain, errors) and I'll keep wiring it up.

---

## OVERVIEW (what each piece does)
- **Free VM** = the always-on computer in the cloud running the bot.
- **DuckDNS** = a free web address that points at that VM (no domain to buy).
- **Caddy** (already configured) = gives you automatic free HTTPS so TradingView is happy.
- **GitHub** = where your code lives; you `git clone` it onto the VM.
- **Docker Compose** (already configured) = one command starts bot + HTTPS together, auto-restarts forever.

---

## PART A — Put the code on GitHub (~5 min)
Your `.env` (secrets) is git-ignored, so it will NOT be uploaded. Safe to push.

1. Make a GitHub account if you don't have one: https://github.com/signup
2. Create a new **empty** repo: https://github.com/new → name it `apex-bot` → **Private** → Create.
   (Do NOT add a README/.gitignore — the folder already has them.)
3. On your PC, in the `apex_bot` folder, run these (or paste them to me and I'll prep exact commands):
   ```bash
   git init
   git add .
   git commit -m "Apex bot — cloud deploy ready"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/apex-bot.git
   git push -u origin main
   ```
   GitHub will ask you to log in / use a personal access token. That's the one human gate here.

---

## PART B — Get a free DuckDNS address (~3 min)
1. Go to https://www.duckdns.org → sign in (Google/GitHub — free).
2. Type a subdomain, e.g. `apex-kp`, click **add domain**. You now own `apex-kp.duckdns.org`.
3. Leave this tab open — you'll paste your VM's IP into the "current ip" box in Part C, step 6.

---

## PART C — Create the free always-on VM
Pick ONE. **Oracle is recommended** (much more powerful, truly free forever). GCP is the fallback.

### Option 1 (recommended): Oracle Cloud — Always Free "Ampere A1"
1. Sign up: https://www.oracle.com/cloud/free/ → "Start for free". Pick your home region (near you) — **this is permanent**, choose a US West region if you're in California.
2. After signup, Console → **Menu → Compute → Instances → Create instance**.
3. Image & shape: **Canonical Ubuntu 22.04**; Shape → **Ampere (Arm) — VM.Standard.A1.Flex**, set **1 OCPU / 6 GB** (well within free limits).
4. Networking: keep "Assign a public IPv4 address" = **Yes**.
5. **SSH keys:** choose "Generate a key pair for me" → **download the private key** (you'll need it to log in). Click **Create**.
6. When it's running, copy the **Public IP address**. Paste it into the DuckDNS "current ip" box (Part B) → **update ip**.
7. Open the firewall for web traffic:
   - In Oracle: the instance's **subnet → Security List → Add Ingress Rules**: allow TCP **80** and **443** from `0.0.0.0/0`.
   - (We'll also open them inside Ubuntu in Part D.)

### Option 2 (fallback): Google Cloud — Always Free "e2-micro"
1. Sign up: https://cloud.google.com/free → create a project.
2. Compute Engine → Create instance → **Region must be `us-west1`, `us-central1`, or `us-east1`** (only these are free) → Machine type **e2-micro** → Boot disk **Ubuntu 22.04**, 30 GB standard.
3. Check **Allow HTTP traffic** and **Allow HTTPS traffic**. Create.
4. Copy the **External IP** → paste into DuckDNS → update ip.
   - Note: e2-micro has 1 GB RAM. If the Docker build runs out of memory, add a 2 GB swapfile (Part D, optional step).

---

## PART D — Install & launch on the VM (~10 min)
SSH into the VM (Oracle gives a connect command; GCP has an in-browser "SSH" button). Then:

1. Install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
2. Open the firewall inside Ubuntu:
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save 2>/dev/null || true
   ```
3. Get the code:
   ```bash
   git clone https://github.com/YOUR_USERNAME/apex-bot.git
   cd apex-bot
   ```
4. Create the `.env` on the server (it wasn't uploaded). Easiest: copy the template and edit:
   ```bash
   cp .env.example .env
   nano .env
   ```
   Set these, then save (Ctrl+O, Enter, Ctrl+X):
   - `WEBHOOK_SECRET=e061cec072f0ea32`   (your existing secret)
   - `APEX_DOMAIN=apex-kp.duckdns.org`   (your DuckDNS address)
   - `LETSENCRYPT_EMAIL=ninjasteeler4615@gmail.com`
   - `DISCORD_WEBHOOK_URL=` (paste yours if you have it)
   - keep the stock/risk lines (SYMBOLS, RISK_PCT, etc.) as they are.

   *(Optional, GCP e2-micro only — add swap so the build doesn't OOM:)*
   ```bash
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   ```
5. Launch:
   ```bash
   docker compose up -d --build
   ```
6. Check it's alive:
   ```bash
   docker compose logs -f
   ```
   You want to see "APEX webhook online" and Caddy obtaining a certificate. Ctrl+C to stop watching (the bot keeps running).
7. Test from anywhere:
   ```
   https://apex-kp.duckdns.org/health
   ```
   Should return `{"ok": true, ...}`. 🎉 You now have a permanent HTTPS bot.

---

## PART E — Point TradingView at the cloud bot (~3 min)
Same as before, but the URL is now your permanent DuckDNS one (no ngrok).
In each Tradevisor alert → Notifications → **Webhook URL**:
```
https://apex-kp.duckdns.org/webhook
```
BUY alert message:
```json
{"secret":"e061cec072f0ea32","action":"buy","symbol":"{{ticker}}","price":{{close}}}
```
SELL alert message:
```json
{"secret":"e061cec072f0ea32","action":"sell","symbol":"{{ticker}}","price":{{close}}}
```

---

## UPDATING LATER
When I change the code and you push to GitHub, update the VM with:
```bash
cd apex-bot && git pull && docker compose up -d --build
```

## REMINDERS
- Still **paper money** — the cloud changes *where* it runs, not *what* it risks. Nothing real trades until you flip the live flag after a proven edge.
- The free tiers are genuinely free but require a card for verification. Set a budget alert ($0–$1) to be certain you're never billed.
- Keep your private SSH key safe — it's the login to your VM.
