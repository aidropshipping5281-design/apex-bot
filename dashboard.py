"""APEX COMMAND — live web dashboard for the day trader, served from the VM.

Pure stdlib (http.server): no new dependencies. Runs as a daemon thread inside
always_on. Read-only: it renders paper state, it can't touch the bot.
Open http://<vm-ip>/ from any device. Auto-refreshes every 60s.
"""
import os
import csv
import json
import threading
from datetime import time as TT
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8080
RISK_PCT, MAX_FVG_POS = 3.0, 2


def _now_et():
    import pandas as pd
    return pd.Timestamp.now(tz="America/New_York")


def _jload(path, default):
    try:
        return json.load(open(os.path.join(HERE, path)))
    except Exception:
        return default


def _day_trades():
    rows = []
    p = os.path.join(HERE, "closed_trades.csv")
    if not os.path.exists(p):
        return rows
    try:
        for r in csv.DictReader(open(p, newline="")):
            if r.get("source") != "day":
                continue
            try:
                r["pnl"] = float(r["pnl"]); r["r"] = float(r["r"])
                rows.append(r)
            except Exception:
                continue
    except Exception:
        pass
    return rows


def _stats(rows):
    n = len(rows)
    if not n:
        return dict(n=0, win=0.0, pf=0.0, avg_r=0.0, net=0.0, avg_pnl=0.0)
    wins = [r for r in rows if r["pnl"] > 0]
    pos = sum(r["pnl"] for r in wins)
    neg = -sum(r["pnl"] for r in rows if r["pnl"] <= 0)
    return dict(n=n, win=100.0 * len(wins) / n,
                pf=(pos / neg) if neg > 0 else 99.0,
                avg_r=sum(r["r"] for r in rows) / n,
                net=sum(r["pnl"] for r in rows),
                avg_pnl=sum(r["pnl"] for r in rows) / n)


def build_data():
    t = _now_et()
    st = _jload("day_state.json", {"cash": 100.0, "positions": {}})
    paused = _jload("paused_sleeves.json", {})
    trades = _day_trades()
    today = str(t.date())
    todays = [r for r in trades if r["timestamp"][:10] == today]
    curve, cum = [], 0.0
    for r in trades[-120:]:
        cum += r["pnl"]
        curve.append(round(cum, 2))
    equity = st.get("cash", 0.0) + sum(p["size"] * p["entry"]
                                       for p in st.get("positions", {}).values())
    market_open = t.weekday() < 5 and TT(9, 30) <= t.time() <= TT(16, 0)
    return {
        "updated": t.isoformat(timespec="seconds"),
        "market_open": market_open,
        "equity": round(equity, 2),
        "today_pnl": round(sum(r["pnl"] for r in todays), 2),
        "today_n": len(todays),
        "today_best": round(max([r["pnl"] for r in todays], default=0.0), 2),
        "today_worst": round(min([r["pnl"] for r in todays], default=0.0), 2),
        "all": _stats(trades),
        "curve": curve,
        "positions": [dict(key=k, dir=("LONG" if p["dir"] > 0 else "SHORT"),
                           entry=round(p["entry"], 2), stop=round(p["stop"], 2),
                           target=(round(p["target"], 2) if p.get("target") else "EOD"))
                      for k, p in st.get("positions", {}).items()],
        "feed": [dict(ts=r["timestamp"][5:16].replace("T", " "), sleeve=r["sleeve"],
                      pnl=round(r["pnl"], 2), r=round(r["r"], 2), why=r["reason"])
                 for r in trades[-15:]][::-1],
        "paused": list(paused) if isinstance(paused, (list, dict)) else [],
    }


PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>APEX — Day Trader</title><style>
:root{--bg:#0b0f14;--card:#111820;--line:#1d2733;--tx:#e6edf3;--dim:#7d8b99;
--grn:#2be28a;--red:#ff5d6c;--acc:#37c6ff}
*{box-sizing:border-box;margin:0}body{background:var(--bg);color:var(--tx);
font:15px/1.45 'Segoe UI',system-ui,sans-serif;padding:28px;max-width:1080px;margin:auto}
h1{font-size:15px;letter-spacing:.35em;color:var(--dim);font-weight:600}
.live{float:right;font-size:12px;letter-spacing:.15em}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.pnl{font-size:64px;font-weight:700;margin:18px 0 2px}
.sub{color:var(--dim);font-size:13px;margin-bottom:26px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.k{color:var(--dim);font-size:11px;letter-spacing:.12em;text-transform:uppercase}
.v{font-size:26px;font-weight:650;margin-top:6px}
.sec{margin-top:26px}.sec h2{font-size:12px;letter-spacing:.2em;color:var(--dim);
text-transform:uppercase;margin-bottom:10px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-size:11px;letter-spacing:.1em;text-transform:uppercase}
canvas{width:100%;height:120px}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;
border:1px solid var(--line);color:var(--dim);margin:2px 6px 2px 0}
.g{color:var(--grn)}.r{color:var(--red)}.muted{color:var(--dim)}
footer{margin-top:30px;color:var(--dim);font-size:12px}
</style></head><body>
<h1>APEX <span style="color:var(--grn)">DAY TRADER</span>
<span class="live" id="live"></span></h1>
<div class="pnl" id="pnl">—</div>
<div class="sub">TODAY'S REALIZED P&amp;L · <span id="tn">0</span> trades ·
best <span id="tb" class="g">—</span> · worst <span id="tw" class="r">—</span> ·
paper equity $<span id="eq">—</span></div>
<div class="grid">
<div class="card"><div class="k">Win rate</div><div class="v" id="win">—</div><div class="k">all time</div></div>
<div class="card"><div class="k">Avg trade</div><div class="v" id="avg">—</div><div class="k">per trade</div></div>
<div class="card"><div class="k">Profit factor</div><div class="v" id="pf">—</div><div class="k">gross / loss</div></div>
<div class="card"><div class="k">Expectancy</div><div class="v" id="exp">—</div><div class="k">avg R</div></div>
<div class="card"><div class="k">Closed trades</div><div class="v" id="n">—</div><div class="k">sample size</div></div>
</div>
<div class="sec card"><h2>Equity curve (cumulative P&amp;L)</h2><canvas id="cv"></canvas></div>
<div class="sec"><h2>Open positions</h2><table id="pos"><tr><th>Sleeve</th><th>Side</th>
<th>Entry</th><th>Stop</th><th>Target</th></tr></table></div>
<div class="sec"><h2>Trade feed</h2><table id="feed"><tr><th>Time</th><th>Sleeve</th>
<th>P&amp;L</th><th>R</th><th>Exit</th></tr></table></div>
<div class="sec card"><h2>Account protection</h2>
<span class="pill">RISK_PILLS</span>
<div id="paused" class="muted" style="margin-top:8px"></div></div>
<footer>APEX paper day trader — FVG displacement + intraday momentum on index
futures. Validated 2026-07-01 (labs 1-5). Auto-refresh 60s. Updated
<span id="upd">—</span>.</footer>
<script>
const $=id=>document.getElementById(id);
function money(x){const s=x<0?"-":"";return s+"$"+Math.abs(x).toFixed(2)}
async function load(){
 const d=await (await fetch("/data")).json();
 $("pnl").textContent=money(d.today_pnl);
 $("pnl").style.color=d.today_pnl>=0?"var(--grn)":"var(--red)";
 $("tn").textContent=d.today_n;$("eq").textContent=d.equity.toFixed(2);
 $("tb").textContent=money(d.today_best);$("tw").textContent=money(d.today_worst);
 $("live").innerHTML='<span class="dot" style="background:'+(d.market_open?"var(--grn)":"var(--dim)")+'"></span>'+(d.market_open?"MARKET OPEN":"MARKET CLOSED");
 $("win").textContent=d.all.n?d.all.win.toFixed(0)+"%":"—";
 $("avg").textContent=d.all.n?money(d.all.avg_pnl):"—";
 $("pf").textContent=d.all.n?Math.min(d.all.pf,99).toFixed(2):"—";
 $("exp").textContent=d.all.n?(d.all.avg_r>=0?"+":"")+d.all.avg_r.toFixed(2)+"R":"—";
 $("n").textContent=d.all.n;$("upd").textContent=d.updated;
 const pos=$("pos");pos.innerHTML=pos.rows[0].outerHTML;
 if(!d.positions.length){pos.insertRow().innerHTML='<td colspan="5" class="muted">flat — no open positions</td>'}
 d.positions.forEach(p=>{pos.insertRow().innerHTML=
  `<td>${p.key}</td><td class="${p.dir=='LONG'?'g':'r'}">${p.dir}</td><td>${p.entry}</td><td>${p.stop}</td><td>${p.target}</td>`});
 const fd=$("feed");fd.innerHTML=fd.rows[0].outerHTML;
 if(!d.feed.length){fd.insertRow().innerHTML='<td colspan="5" class="muted">no closed trades yet — the record starts with the first exit</td>'}
 d.feed.forEach(r=>{fd.insertRow().innerHTML=
  `<td class="muted">${r.ts}</td><td>${r.sleeve}</td><td class="${r.pnl>=0?'g':'r'}">${money(r.pnl)}</td><td class="${r.r>=0?'g':'r'}">${r.r>=0?"+":""}${r.r}R</td><td class="muted">${r.why}</td>`});
 $("paused").textContent=d.paused.length?("auto-paused sleeves: "+d.paused.join(", ")):"auto-pause armed — no sleeves benched";
 const cv=$("cv"),ctx=cv.getContext("2d");cv.width=cv.clientWidth;cv.height=120;
 ctx.clearRect(0,0,cv.width,cv.height);
 const c=d.curve.length?d.curve:[0,0];
 const mn=Math.min(0,...c),mx=Math.max(0.01,...c);
 ctx.beginPath();ctx.strokeStyle=c[c.length-1]>=0?"#2be28a":"#ff5d6c";ctx.lineWidth=2;
 c.forEach((y,i)=>{const px=i/(c.length-1)*cv.width,
  py=cv.height-8-(y-mn)/(mx-mn)*(cv.height-16);i?ctx.lineTo(px,py):ctx.moveTo(px,py)});
 ctx.stroke();
}
load();setInterval(load,60000);
</script></body></html>"""

PAGE = PAGE.replace("RISK_PILLS",
                    f'3% risk / trade</span><span class="pill">max {MAX_FVG_POS} concurrent FVG'
                    '</span><span class="pill">hard EOD-flat 15:55 ET</span>'
                    '<span class="pill">no overnight risk</span>'
                    '<span class="pill">tracker auto-pause</span><span class="pill">PAPER — no real money')


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path.startswith("/data"):
                body = json.dumps(build_data()).encode()
                ctype = "application/json"
            else:
                body = PAGE.encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            try:
                self.send_error(500, str(e))
            except Exception:
                pass

    def log_message(self, *a):
        pass


def serve():
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
    except Exception as e:
        print(f"[dashboard] server error: {e}")


def start():
    threading.Thread(target=serve, daemon=True).start()
    print(f"[dashboard] serving on :{PORT}")


if __name__ == "__main__":
    serve()
