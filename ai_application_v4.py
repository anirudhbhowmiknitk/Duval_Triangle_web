"""
app_v8.py
==========
Transformer Oil DGA Analyzer
- Groq AI analysis of all gases
- Vendor recommendation text → classified Good / Mild / Bad
- AI gas-by-gas analysis → classified Good / Mild / Bad
- Side-by-side comparison with diff explanation
- Interactive Duval Triangle (SVG, animated sample point, hover zones)
- Clickable gas cards with detail popups
"""

import streamlit as st
import tempfile
import json
import re
import math
from openai import OpenAI

from transformer_oil_extractor_v7 import parse_pdf
from duval_triangle_v7 import classify_duval

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

import os

api_key = os.getenv("GROQ_API_KEY")
# IEC / IS limits for each gas (µl/l)
GAS_LIMITS = {
    "h2":   {"name": "Hydrogen",        "symbol": "H₂",    "unit": "µl/l", "caution": 100,  "limit": 700,  "color": "#4FC3F7"},
    "ch4":  {"name": "Methane",         "symbol": "CH₄",   "unit": "µl/l", "caution": 30,   "limit": 120,  "color": "#81C784"},
    "c2h2": {"name": "Acetylene",       "symbol": "C₂H₂",  "unit": "µl/l", "caution": 1,    "limit": 3,    "color": "#E57373"},
    "c2h4": {"name": "Ethylene",        "symbol": "C₂H₄",  "unit": "µl/l", "caution": 60,   "limit": 280,  "color": "#FFB74D"},
    "c2h6": {"name": "Ethane",          "symbol": "C₂H₆",  "unit": "µl/l", "caution": 35,   "limit": 65,   "color": "#BA68C8"},
    "co":   {"name": "Carbon Monoxide", "symbol": "CO",    "unit": "µl/l", "caution": 400,  "limit": 600,  "color": "#A1887F"},
    "co2":  {"name": "Carbon Dioxide",  "symbol": "CO₂",   "unit": "µl/l", "caution": 3800, "limit": 14000,"color": "#90A4AE"},
    "tdcg": {"name": "TDCG",            "symbol": "TDCG",  "unit": "µl/l", "caution": 720,  "limit": 1920, "color": "#F06292"},
}

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    try:
        s = str(v).strip().upper()
        if s in ("ND", "", "NOT FOUND", "NOT DETECTED"):
            return 0.0
        return float(s)
    except:
        return 0.0


def classify_gas_level(key: str, value: float) -> str:
    """Return GOOD / MILD / BAD based on IEC limits."""
    if key not in GAS_LIMITS:
        return "GOOD"
    caution = GAS_LIMITS[key]["caution"]
    limit   = GAS_LIMITS[key]["limit"]
    if value <= caution:
        return "GOOD"
    if value <= limit:
        return "MILD"
    return "BAD"


def classify_vendor_recommendation(text: str) -> str:
    """Parse vendor recommendation text → GOOD / MILD / BAD."""
    if not text:
        return "UNKNOWN"
    t = text.lower()
    bad_kw  = ["immediate", "critical", "danger", "fault", "isolate", "outage",
               "urgent", "replace", "high energy", "discharge", "arc"]
    mild_kw = ["monitor", "caution", "watch", "trending", "retest", "attention",
               "schedule", "mild", "slight", "investigate", "follow up"]
    good_kw = ["normal", "satisfactory", "acceptable", "good", "healthy",
               "no fault", "within limit", "no action", "ok"]
    for k in bad_kw:
        if k in t: return "BAD"
    for k in mild_kw:
        if k in t: return "MILD"
    for k in good_kw:
        if k in t: return "GOOD"
    return "MILD"   # default


