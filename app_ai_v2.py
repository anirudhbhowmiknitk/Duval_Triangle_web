"""
Transformer Oil Analyzer — Interactive Edition
================================================
• Extracts vendor recommendations from PDF reports
• Uses Groq AI for independent gas analysis
• Compares vendor vs AI verdict (Good / Mild / Bad)
• Fully interactive Duval Triangle (Plotly)
• Explains why vendor and AI may disagree
"""

import json
import re
import tempfile
import streamlit as st
import plotly.graph_objects as go
import numpy as np
from openai import OpenAI

# ── drop-in: reuse existing extractor ──────────────────────────────────────
from transformer_oil_extractor_v7 import parse_pdf

# ===========================================================================
# CONFIG
# ===========================================================================

import os

api_key = os.getenv("GROQ_API_KEY")
GROQ_MODEL   = "llama-3.3-70b-versatile"           # fast & capable

# Condition label → (badge colour, emoji)
CONDITION_STYLE = {
    "GOOD":     ("green",  "🟢"),
    "MILD":     ("orange", "🟡"),
    "BAD":      ("red",    "🔴"),
}

# ===========================================================================
# HELPERS — condition normaliser
# ===========================================================================

def normalise_condition(raw: str) -> str:
    """Map any severity label to GOOD / MILD / BAD."""
    raw = str(raw).upper().strip()
    if raw in ("NORMAL", "GOOD", "OK", "SATISFACTORY", "ACCEPTABLE"):
        return "GOOD"
    if raw in ("MILD", "WARNING", "CAUTION", "MONITOR", "MINOR",
               "MODERATE", "BORDERLINE"):
        return "MILD"
    if raw in ("BAD", "DANGER", "CRITICAL", "SEVERE", "FAULT",
               "IMMEDIATE", "URGENT", "POOR"):
        return "BAD"
    # fallback: try to infer from keywords
    for kw in ("critical", "danger", "fault", "severe", "poor", "bad",
               "immediate", "urgent"):
        if kw in raw.lower():
            return "BAD"
    for kw in ("warning", "caution", "mild", "monitor", "moderate",
               "borderline"):
        if kw in raw.lower():
            return "MILD"
    return "GOOD"


def vendor_condition_from_data(data: dict) -> tuple[str, str]:
    """
    Derive vendor condition label + the raw text from the parsed data.
    Looks at ost_recommendation, dga_recommendation, recommendation fields.
    Returns (condition, raw_text).
    """
    texts = []
    for key in ("ost_recommendation", "dga_recommendation", "recommendation"):
        v = data.get(key, "").strip()
        if v:
            texts.append(f"{key}: {v}")

    combined = " | ".join(texts) if texts else ""

    # Score keyword hits
    bad_hits  = len(re.findall(
        r"immediate|withdraw|critical|severe|disconnect|dangerous|fault|poor",
        combined, re.IGNORECASE))
    mild_hits = len(re.findall(
        r"caution|monitor|check|warning|test|inspect|investigate|borderline|mild",
        combined, re.IGNORECASE))

    if bad_hits >= 1:
        return "BAD", combined or "No recommendation text found."
    if mild_hits >= 1:
        return "MILD", combined or "No recommendation text found."
    if combined:
        return "GOOD", combined
    return "GOOD", "No recommendation text was found in the report."


# ===========================================================================
# GROQ AI ANALYSIS
# ===========================================================================

