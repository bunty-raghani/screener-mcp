"""Render the Product Hunt gallery images (1270x760) to PNG with headless Chrome.

Usage: python producthunt/build.py
Numbers in the slides come from real tool output; refresh them before reuse.
"""
import subprocess, pathlib

HERE = pathlib.Path(__file__).parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box;margin:0}
body{width:1270px;height:760px;overflow:hidden;background:#060807;color:#f2f5f0;
 font-family:Inter,-apple-system,sans-serif;position:relative}
body:before{content:"";position:absolute;inset:0;
 background:radial-gradient(900px 500px at 85% -10%,rgba(34,197,94,.45),transparent 70%),
 linear-gradient(rgba(255,255,255,.03) 1px,transparent 1px) 0 0/40px 40px,
 linear-gradient(90deg,rgba(255,255,255,.03) 1px,transparent 1px) 0 0/40px 40px}
.wrap{position:relative;padding:40px 64px 100px;height:100%;display:flex;flex-direction:column;justify-content:center}
.pill{display:inline-flex;align-items:center;gap:8px;background:#10140f;border:1px solid #212b1f;
 border-radius:999px;padding:8px 16px;font-size:15px;color:#99a396;letter-spacing:.04em;text-transform:uppercase}
.dot{width:8px;height:8px;border-radius:50%;background:#34d399}
h1{font-size:60px;line-height:1.08;font-weight:600;letter-spacing:-.02em}
h2{font-size:44px;line-height:1.1;font-weight:600;letter-spacing:-.02em}
.g{color:#34d399}
.sub{color:#99a396;font-size:22px;margin-top:18px;line-height:1.4}
.mono{font-family:"JetBrains Mono",monospace}
.card{background:#10140f;border:1px solid #212b1f;border-radius:16px}
.logo{display:flex;align-items:center;gap:12px;font-size:20px;font-weight:500}
.logo i{width:36px;height:36px;border-radius:10px;background:#22c55e;color:#060807;display:grid;place-items:center;font-style:normal;font-weight:600}
.chip{display:inline-block;background:#080a07;border:1px solid #212b1f;border-radius:8px;padding:5px 10px;
 font-family:"JetBrains Mono",monospace;font-size:14px;color:#34d399}
.user{background:#1a2217;border:1px solid #2a3827;border-radius:14px;padding:14px 18px;font-size:19px;display:inline-block}
table{border-collapse:collapse;width:100%;font-size:19px}
th{color:#647062;font-weight:500;text-align:right;padding:10px 14px;font-size:15px;text-transform:uppercase;letter-spacing:.05em}
td{padding:11px 14px;text-align:right;border-top:1px solid #171f15;font-variant-numeric:tabular-nums}
th:first-child,td:first-child{text-align:left;color:#99a396}
.best{color:#34d399;font-weight:600}
.foot{position:absolute;left:64px;right:64px;bottom:36px;display:flex;justify-content:space-between;color:#647062;font-size:16px;align-items:center}
"""

def page(body):
    return f"<!doctype html><meta charset=utf-8><style>{CSS}</style><body><div class=wrap>{body}</div></body>"

LOGO = '<div class=logo><i>S</i>Screener MCP</div>'
FOOT = f'<div class=foot>{LOGO}<span>github.com/LogeshR15/screener-mcp</span></div>'

slides = {}

slides["1-hero"] = page(f"""
<span class=pill><span class=dot></span>Open source · MIT licensed</span>
<h1 style="margin-top:34px;font-size:76px">Indian stock research,<br><span class=g>right inside Claude</span></h1>
<p class=sub style="font-size:26px;margin-top:26px">Live Screener.in, NSE &amp; MCX data for Claude Code and Claude Desktop.<br>Ask in plain English. Get real, current numbers.</p>
<div style="display:flex;gap:12px;margin-top:40px">
 <span class=pill style="text-transform:none;font-size:18px;color:#f2f5f0">30 MCP tools</span>
 <span class=pill style="text-transform:none;font-size:18px;color:#f2f5f0">No API keys</span>
 <span class=pill style="text-transform:none;font-size:18px;color:#f2f5f0">Python 3.11+</span>
</div>{FOOT}""")

rows = [
 ("Price (₹)","261","1,853","1,243",None),
 ("P/E","16.6","39.5","60.3",0),
 ("ROCE %","38.9","28.4","85.3",2),
 ("ROE %","29.3","31.0","74.2",2),
 ("Debt / equity","0.03","0.03","0.08",(0,1)),
 ("Dividend yield %","5.56","2.21","0.97",0),
]
def tr(r):
    cells="".join(f'<td class="{"best" if i in (r[4] if isinstance(r[4],tuple) else (r[4],)) else ""}">{v}</td>' for i,v in enumerate(r[1:4]))
    return f"<tr><td>{r[0]}</td>{cells}</tr>"
slides["2-compare"] = page(f"""
<h2>Compare companies <span class=g>side by side</span></h2>
<div style="margin-top:28px"><span class=user>Compare ITC, HINDUNILVR and NESTLEIND on key ratios</span></div>
<div style="margin-top:16px"><span class=chip>compare_companies</span></div>
<div class=card style="margin-top:14px;padding:8px 10px">
<table><tr><th></th><th>ITC</th><th>HINDUNILVR</th><th>NESTLEIND</th></tr>{"".join(tr(r) for r in rows)}</table></div>
<p style="color:#647062;font-size:15px;margin-top:12px">Real output from the compare_companies tool. Highlighted = best in row.</p>
{FOOT}""")

slides["3-redflags"] = page(f"""
<h2>Rule-based <span class=g>red-flag checks</span></h2>
<div style="margin-top:28px"><span class=user>What are the red flags in Asian Paints?</span></div>
<div style="margin-top:16px"><span class=chip>analyze_red_flags</span></div>
<div style="display:flex;gap:22px;margin-top:14px">
 <div class=card style="flex:1;padding:22px 26px">
  <div style="color:#647062;font-size:14px;text-transform:uppercase;letter-spacing:.06em">Flagged · 1</div>
  <div style="margin-top:12px;display:flex;gap:12px;align-items:flex-start">
   <span style="background:#3a2f12;color:#fbbf24;border-radius:6px;padding:3px 9px;font-size:14px;font-weight:600;margin-top:2px">MEDIUM</span>
   <div style="font-size:21px;line-height:1.35">ROCE fell from 34% (Mar 2023) to 26% (Mar 2026)</div></div>
  <div class=mono style="margin-top:16px;font-size:14px;color:#647062">threshold: fell &gt; 5 pp over 3 years, or below 10%</div>
 </div>
 <div class=card style="flex:1;padding:22px 26px">
  <div style="color:#647062;font-size:14px;text-transform:uppercase;letter-spacing:.06em">Clean checks · 9</div>
  <div style="margin-top:12px;display:flex;flex-wrap:wrap;gap:8px">
   {"".join(f'<span class=chip style="color:#99a396">✓ {c}</span>' for c in ["cash_conversion","debt","debtor_days","dilution","growth_quality","inventory_days","other_income","pledge","promoter_holding"])}
  </div></div>
</div>
<p class=sub style="font-size:19px;margin-top:20px">Every flag carries its severity, evidence and threshold. Checks that don't apply are skipped, never silently passed.</p>
{FOOT}""")

groups = [
 ("Company research","Overview, financials, quarterly results, shareholding, peers, red flags"),
 ("Screening","Custom queries, thematic screens, 52-week-low candidates, sector compare"),
 ("Street &amp; news","Analyst targets, news, forward outlook, moat signals, valuation"),
 ("Filings &amp; documents","NSE announcements, bulk deals, insider trading, annual reports, earnings calls"),
 ("Commodities","MCX commodity prices"),
 ("Portfolio","Local tracker with live P&amp;L"),
]
slides["4-tools"] = page(f"""
<h2><span class=g>30 tools</span>, one research assistant</h2>
<p class=sub>Claude picks the right tool. You just ask.</p>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:34px">
{"".join(f'<div class=card style="padding:22px 24px;height:150px"><div style="font-size:21px;font-weight:600">{t}</div><div style="color:#99a396;font-size:16px;margin-top:10px;line-height:1.45">{d}</div></div>' for t,d in groups)}
</div>{FOOT}""")

slides["5-install"] = page(f"""
<span class=pill><span class=dot></span>Quick start</span>
<h2 style="margin-top:28px;font-size:56px">Install with <span class=g>one command</span></h2>
<div class="card mono" style="margin-top:36px;padding:26px 30px;font-size:21px;line-height:1.7;background:#080a07">
<span style="color:#647062">$</span> claude mcp add screener -s user -- \\<br>
&nbsp;&nbsp;uvx --from 'screener-mcp[ai]' screener-mcp</div>
<div style="display:flex;gap:44px;margin-top:40px;font-size:20px;color:#99a396">
 <div><span class=g>✓</span> Claude Code &amp; Claude Desktop</div>
 <div><span class=g>✓</span> No API keys</div>
 <div><span class=g>✓</span> Free &amp; open source</div></div>
<p class=sub style="font-size:19px;margin-top:26px">Fundamental stock screening needs a free Screener.in login. Everything else works out of the box.</p>
{FOOT}""")

for name, html in slides.items():
    f = HERE / f"{name}.html"
    f.write_text(html)
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--window-size=1270,760", "--virtual-time-budget=6000",
        f"--screenshot={HERE / (name + '.png')}", f"file://{f}"], check=True, capture_output=True)
    f.unlink()
    print("wrote", name + ".png")