def groq_analyze(data: dict, api_key: str) -> dict:
    """Call Groq with gas values + vendor recommendations → structured analysis."""
    client = OpenAI(
    api_key=api_key,
    base_url="https://api.groq.com/openai/v1"
)

    gases_block = {k: data.get(k, "ND") for k in GAS_LIMITS}
    recs_block = {
        "ost_recommendation": data.get("ost_recommendation", ""),
        "dga_recommendation": data.get("dga_recommendation", ""),
        "overall_recommendation": data.get("recommendation", ""),
    }

    prompt = f"""You are a senior power transformer diagnostic engineer (30 yrs experience, IEC 60599, IEEE C57.104).

You are given:
1. DGA dissolved gas values (µl/l)
2. OST oil screening test parameters
3. Vendor's recommendation text from the lab report

Your job:
A) Independently analyze each gas and the overall DGA picture
B) Classify overall transformer health as GOOD / MILD / BAD
C) Compare your conclusion with the vendor's recommendation
D) Explain any differences between vendor and your AI analysis

GAS VALUES:
{json.dumps(gases_block, indent=2)}

ALL REPORT DATA (OST + identifiers):
{json.dumps({k: v for k, v in data.items() if k not in gases_block}, indent=2)}

VENDOR RECOMMENDATIONS:
{json.dumps(recs_block, indent=2)}

Return ONLY valid JSON, no markdown, no explanation outside JSON:

{{
  "ai_overall": "GOOD|MILD|BAD",
  "ai_severity": "NORMAL|MILD|WARNING|DANGER|CRITICAL",
  "ai_fault_type": "string",
  "ai_root_cause": "string (2-3 sentences)",
  "ai_maintenance": "string (bullet points as \\n separated)",
  "ai_confidence": "HIGH|MEDIUM|LOW",
  "vendor_classification": "GOOD|MILD|BAD",
  "vendor_summary": "string (one sentence summarizing what vendor said)",
  "agreement": "AGREE|PARTIAL|DISAGREE",
  "diff_reason": "string (2-4 sentences explaining why AI and vendor differ or agree - be specific about which gases and values drove AI conclusion)",
  "gas_analysis": {{
    "h2":   {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}},
    "ch4":  {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}},
    "c2h2": {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}},
    "c2h4": {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}},
    "c2h6": {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}},
    "co":   {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}},
    "co2":  {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}},
    "tdcg": {{"value": 0, "status": "GOOD|MILD|BAD", "note": "string"}}
  }}
}}"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=2000,
    )
    text = response.choices[0].message.content
    text = re.sub(r"```json|```", "", text).strip()
    return json.loads(text)


# ──────────────────────────────────────────────────────────────────────────────
# INTERACTIVE DUVAL TRIANGLE (HTML component)
# ──────────────────────────────────────────────────────────────────────────────

def build_duval_html(pCH4: float, pC2H4: float, pC2H2: float, fault_zone: str) -> str:
    """Return a self-contained HTML string with an interactive SVG Duval Triangle."""

    def ternary_to_xy(pCH4, pC2H4, pC2H2):
        total = pCH4 + pC2H4 + pC2H2
        if total == 0:
            return 0.5, 0.1
        a = pCH4 / total
        b = pC2H4 / total
        c = pC2H2 / total
        x = 0.5 * (2 * b + c)
        y = (math.sqrt(3) / 2) * c
        return x, y

    def tp(pCH4, pC2H4, pC2H2, W=560, H=500, ox=20, oy=30):
        x, y = ternary_to_xy(pCH4, pC2H4, pC2H2)
        px = ox + x * W
        py = oy + (1 - y / (math.sqrt(3) / 2)) * H
        return px, py

    W, H, ox, oy = 560, 500, 20, 30

    zones_pts = {
        "PD": {"pts": [(98,0,2),(100,0,0),(98,2,0)],           "color":"#B3D9FF","label":"PD","desc":"Partial Discharge"},
        "T1": {"pts": [(98,0,2),(98,2,0),(76,24,0),(77,0,23)], "color":"#FFFACD","label":"T1","desc":"Thermal < 300°C"},
        "T2": {"pts": [(77,0,23),(76,24,0),(40,60,0),(46,0,54)],"color":"#FFD966","label":"T2","desc":"Thermal 300–700°C"},
        "T3": {"pts": [(46,0,54),(40,60,0),(0,100,0),(0,93,7),(0,0,100)],"color":"#FF9900","label":"T3","desc":"Thermal > 700°C"},
        "D1": {"pts": [(100,0,0),(98,2,0),(76,24,0),(87,0,13)],"color":"#FF9999","label":"D1","desc":"Low Energy Discharge"},
        "D2": {"pts": [(87,0,13),(76,24,0),(40,60,0),(23,0,77)],"color":"#FF3333","label":"D2","desc":"High Energy Discharge"},
        "DT": {"pts": [(23,0,77),(40,60,0),(0,93,7),(0,0,100)],"color":"#CC66FF","label":"DT","desc":"Mixed Discharge+Thermal"},
    }

    polygon_svgs = []
    for zname, zdata in zones_pts.items():
        pts_xy = [tp(*p, W, H, ox, oy) for p in zdata["pts"]]
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts_xy)
        cx = sum(p[0] for p in pts_xy) / len(pts_xy)
        cy = sum(p[1] for p in pts_xy) / len(pts_xy)
        highlight = "stroke-width='3' stroke='#222'" if zname == fault_zone else "stroke-width='1' stroke='white'"
        polygon_svgs.append(
            f'<polygon points="{pts_str}" fill="{zdata["color"]}" {highlight} opacity="0.85" '
            f'class="zone" data-zone="{zname}" data-desc="{zdata["desc"]}" style="cursor:pointer"/>'
        )
        polygon_svgs.append(
            f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" dominant-baseline="middle" '
            f'font-size="11" font-weight="bold" fill="#222" pointer-events="none">{zname}</text>'
        )

    # Corner coords
    bl = tp(100, 0, 0, W, H, ox, oy)
    br = tp(0, 100, 0, W, H, ox, oy)
    top = tp(0, 0, 100, W, H, ox, oy)

    # Sample point
    sx, sy = tp(pCH4, pC2H4, pC2H2, W, H, ox, oy)

    zones_json = json.dumps({k: {"color": v["color"], "desc": v["desc"]} for k, v in zones_pts.items()})

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  body {{ margin:0; background:#0f1117; font-family:'Segoe UI',sans-serif; color:#eee; }}
  #container {{ display:flex; flex-direction:column; align-items:center; padding:10px; }}
  h3 {{ color:#60C8FF; margin:6px 0 2px; font-size:15px; letter-spacing:1px; }}
  #tooltip {{
    position:fixed; background:rgba(15,17,23,0.95); border:1px solid #60C8FF;
    border-radius:8px; padding:10px 14px; font-size:12px; pointer-events:none;
    display:none; z-index:100; min-width:160px; max-width:220px;
    box-shadow: 0 4px 20px rgba(96,200,255,0.3);
  }}
  #tooltip .zone-name {{ font-size:15px; font-weight:bold; color:#60C8FF; margin-bottom:4px; }}
  #tooltip .zone-desc {{ color:#ccc; }}
  #info-bar {{
    background:rgba(96,200,255,0.08); border:1px solid #60C8FF33;
    border-radius:8px; padding:8px 16px; margin-top:8px;
    font-size:12px; text-align:center; width:580px; max-width:98vw;
  }}
  .badge {{
    display:inline-block; padding:3px 10px; border-radius:12px;
    font-weight:bold; font-size:13px; margin-left:8px;
  }}
  .zone {{ transition: opacity 0.15s; }}
  .zone:hover {{ opacity:1 !important; }}
  svg {{ filter: drop-shadow(0 4px 12px rgba(0,0,0,0.5)); }}
  #sample-dot {{ transition: cx 0.8s cubic-bezier(.68,-0.55,.27,1.55), cy 0.8s cubic-bezier(.68,-0.55,.27,1.55); }}
  @keyframes pulse {{
    0%   {{ r: 8; opacity:1; }}
    70%  {{ r: 18; opacity:0; }}
    100% {{ r: 8; opacity:0; }}
  }}
  #pulse-ring {{ animation: pulse 1.8s ease-out infinite; }}
</style>
</head>
<body>
<div id="container">
  <h3>⚡ DUVAL TRIANGLE — IEC 60599 (Interactive)</h3>
  <div id="info-bar">
    <span>Sample: CH₄={pCH4:.1f}% | C₂H₄={pC2H4:.1f}% | C₂H₂={pC2H2:.1f}%</span>
    &nbsp;|&nbsp; Fault Zone: <span class="badge" id="zone-badge" style="background:#60C8FF22;color:#60C8FF;">{fault_zone}</span>
    &nbsp;—&nbsp; <span id="zone-desc-bar" style="color:#aaa;"></span>
  </div>
  <svg width="{W+ox*2}" height="{H+oy*2}" xmlns="http://www.w3.org/2000/svg">
    <!-- Zone polygons -->
    {"".join(polygon_svgs)}
    <!-- Triangle outline -->
    <polygon points="{bl[0]:.1f},{bl[1]:.1f} {br[0]:.1f},{br[1]:.1f} {top[0]:.1f},{top[1]:.1f}"
             fill="none" stroke="#ccc" stroke-width="2"/>
    <!-- Grid lines 20% -->
    {''.join([
      f'<line x1="{tp(tv,0,100-tv,W,H,ox,oy)[0]:.1f}" y1="{tp(tv,0,100-tv,W,H,ox,oy)[1]:.1f}" '
      f'x2="{tp(tv,100-tv,0,W,H,ox,oy)[0]:.1f}" y2="{tp(tv,100-tv,0,W,H,ox,oy)[1]:.1f}" '
      f'stroke="#ffffff22" stroke-width="0.5" stroke-dasharray="3,3"/>'
      for tv in [20,40,60,80]
    ])}
    <!-- Corner labels -->
    <text x="{bl[0]:.1f}" y="{bl[1]+18:.1f}" text-anchor="middle" font-size="12" font-weight="bold" fill="#eee">100% CH₄</text>
    <text x="{br[0]:.1f}" y="{br[1]+18:.1f}" text-anchor="middle" font-size="12" font-weight="bold" fill="#eee">100% C₂H₄</text>
    <text x="{top[0]:.1f}" y="{top[1]-10:.1f}" text-anchor="middle" font-size="12" font-weight="bold" fill="#eee">100% C₂H₂</text>
    <!-- Pulse ring -->
    <circle id="pulse-ring" cx="{sx:.1f}" cy="{sy:.1f}" r="8" fill="none" stroke="#ff4444" stroke-width="2" opacity="0.8"/>
    <!-- Sample point -->
    <circle id="sample-dot" cx="{sx:.1f}" cy="{sy:.1f}" r="7" fill="#ff2222" stroke="white" stroke-width="2"/>
    <text id="sample-label" x="{sx+12:.1f}" y="{sy-10:.1f}" font-size="10" fill="#ff8888" font-weight="bold">▶ Sample</text>
  </svg>
</div>
<div id="tooltip">
  <div class="zone-name" id="tt-name"></div>
  <div class="zone-desc" id="tt-desc"></div>
</div>
<script>
const zones = {zones_json};
const faultZone = "{fault_zone}";

// Set initial info bar
const zinfo = zones[faultZone];
if (zinfo) {{
  document.getElementById('zone-badge').style.background = zinfo.color + '44';
  document.getElementById('zone-badge').style.color = zinfo.color;
  document.getElementById('zone-desc-bar').textContent = zinfo.desc;
}}

// Tooltip on zone hover
document.querySelectorAll('.zone').forEach(el => {{
  el.addEventListener('mouseenter', e => {{
    const z = e.target.dataset.zone;
    const d = e.target.dataset.desc;
    const tt = document.getElementById('tooltip');
    document.getElementById('tt-name').textContent = z + ' — ' + zones[z].color;
    document.getElementById('tt-name').style.color = zones[z].color;
    document.getElementById('tt-desc').textContent = d;
    tt.style.display = 'block';
  }});
  el.addEventListener('mousemove', e => {{
    const tt = document.getElementById('tooltip');
    tt.style.left = (e.clientX+14)+'px';
    tt.style.top  = (e.clientY-10)+'px';
  }});
  el.addEventListener('mouseleave', () => {{
    document.getElementById('tooltip').style.display = 'none';
  }});
  el.addEventListener('click', e => {{
    const z = e.target.dataset.zone;
    const badge = document.getElementById('zone-badge');
    badge.textContent = z;
    badge.style.background = zones[z].color + '44';
    badge.style.color = zones[z].color;
    document.getElementById('zone-desc-bar').textContent = zones[z].desc;
  }});
}});
</script>
</body>
</html>"""
    return html