def groq_analyze(data: dict) -> dict:
    """Send DGA + OST data to Groq and return structured analysis."""
    client = Groq(api_key=GROQ_API_KEY)

    # Build a compact summary of gases and OST
    gas_keys = ["h2","o2","n2","co","ch4","co2","c2h4","c2h6","c2h2",
                "c3h6","c3h8","tdcg","tgc","tdcg_ratio"]
    ost_keys = ["bdv","water","color","density","sp_res_27","sp_res_90",
                "ddf_27","ddf_90","ift","neutralization","sediment","flash","oqi"]

    gases = {k: data.get(k, "ND") for k in gas_keys}
    ost   = {k: data.get(k, "ND") for k in ost_keys}

    vendor_recs = {
        "ost_recommendation": data.get("ost_recommendation", ""),
        "dga_recommendation": data.get("dga_recommendation", ""),
        "overall":            data.get("recommendation", ""),
    }

    prompt = f"""
You are a world-class transformer diagnostic engineer (IEC 60599, IEEE C57.104).

Analyze the transformer oil DGA and OST data below. Give a completely
independent engineering assessment — do NOT just echo the vendor's recommendations.

=== DGA GASES (µL/L) ===
{json.dumps(gases, indent=2)}

=== OIL QUALITY (OST) ===
{json.dumps(ost, indent=2)}

=== VENDOR RECOMMENDATIONS (for your reference only) ===
{json.dumps(vendor_recs, indent=2)}

Your tasks:
1. Classify overall condition as exactly one of: GOOD, MILD, BAD
2. Identify fault type (PD / Thermal-Low / Thermal-Medium / Thermal-High /
   Discharge-Low / Discharge-High / Mixed / Normal)
3. Rate severity (NORMAL / MILD / WARNING / DANGER / CRITICAL)
4. Assess each key gas individually — note which are elevated and why
5. Compare your assessment to the vendor's — explain ANY differences
6. Give specific maintenance recommendations

Return ONLY a valid JSON object, no markdown, no preamble:

{{
  "condition": "GOOD|MILD|BAD",
  "severity": "NORMAL|MILD|WARNING|DANGER|CRITICAL",
  "fault_type": "...",
  "confidence": "High|Medium|Low",
  "risk_level": "Low|Medium|High|Critical",
  "gas_analysis": {{
    "h2":   {{"value": "...", "status": "Normal|Elevated|High", "note": "..."}},
    "co":   {{"value": "...", "status": "...", "note": "..."}},
    "ch4":  {{"value": "...", "status": "...", "note": "..."}},
    "c2h2": {{"value": "...", "status": "...", "note": "..."}},
    "c2h4": {{"value": "...", "status": "...", "note": "..."}},
    "c2h6": {{"value": "...", "status": "...", "note": "..."}},
    "co2":  {{"value": "...", "status": "...", "note": "..."}},
    "tdcg": {{"value": "...", "status": "...", "note": "..."}}
  }},
  "vendor_vs_ai": {{
    "vendor_condition": "GOOD|MILD|BAD",
    "agreement": true,
    "differences": "Detailed explanation of any discrepancies between vendor and AI assessment. If they agree, explain why both are correct.",
    "why_ai_differs": "If disagreement: explain the technical reason AI assessment differs from vendor"
  }},
  "root_cause": "...",
  "maintenance_action": "...",
  "explanation": "..."
}}
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=2000,
    )

    text = response.choices[0].message.content.strip()
    text = re.sub(r"```json|```", "", text).strip()
    return json.loads(text)


# ===========================================================================
# INTERACTIVE DUVAL TRIANGLE (Plotly)
# ===========================================================================

def _t2c(pCH4, pC2H4, pC2H2):
    """Ternary → Cartesian (equilateral triangle)."""
    total = pCH4 + pC2H4 + pC2H2
    if total == 0:
        return 0.5, 0.0
    a, b, c = pCH4/total, pC2H4/total, pC2H2/total
    x = 0.5 * (2*b + c)
    y = (np.sqrt(3)/2) * c
    return x, y


ZONE_DEFS = {
    "PD":  {"color":"#B3D9FF","label":"PD\nPartial Discharge",
            "pts":[(98,0,2),(100,0,0),(98,2,0)]},
    "T1":  {"color":"#FFFF99","label":"T1 < 300°C",
            "pts":[(98,0,2),(98,2,0),(76,24,0),(77,0,23)]},
    "T2":  {"color":"#FFD966","label":"T2 300–700°C",
            "pts":[(77,0,23),(76,24,0),(40,60,0),(46,0,54)]},
    "T3":  {"color":"#FF9900","label":"T3 > 700°C",
            "pts":[(46,0,54),(40,60,0),(0,100,0),(0,93,7),(0,0,100)]},
    "D1":  {"color":"#FF7F7F","label":"D1 Low Energy Discharge",
            "pts":[(100,0,0),(98,2,0),(76,24,0),(87,0,13)]},
    "D2":  {"color":"#FF3333","label":"D2 High Energy Discharge",
            "pts":[(87,0,13),(76,24,0),(40,60,0),(23,0,77)]},
    "DT":  {"color":"#CC66FF","label":"DT Mixed Discharge + Thermal",
            "pts":[(23,0,77),(40,60,0),(0,93,7),(0,0,100)]},
}

FAULT_MEANINGS = {
    "PD":"Partial Discharge","T1":"Thermal Fault < 300°C",
    "T2":"Thermal Fault 300–700°C","T3":"Thermal Fault > 700°C",
    "D1":"Low Energy Electrical Discharge",
    "D2":"High Energy Electrical Discharge (Arc)",
    "DT":"Mixed Discharge + Thermal Fault",
}


def _safe(v) -> float:
    try:
        s = str(v).strip().upper()
        if s in ("ND","","NOT FOUND","N/A","NONE"):
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def classify_duval(pCH4, pC2H4, pC2H2) -> str:
    if pC2H2 >= 29:
        return "DT" if pCH4 <= 23 else "D2"
    if pC2H2 >= 13:
        return "DT" if (pC2H4 <= 60 and pCH4 <= 23) else "D2"
    if pC2H2 >= 2:
        if pC2H4 < 24:
            return "T1"
        return "D1" if pCH4 >= 87 else "D2"
    if pC2H4 >= 60:
        return "T3"
    if pC2H4 >= 24:
        return "T2"
    if pCH4 >= 98:
        return "PD"
    return "T1"


def make_duval_figure(data: dict) -> go.Figure:
    CH4  = _safe(data.get("ch4",  0))
    C2H4 = _safe(data.get("c2h4", 0))
    C2H2 = _safe(data.get("c2h2", 0))
    total = CH4 + C2H4 + C2H2

    if total > 0:
        pCH4, pC2H4, pC2H2 = (CH4/total*100, C2H4/total*100, C2H2/total*100)
        fault_zone = classify_duval(pCH4, pC2H4, pC2H2)
    else:
        pCH4 = pC2H4 = pC2H2 = 0.0
        fault_zone = "T1"

    fig = go.Figure()

    # ── Zone polygons ───────────────────────────────────────────────────────
    for zname, zd in ZONE_DEFS.items():
        xs, ys = [], []
        for pt in zd["pts"]:
            x, y = _t2c(*pt)
            xs.append(x); ys.append(y)
        xs.append(xs[0]); ys.append(ys[0])  # close
        cx = float(np.mean(xs[:-1]))
        cy = float(np.mean(ys[:-1]))
        fig.add_trace(go.Scatter(
            x=xs, y=ys, fill="toself",
            fillcolor=zd["color"],
            line=dict(color="white", width=1),
            opacity=0.85,
            name=zd["label"],
            hovertemplate=f"<b>{zd['label']}</b><br>{FAULT_MEANINGS.get(zname,'')}<extra></extra>",
        ))
        fig.add_annotation(
            x=cx, y=cy,
            text=f"<b>{zname}</b>",
            showarrow=False,
            font=dict(size=10, color="#222"),
        )

    # ── Triangle outline ────────────────────────────────────────────────────
    tx = [0, 1, 0.5, 0]; ty = [0, 0, np.sqrt(3)/2, 0]
    fig.add_trace(go.Scatter(
        x=tx, y=ty, mode="lines",
        line=dict(color="black", width=2),
        showlegend=False, hoverinfo="skip",
    ))

    # ── Grid at 20% intervals ───────────────────────────────────────────────
    for tv in [20, 40, 60, 80]:
        for pairs in [
            (_t2c(tv,0,100-tv), _t2c(tv,100-tv,0)),
            (_t2c(0,tv,100-tv), _t2c(100-tv,tv,0)),
            (_t2c(0,100-tv,tv), _t2c(100-tv,0,tv)),
        ]:
            fig.add_trace(go.Scatter(
                x=[pairs[0][0], pairs[1][0]],
                y=[pairs[0][1], pairs[1][1]],
                mode="lines",
                line=dict(color="gray", width=0.4, dash="dash"),
                showlegend=False, hoverinfo="skip",
            ))

    # ── Sample point (animated) ─────────────────────────────────────────────
    if total > 0:
        sx, sy = _t2c(pCH4, pC2H4, pC2H2)
        # Outer ring (pulse effect via multiple traces)
        for r, alpha in [(0.025, 0.15), (0.015, 0.30)]:
            theta = np.linspace(0, 2*np.pi, 40)
            rx = sx + r * np.cos(theta)
            ry = sy + r * np.sin(theta)
            fig.add_trace(go.Scatter(
                x=rx, y=ry, fill="toself",
                fillcolor=f"rgba(220,50,50,{alpha})",
                line=dict(width=0), showlegend=False, hoverinfo="skip",
            ))
        fig.add_trace(go.Scatter(
            x=[sx], y=[sy],
            mode="markers+text",
            marker=dict(size=14, color="red", symbol="star",
                        line=dict(color="darkred", width=1.5)),
            text=[f"  {fault_zone}"],
            textposition="middle right",
            textfont=dict(size=11, color="darkred"),
            name=f"Sample — {FAULT_MEANINGS.get(fault_zone, fault_zone)}",
            hovertemplate=(
                f"<b>Fault Zone: {fault_zone}</b><br>"
                f"{FAULT_MEANINGS.get(fault_zone,'')}<br>"
                f"CH₄={pCH4:.1f}% | C₂H₄={pC2H4:.1f}% | C₂H₂={pC2H2:.1f}%"
                "<extra></extra>"
            ),
        ))

    # ── Corner labels ────────────────────────────────────────────────────────
    for x, y, txt in [
        (-0.07, -0.05, "100%<br>CH₄"),
        (1.07,  -0.05, "100%<br>C₂H₄"),
        (0.50,   np.sqrt(3)/2 + 0.05, "100%<br>C₂H₂"),
    ]:
        fig.add_annotation(x=x, y=y, text=txt, showarrow=False,
                           font=dict(size=11, color="black"), align="center")

    # ── Tick labels ──────────────────────────────────────────────────────────
    for tv in [20, 40, 60, 80]:
        x, y   = _t2c(100-tv, tv, 0)
        x2, y2 = _t2c(tv, 0, 100-tv)
        x3, y3 = _t2c(0, tv, 100-tv)
        for ann_x, ann_y, txt, xanchor in [
            (x, y-0.04,  f"{tv}%", "center"),
            (x2-0.03, y2, f"{tv}%", "right"),
            (x3+0.03, y3, f"{tv}%", "left"),
        ]:
            fig.add_annotation(
                x=ann_x, y=ann_y, text=txt, showarrow=False,
                font=dict(size=8, color="#666"),
                xanchor=xanchor,
            )

    equip = data.get("equipment_designation") or data.get("css_name") or ""
    title = (
        f"<b>Duval Triangle (IEC 60599)</b>"
        + (f" — {equip}" if equip else "")
        + f"<br><span style='font-size:13px'>Fault Zone: "
          f"<b>{fault_zone}</b> — {FAULT_MEANINGS.get(fault_zone,'')}</span>"
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=15), x=0.5, xanchor="center"),
        showlegend=True,
        legend=dict(
            x=1.02, y=1, xanchor="left",
            font=dict(size=10),
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="lightgray", borderwidth=1,
        ),
        xaxis=dict(visible=False, range=[-0.18, 1.22]),
        yaxis=dict(visible=False, scaleanchor="x",
                   range=[-0.12, np.sqrt(3)/2 + 0.18]),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=20, r=220, t=100, b=40),
        height=580,
        dragmode="zoom",
    )

    return fig, fault_zone, pCH4, pC2H4, pC2H2


# ===========================================================================
# UI HELPERS
# ===========================================================================

def condition_badge(label: str) -> str:
    style_map = {
        "GOOD":  ("background:#1a9e3f;color:white", "🟢"),
        "MILD":  ("background:#f0a500;color:white", "🟡"),
        "BAD":   ("background:#d32f2f;color:white", "🔴"),
    }
    style, icon = style_map.get(label, ("background:#888;color:white", "⚪"))
    return (f'<span style="{style};padding:4px 14px;border-radius:20px;'
            f'font-weight:700;font-size:15px">{icon} {label}</span>')


def gas_status_chip(status: str) -> str:
    colours = {
        "Normal":   "#1a9e3f",
        "Elevated": "#f0a500",
        "High":     "#d32f2f",
    }
    bg = colours.get(status, "#888")
    return (f'<span style="background:{bg};color:white;padding:1px 8px;'
            f'border-radius:10px;font-size:12px;font-weight:600">{status}</span>')


# ===========================================================================
# PAGE LAYOUT
# ===========================================================================

st.set_page_config(
    page_title="Transformer Oil Analyzer",
    page_icon="⚡",
    layout="wide",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .main-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    color: white;
    padding: 28px 32px;
    border-radius: 14px;
    margin-bottom: 28px;
  }
  .main-header h1 { margin: 0; font-size: 2rem; }
  .main-header p  { margin: 6px 0 0; opacity: 0.75; }
  .section-card {
    background: white;
    border: 1px solid #e8e8e8;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 18px;
    box-shadow: 0 1px 6px rgba(0,0,0,0.05);
  }
  .verdict-row {
    display: flex;
    gap: 28px;
    align-items: center;
    flex-wrap: wrap;
  }
  .verdict-block { text-align: center; }
  .verdict-block .label {
    font-size: 11px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
  }
  .diff-box {
    background: #fffbf0;
    border-left: 4px solid #f0a500;
    padding: 14px 18px;
    border-radius: 0 8px 8px 0;
    margin: 12px 0;
  }
  .agree-box {
    background: #f0fff4;
    border-left: 4px solid #1a9e3f;
    padding: 14px 18px;
    border-radius: 0 8px 8px 0;
    margin: 12px 0;
  }
  .gas-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 0;
    border-bottom: 1px solid #f0f0f0;
  }
  .gas-name { font-weight: 600; width: 60px; color: #333; }
  .gas-val  { width: 80px; font-family: monospace; color: #555; }
  .gas-note { flex: 1; font-size: 13px; color: #666; }
  .rec-chip {
    display: inline-block;
    background: #f0f4ff;
    border: 1px solid #d0d8f5;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 6px 4px;
    font-size: 13px;
    color: #3a3a7a;
  }
  h3 { color: #1a1a2e; }
</style>
""", unsafe_allow_html=True)