# ──────────────────────────────────────────────────────────────────────────────
# GAS CARDS HTML
# ──────────────────────────────────────────────────────────────────────────────

def build_gas_cards_html(data: dict, ai_gas: dict) -> str:
    status_color = {"GOOD": "#4CAF50", "MILD": "#FF9800", "BAD": "#F44336", "GOOD": "#4CAF50"}
    status_bg    = {"GOOD": "#1a3a1a", "MILD": "#3a2a00", "BAD": "#3a0a0a"}
    status_icon  = {"GOOD": "✅", "MILD": "⚠️", "BAD": "🚨"}

    cards = []
    for key, meta in GAS_LIMITS.items():
        raw_val = data.get(key, "ND")
        val = _safe_float(raw_val)
        raw_status = classify_gas_level(key, val)
        ai_info = ai_gas.get(key, {})
        ai_status = ai_info.get("status", raw_status)
        ai_note = ai_info.get("note", "")

        # progress bar pct
        limit = meta["limit"]
        pct = min(100, (val / limit * 100)) if limit > 0 and val > 0 else 0
        bar_color = status_color.get(ai_status, "#4CAF50")
        bg_color  = status_bg.get(ai_status, "#1a3a1a")

        cards.append(f"""
<div class="gas-card" onclick="toggleCard(this)" data-key="{key}"
     style="background:{bg_color}; border:1px solid {bar_color}44; border-radius:12px;
            padding:14px; cursor:pointer; transition:all 0.25s; position:relative;">
  <div style="display:flex; justify-content:space-between; align-items:center;">
    <div>
      <div style="font-size:11px; color:#888; letter-spacing:1px;">{meta['name'].upper()}</div>
      <div style="font-size:22px; font-weight:bold; color:{bar_color}; font-family:monospace;">
        {raw_val} <span style="font-size:12px; color:#888;">{meta['unit']}</span>
      </div>
      <div style="font-size:18px; color:#ccc;">{meta['symbol']}</div>
    </div>
    <div style="text-align:right;">
      <div style="font-size:22px;">{status_icon.get(ai_status,'')}</div>
      <div style="font-size:11px; font-weight:bold; color:{bar_color};">{ai_status}</div>
    </div>
  </div>
  <div style="margin-top:8px; background:#ffffff11; border-radius:4px; height:6px; overflow:hidden;">
    <div style="width:{pct:.0f}%; height:100%; background:{bar_color};
                border-radius:4px; transition:width 1s ease;"></div>
  </div>
  <div style="font-size:10px; color:#666; margin-top:3px;">
    Caution: {meta['caution']} | Limit: {meta['limit']} {meta['unit']}
  </div>
  <div class="card-detail" style="display:none; margin-top:10px; padding-top:10px;
       border-top:1px solid #ffffff22; font-size:12px; color:#bbb; line-height:1.5;">
    <b style="color:{bar_color};">AI Analysis:</b><br/>{ai_note if ai_note else "—"}
  </div>
</div>""")

    grid = "\n".join(cards)
    return f"""<div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
                           gap:12px; padding:4px;">
{grid}
</div>
<script>
function toggleCard(el) {{
  const detail = el.querySelector('.card-detail');
  const isOpen = detail.style.display === 'block';
  document.querySelectorAll('.card-detail').forEach(d => d.style.display='none');
  if (!isOpen) detail.style.display = 'block';
}}
</script>"""


# ──────────────────────────────────────────────────────────────────────────────
# COMPARISON PANEL HTML
# ──────────────────────────────────────────────────────────────────────────────

def build_comparison_html(vendor_class: str, ai_overall: str,
                           vendor_summary: str, ai_fault: str,
                           agreement: str, diff_reason: str) -> str:
    vc_color = {"GOOD":"#4CAF50","MILD":"#FF9800","BAD":"#F44336","UNKNOWN":"#888"}.get(vendor_class,"#888")
    ac_color = {"GOOD":"#4CAF50","MILD":"#FF9800","BAD":"#F44336"}.get(ai_overall,"#888")
    ag_color = {"AGREE":"#4CAF50","PARTIAL":"#FF9800","DISAGREE":"#F44336"}.get(agreement,"#888")
    ag_icon  = {"AGREE":"🤝","PARTIAL":"〰️","DISAGREE":"⚡"}.get(agreement,"?")

    return f"""
<div style="display:grid; grid-template-columns:1fr auto 1fr; gap:16px; align-items:start;
            background:#0d1117; border-radius:16px; padding:20px; margin:8px 0;
            border:1px solid #ffffff11;">

  <!-- VENDOR SIDE -->
  <div style="background:#1a1f2e; border-radius:12px; padding:16px;
              border-left:4px solid {vc_color};">
    <div style="font-size:11px; letter-spacing:2px; color:#888; margin-bottom:6px;">VENDOR / LAB</div>
    <div style="font-size:28px; font-weight:900; color:{vc_color};">{vendor_class}</div>
    <div style="font-size:12px; color:#aaa; margin-top:8px; line-height:1.5;">{vendor_summary or "No recommendation text extracted."}</div>
  </div>

  <!-- AGREEMENT BADGE -->
  <div style="display:flex; flex-direction:column; align-items:center; justify-content:center;
              padding:0 8px; min-width:80px;">
    <div style="font-size:26px;">{ag_icon}</div>
    <div style="font-size:10px; font-weight:bold; color:{ag_color}; letter-spacing:1px;
                margin-top:4px; text-align:center;">{agreement}</div>
  </div>

  <!-- AI SIDE -->
  <div style="background:#1a1f2e; border-radius:12px; padding:16px;
              border-left:4px solid {ac_color};">
    <div style="font-size:11px; letter-spacing:2px; color:#888; margin-bottom:6px;">AI (GROQ)</div>
    <div style="font-size:28px; font-weight:900; color:{ac_color};">{ai_overall}</div>
    <div style="font-size:12px; color:#aaa; margin-top:8px; line-height:1.5;">
      Fault: <b style="color:{ac_color};">{ai_fault}</b>
    </div>
  </div>

</div>

<!-- DIFF EXPLANATION -->
<div style="background:#1a1a0a; border:1px solid #FF980033; border-radius:12px;
            padding:16px; margin-top:4px;">
  <div style="font-size:11px; letter-spacing:2px; color:#FF9800; margin-bottom:8px;">
    WHY THE DIFFERENCE
  </div>
  <div style="font-size:13px; color:#ddd; line-height:1.7;">{diff_reason}</div>
</div>
"""