# ── Header ───────────────────────────────────────────────────────────────────
st.markdown("""
<div class="main-header">
  <h1>⚡ Transformer Oil Analyzer</h1>
  <p>AI-powered DGA analysis · Vendor vs AI comparison · Interactive Duval Triangle</p>
</div>
""", unsafe_allow_html=True)

# ── Upload ───────────────────────────────────────────────────────────────────
pdf_file = st.file_uploader(
    "Upload Transformer Oil DGA Report (PDF)",
    type=["pdf"],
    help="Supports TRU-FIL and SGS/CPRI report formats",
)

if not pdf_file:
    st.info("👆 Upload a PDF report to begin analysis.")
    st.stop()

# ── Extract ──────────────────────────────────────────────────────────────────
with st.spinner("🔍 Extracting report data…"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_file.read())
        pdf_path = tmp.name
    data = parse_pdf(pdf_path)

st.success("✅ Report extracted successfully")

# ── Equipment banner ─────────────────────────────────────────────────────────
equip = (data.get("equipment_designation") or
         data.get("css_name") or "Unknown Equipment")
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Equipment", equip[:32] + ("…" if len(equip) > 32 else ""))
with col2:
    st.metric("Report Date",  data.get("report_date",  "—"))
with col3:
    st.metric("Sampling Date", data.get("sampling_date", "—"))
with col4:
    st.metric("TDCG", data.get("tdcg", "—") + " µL/L" if data.get("tdcg") not in ("", "ND", None) else "—")

st.markdown("---")

# ===========================================================================
# TWO COLUMNS: LEFT = Vendor recs, RIGHT = Groq AI
# ===========================================================================

col_vendor, col_ai = st.columns(2, gap="large")

# ── Vendor Recommendations (left) ───────────────────────────────────────────
with col_vendor:
    st.markdown("### 🏭 Vendor Recommendations")

    vendor_cond, vendor_text = vendor_condition_from_data(data)
    vc_html = condition_badge(vendor_cond)
    st.markdown(vc_html, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    for key, label in [
        ("ost_recommendation", "OST Recommendation"),
        ("dga_recommendation", "DGA Recommendation"),
        ("recommendation",     "Overall Recommendation"),
    ]:
        val = data.get(key, "").strip()
        if val:
            st.markdown(f"""
            <div class="rec-chip">
              <strong>{label}</strong><br>{val}
            </div>""", unsafe_allow_html=True)

    if not any(data.get(k, "").strip() for k in
               ["ost_recommendation", "dga_recommendation", "recommendation"]):
        st.warning("No recommendation text found in the report.")

# ── Groq AI Analysis (right) ─────────────────────────────────────────────────
with col_ai:
    st.markdown("### 🤖 AI (Groq) Analysis")

    ai_result = None
    if st.button("▶ Run Groq AI Analysis", use_container_width=True):
        with st.spinner("Groq is analyzing…"):
            try:
                ai_result = groq_analyze(data)
                st.session_state["ai_result"] = ai_result
            except Exception as e:
                st.error(f"Groq API error: {e}")

    # Persist across reruns
    if "ai_result" not in st.session_state and ai_result is None:
        st.info("Click the button above to run AI analysis.")
        ai_result = None
    elif ai_result is None:
        ai_result = st.session_state.get("ai_result")

    if ai_result:
        ai_cond = normalise_condition(ai_result.get("condition", "GOOD"))
        ac_html = condition_badge(ai_cond)
        st.markdown(ac_html, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        c1.metric("Fault Type",  ai_result.get("fault_type",  "—"))
        c2.metric("Severity",    ai_result.get("severity",    "—"))
        c1.metric("Risk Level",  ai_result.get("risk_level",  "—"))
        c2.metric("Confidence",  ai_result.get("confidence",  "—"))

st.markdown("---")

# ===========================================================================
# VERDICT COMPARISON (only when AI result is available)
# ===========================================================================

if "ai_result" in st.session_state:
    ai_result = st.session_state["ai_result"]
    ai_cond   = normalise_condition(ai_result.get("condition", "GOOD"))

    st.markdown("### ⚖️ Vendor vs AI — Side-by-Side Verdict")

    v1, v2, v3 = st.columns([1, 1, 2])

    with v1:
        st.markdown("**🏭 Vendor**")
        st.markdown(condition_badge(vendor_cond), unsafe_allow_html=True)

    with v2:
        st.markdown("**🤖 AI (Groq)**")
        st.markdown(condition_badge(ai_cond), unsafe_allow_html=True)

    with v3:
        agree = (vendor_cond == ai_cond)
        vv    = ai_result.get("vendor_vs_ai", {})
        if agree:
            st.markdown(
                f'<div class="agree-box">✅ <strong>Agreement</strong><br>'
                f'{vv.get("differences","Both assessments reach the same conclusion.")}'
                f'</div>', unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div class="diff-box">⚠️ <strong>Disagreement Detected</strong><br>'
                f'<em>Vendor: {vendor_cond} | AI: {ai_cond}</em><br><br>'
                f'{vv.get("differences","")}<br><br>'
                f'<strong>Why AI differs:</strong> {vv.get("why_ai_differs","")}'
                f'</div>', unsafe_allow_html=True
            )

    st.markdown("---")

    # ── Gas-by-Gas AI Analysis ───────────────────────────────────────────────
    st.markdown("### 🧪 Gas-by-Gas Analysis (AI)")

    gas_data = ai_result.get("gas_analysis", {})
    if gas_data:
        rows_html = ""
        for gas, info in gas_data.items():
            chip = gas_status_chip(info.get("status", "Normal"))
            rows_html += f"""
            <div class="gas-row">
              <span class="gas-name">{gas.upper()}</span>
              <span class="gas-val">{info.get("value","ND")} µL/L</span>
              {chip}
              <span class="gas-note">{info.get("note","")}</span>
            </div>"""
        st.markdown(
            f'<div class="section-card">{rows_html}</div>',
            unsafe_allow_html=True,
        )

    # ── Root Cause & Maintenance ─────────────────────────────────────────────
    exp1, exp2 = st.columns(2)
    with exp1:
        with st.expander("🔍 Root Cause Analysis", expanded=True):
            st.write(ai_result.get("root_cause", "—"))
    with exp2:
        with st.expander("🔧 Maintenance Actions", expanded=True):
            st.write(ai_result.get("maintenance_action", "—"))

    with st.expander("📋 Full Engineering Explanation"):
        st.write(ai_result.get("explanation", "—"))

    st.markdown("---")

# ===========================================================================
# INTERACTIVE DUVAL TRIANGLE
# ===========================================================================

st.markdown("### 🔺 Interactive Duval Triangle (IEC 60599)")
st.caption(
    "Hover over zones for details · Scroll to zoom · Drag to pan · "
    "Click legend to hide/show zones"
)

fig, fault_zone, pCH4, pC2H4, pC2H2 = make_duval_figure(data)
st.plotly_chart(fig, use_container_width=True)

CH4_raw  = _safe(data.get("ch4",  0))
C2H4_raw = _safe(data.get("c2h4", 0))
C2H2_raw = _safe(data.get("c2h2", 0))
total = CH4_raw + C2H4_raw + C2H2_raw

col_t1, col_t2, col_t3, col_t4 = st.columns(4)
col_t1.metric("CH₄",  f"{CH4_raw} µL/L",  f"{pCH4:.1f}%"  if total > 0 else "—")
col_t2.metric("C₂H₄", f"{C2H4_raw} µL/L", f"{pC2H4:.1f}%" if total > 0 else "—")
col_t3.metric("C₂H₂", f"{C2H2_raw} µL/L", f"{pC2H2:.1f}%" if total > 0 else "—")
col_t4.metric("Duval Zone", fault_zone, FAULT_MEANINGS.get(fault_zone, ""))

st.markdown("---")

# ===========================================================================
# RAW DATA EXPANDER
# ===========================================================================

with st.expander("🗂 Raw Extracted Report Data"):
    st.json(data)