# ──────────────────────────────────────────────────────────────────────────────
# PAGE SETUP
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Transformer DGA Analyzer",
    page_icon="⚡",
    layout="wide"
)

st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background: #0f1117; }
  [data-testid="stSidebar"] { background: #161b22; }
  h1, h2, h3 { color: #60C8FF !important; }
  .stSpinner > div { border-top-color: #60C8FF !important; }
  div[data-testid="metric-container"] {
    background: #161b22;
    border: 1px solid #60C8FF22;
    border-radius: 10px;
    padding: 12px;
  }
  .stTabs [data-baseweb="tab"] { color: #aaa; font-size: 14px; }
  .stTabs [aria-selected="true"] { color: #60C8FF !important; border-bottom-color: #60C8FF !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("# ⚡ Transformer Oil DGA Analyzer")
st.markdown("<p style='color:#888; margin-top:-10px;'>Powered by Groq AI · IEC 60599 · IEEE C57.104</p>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Settings")
    api_key = st.text_input("Groq API Key", value=GROQ_API_KEY, type="password")
    st.markdown("---")
    st.markdown("### 📋 Gas Reference Limits")
    for k, m in GAS_LIMITS.items():
        st.markdown(
            f"<div style='font-size:12px; color:#aaa; margin:2px 0;'>"
            f"<b style='color:{m['color']};'>{m['symbol']}</b>: "
            f"caution {m['caution']} / limit {m['limit']} {m['unit']}</div>",
            unsafe_allow_html=True
        )
    st.markdown("---")
    st.caption("TRU-FIL & SGS/CPRI formats supported")

# ──────────────────────────────────────────────────────────────────────────────
# UPLOAD
# ──────────────────────────────────────────────────────────────────────────────

pdf_file = st.file_uploader("📄 Upload Transformer Oil Report PDF", type=["pdf"])

if not pdf_file:
    st.markdown("""
    <div style="border:2px dashed #60C8FF33; border-radius:16px; padding:40px;
                text-align:center; color:#555; margin-top:20px;">
      <div style="font-size:40px;">📊</div>
      <div style="font-size:16px; margin-top:10px;">Upload a PDF report to begin analysis</div>
      <div style="font-size:12px; margin-top:6px;">Supports TRU-FIL and SGS/CPRI formats</div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ──────────────────────────────────────────────────────────────────────────────
# EXTRACT
# ──────────────────────────────────────────────────────────────────────────────

with st.spinner("🔍 Extracting report data..."):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_file.read())
        pdf_path = tmp.name
    data = parse_pdf(pdf_path)

# ──────────────────────────────────────────────────────────────────────────────
# GROQ ANALYSIS
# ──────────────────────────────────────────────────────────────────────────────

if not api_key or api_key == "gsk_your_groq_api_key_here":
    st.error("⚠️ Please enter your Groq API key in the sidebar.")
    st.stop()

with st.spinner("🤖 Running Groq AI analysis..."):
    try:
        result = groq_analyze(data, api_key)
    except Exception as e:
        st.error(f"Groq API error: {e}")
        st.stop()

# ──────────────────────────────────────────────────────────────────────────────
# TRANSFORMER INFO HEADER
# ──────────────────────────────────────────────────────────────────────────────

eq = data.get("equipment_designation") or data.get("css_name") or "Unknown"
loc = data.get("installation_location") or ""
rdate = data.get("report_date") or data.get("sampling_date") or ""

st.markdown(f"""
<div style="background:#161b22; border-radius:12px; padding:14px 20px; margin:10px 0;
            border:1px solid #60C8FF22; display:flex; justify-content:space-between;
            align-items:center; flex-wrap:wrap; gap:10px;">
  <div>
    <span style="color:#888; font-size:11px;">EQUIPMENT</span><br/>
    <span style="color:#60C8FF; font-size:18px; font-weight:bold;">{eq}</span>
  </div>
  <div>
    <span style="color:#888; font-size:11px;">LOCATION</span><br/>
    <span style="color:#eee; font-size:14px;">{loc}</span>
  </div>
  <div>
    <span style="color:#888; font-size:11px;">REPORT DATE</span><br/>
    <span style="color:#eee; font-size:14px;">{rdate}</span>
  </div>
  <div>
    <span style="color:#888; font-size:11px;">FORMAT</span><br/>
    <span style="color:#FFB74D; font-size:14px; font-weight:bold;">{data.get('fmt','?')}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# SEVERITY BANNER
# ──────────────────────────────────────────────────────────────────────────────

sev = result.get("ai_severity", "NORMAL")
sev_colors = {
    "NORMAL": "#4CAF50", "MILD": "#8BC34A",
    "WARNING": "#FF9800", "DANGER": "#F44336", "CRITICAL": "#9C27B0"
}
sev_icons = {
    "NORMAL": "🟢", "MILD": "🟡", "WARNING": "🟠", "DANGER": "🔴", "CRITICAL": "🚨"
}
sc = sev_colors.get(sev, "#888")
st.markdown(f"""
<div style="background:{sc}22; border:2px solid {sc}; border-radius:12px;
            padding:16px 24px; text-align:center; margin:10px 0;">
  <span style="font-size:32px;">{sev_icons.get(sev,'')}</span>
  <span style="font-size:28px; font-weight:900; color:{sc}; margin-left:12px;">{sev}</span>
  <span style="color:#aaa; font-size:14px; margin-left:20px;">
    Fault: <b style="color:{sc};">{result.get('ai_fault_type','')}</b>
    &nbsp;|&nbsp; Confidence: <b>{result.get('ai_confidence','')}</b>
  </span>
</div>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# MAIN TABS
# ──────────────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🔬 Vendor vs AI",
    "🧪 Gas Analysis",
    "📐 Duval Triangle",
    "🔧 Maintenance",
    "📋 Raw Data",
])

# ═══════════════════════════════════════════════════════════
# TAB 1: VENDOR vs AI COMPARISON
# ═══════════════════════════════════════════════════════════

with tab1:
    st.markdown("### 📊 Recommendation Comparison")
    st.markdown("<p style='color:#888; font-size:13px;'>Vendor lab text vs independent AI gas analysis</p>",
                unsafe_allow_html=True)

    vendor_class   = result.get("vendor_classification",
                                classify_vendor_recommendation(
                                    data.get("recommendation","") +
                                    data.get("dga_recommendation","") +
                                    data.get("ost_recommendation","")
                                ))
    ai_overall     = result.get("ai_overall", "GOOD")
    vendor_summary = result.get("vendor_summary", "")
    ai_fault       = result.get("ai_fault_type", "")
    agreement      = result.get("agreement", "PARTIAL")
    diff_reason    = result.get("diff_reason", "")

    comp_html = build_comparison_html(
        vendor_class, ai_overall,
        vendor_summary, ai_fault,
        agreement, diff_reason
    )
    st.components.v1.html(comp_html, height=320, scrolling=False)

    # Vendor raw recommendation text
    st.markdown("---")
    st.markdown("#### 📝 Vendor Recommendation Texts (Raw)")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**OST Recommendation**")
        ost = data.get("ost_recommendation", "") or "—"
        badge_ost = classify_vendor_recommendation(ost)
        bc = {"GOOD":"#4CAF50","MILD":"#FF9800","BAD":"#F44336","UNKNOWN":"#888"}.get(badge_ost,"#888")
        st.markdown(f"<span style='background:{bc}33;color:{bc};padding:2px 8px;border-radius:8px;font-size:11px;font-weight:bold;'>{badge_ost}</span>",
                    unsafe_allow_html=True)
        st.markdown(f"<div style='color:#ccc;font-size:13px;margin-top:6px;'>{ost}</div>",
                    unsafe_allow_html=True)
    with c2:
        st.markdown("**DGA Recommendation**")
        dga_rec = data.get("dga_recommendation", "") or "—"
        badge_dga = classify_vendor_recommendation(dga_rec)
        bc = {"GOOD":"#4CAF50","MILD":"#FF9800","BAD":"#F44336","UNKNOWN":"#888"}.get(badge_dga,"#888")
        st.markdown(f"<span style='background:{bc}33;color:{bc};padding:2px 8px;border-radius:8px;font-size:11px;font-weight:bold;'>{badge_dga}</span>",
                    unsafe_allow_html=True)
        st.markdown(f"<div style='color:#ccc;font-size:13px;margin-top:6px;'>{dga_rec}</div>",
                    unsafe_allow_html=True)
    with c3:
        st.markdown("**Overall Recommendation**")
        ovr = data.get("recommendation", "") or "—"
        badge_ovr = classify_vendor_recommendation(ovr)
        bc = {"GOOD":"#4CAF50","MILD":"#FF9800","BAD":"#F44336","UNKNOWN":"#888"}.get(badge_ovr,"#888")
        st.markdown(f"<span style='background:{bc}33;color:{bc};padding:2px 8px;border-radius:8px;font-size:11px;font-weight:bold;'>{badge_ovr}</span>",
                    unsafe_allow_html=True)
        st.markdown(f"<div style='color:#ccc;font-size:13px;margin-top:6px;'>{ovr}</div>",
                    unsafe_allow_html=True)

    # Summary metrics
    st.markdown("---")
    st.markdown("#### 🧮 Classification Summary")
    m1, m2, m3 = st.columns(3)
    m1.metric("Vendor Overall", vendor_class)
    m2.metric("AI Overall", ai_overall)
    m3.metric("Agreement", agreement)

# ═══════════════════════════════════════════════════════════
# TAB 2: GAS ANALYSIS CARDS
# ═══════════════════════════════════════════════════════════

with tab2:
    st.markdown("### 🧪 Gas-by-Gas Analysis")
    st.markdown("<p style='color:#888; font-size:13px;'>Click any card to expand AI analysis note</p>",
                unsafe_allow_html=True)

    ai_gas = result.get("gas_analysis", {})
    cards_html = build_gas_cards_html(data, ai_gas)
    st.components.v1.html(cards_html, height=520, scrolling=True)

    # Tabular view
    with st.expander("📊 Table View"):
        rows = []
        for key, meta in GAS_LIMITS.items():
            val = _safe_float(data.get(key, 0))
            ai_info = ai_gas.get(key, {})
            rows.append({
                "Gas": meta["symbol"],
                "Name": meta["name"],
                "Value (µl/l)": data.get(key, "ND"),
                "Caution": meta["caution"],
                "Limit": meta["limit"],
                "Rule-based": classify_gas_level(key, val),
                "AI Status": ai_info.get("status", "—"),
                "AI Note": ai_info.get("note", "—"),
            })
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════
# TAB 3: INTERACTIVE DUVAL TRIANGLE
# ═══════════════════════════════════════════════════════════

with tab3:
    st.markdown("### 📐 Duval Triangle — Interactive")
    st.markdown("<p style='color:#888; font-size:13px;'>Hover zones for info · Sample point animated · Click zone for details</p>",
                unsafe_allow_html=True)

    ch4  = _safe_float(data.get("ch4",  0))
    c2h4 = _safe_float(data.get("c2h4", 0))
    c2h2 = _safe_float(data.get("c2h2", 0))
    total = ch4 + c2h4 + c2h2

    if total > 0:
        pCH4  = ch4  / total * 100
        pC2H4 = c2h4 / total * 100
        pC2H2 = c2h2 / total * 100
        fault_zone = classify_duval(pCH4, pC2H4, pC2H2)
    else:
        pCH4 = pC2H4 = pC2H2 = 0.0
        fault_zone = "T1"

    duval_html = build_duval_html(pCH4, pC2H4, pC2H2, fault_zone)
    st.components.v1.html(duval_html, height=620, scrolling=False)

    # Zone info
    FAULT_MEANINGS = {
        "PD": "Partial Discharge — ionization in voids/bubbles, low energy",
        "T1": "Thermal Fault < 300°C — paper/oil degradation at low temp",
        "T2": "Thermal Fault 300–700°C — moderate thermal hotspot",
        "T3": "Thermal Fault > 700°C — severe hotspot, carbonization likely",
        "D1": "Low Energy Electrical Discharge — sparking, PD-like arcs",
        "D2": "High Energy Discharge — arcing, metallic damage likely",
        "DT": "Mixed Discharge + Thermal — combined fault, complex root cause",
    }
    fz_desc = FAULT_MEANINGS.get(fault_zone, "")
    fz_color = {"PD":"#B3D9FF","T1":"#FFFACD","T2":"#FFD966","T3":"#FF9900",
                "D1":"#FF9999","D2":"#FF3333","DT":"#CC66FF"}.get(fault_zone,"#888")
    st.markdown(f"""
    <div style="background:{fz_color}22; border-left:4px solid {fz_color};
                border-radius:8px; padding:14px; margin-top:10px;">
      <b style="color:{fz_color}; font-size:16px;">Zone {fault_zone}</b><br/>
      <span style="color:#ccc; font-size:13px;">{fz_desc}</span><br/>
      <span style="color:#888; font-size:12px;">
        CH₄={pCH4:.1f}% · C₂H₄={pC2H4:.1f}% · C₂H₂={pC2H2:.1f}%
        (raw: CH₄={ch4} · C₂H₄={c2h4} · C₂H₂={c2h2} µl/l)
      </span>
    </div>
    """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 4: MAINTENANCE
# ═══════════════════════════════════════════════════════════

with tab4:
    st.markdown("### 🔧 AI Maintenance Recommendations")
    root_cause = result.get("ai_root_cause", "")
    maintenance = result.get("ai_maintenance", "")

    st.markdown("#### Root Cause")
    st.markdown(f"<div style='background:#1a1f2e; border-radius:10px; padding:14px; color:#ddd; font-size:14px; line-height:1.7;'>{root_cause}</div>",
                unsafe_allow_html=True)

    st.markdown("#### Recommended Actions")
    actions = [a.strip() for a in maintenance.replace("\\n", "\n").split("\n") if a.strip()]
    for i, action in enumerate(actions, 1):
        st.markdown(f"""
        <div style="background:#1a1f2e; border-left:3px solid #60C8FF; border-radius:0 8px 8px 0;
                    padding:10px 14px; margin:6px 0; color:#ddd; font-size:13px;">
          <b style="color:#60C8FF;">#{i}</b> {action}
        </div>""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 5: RAW DATA
# ═══════════════════════════════════════════════════════════

with tab5:
    st.markdown("### 📋 Extracted Report Data")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Identity & OST**")
        ost_keys = ["owner","installation_location","equipment_designation",
                    "report_date","sampling_date","bdv","water","color",
                    "density","sp_res_27","sp_res_90","ddf_27","ddf_90",
                    "ift","neutralization","flash","oqi"]
        st.json({k: data.get(k,"") for k in ost_keys})
    with c2:
        st.markdown("**DGA Values**")
        dga_keys = ["h2","o2","n2","co","ch4","co2","c2h4","c2h6","c2h2",
                    "c3h6","c3h8","tdcg","tgc","tdcg_ratio",
                    "ost_recommendation","dga_recommendation","recommendation"]
        st.json({k: data.get(k,"") for k in dga_keys})

    with st.expander("Full AI Response JSON"):
        st.json(result)