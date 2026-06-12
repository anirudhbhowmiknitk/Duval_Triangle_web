"""
app_water.py — Transformer Oil Dashboard  (Water Content Edition)
==================================================================
Classification: SOLELY on Water Content (ppm) vs BIS 1866-2017 limit of 40 ppm
  🔴 CRITICAL  — water > 40 ppm   (exceeds limit)
  🟠 CAUTION   — water 30–40 ppm  (approaching limit, ≥75% of limit)
  🟢 GOOD      — water < 30 ppm   (well within limit)

Run:  streamlit run app_water.py
"""

import os, tempfile
import streamlit as st

from transformer_oil_extractor_v7 import parse_pdf

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

WATER_LIMIT      = 40.0   # BIS 1866-2017 max (ppm)
WATER_CAUTION    = 30.0   # 75% of limit — flag as caution
WATER_UNIT       = "ppm"

SEVERITY_ICON  = {"CRITICAL": "🔴", "CAUTION": "🟠", "GOOD": "🟢"}
SEVERITY_ORDER = {"CRITICAL": 0, "CAUTION": 1, "GOOD": 2, "ERR": 3}
SEVERITY_COLOR = {"CRITICAL": "#c0392b", "CAUTION": "#d35400", "GOOD": "#27ae60"}
SEVERITY_BG    = {"CRITICAL": "#fff0f0", "CAUTION": "#fff8f0", "GOOD": "#f0fff4"}

# ─────────────────────────────────────────────────────────────────────────────
# Page config + CSS
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Transformer Oil — Water Content Monitor",
    page_icon="💧",
    layout="wide",
)

st.markdown("""
<style>
html, body, [class*="css"] { font-family: 'Segoe UI', Arial, sans-serif; }
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

/* ── Hero ── */
.hero-banner {
    background: linear-gradient(135deg, #0a2342 0%, #0d3b6e 55%, #1565c0 100%);
    border-radius: 14px;
    padding: 1.8rem 2.2rem 1.5rem;
    margin-bottom: 1.5rem;
    box-shadow: 0 6px 24px rgba(0,0,0,0.3);
}
.hero-banner h1 { color: #e3f2fd; font-size: 2rem; font-weight: 700; margin: 0 0 0.3rem; }
.hero-banner p  { color: #90caf9; font-size: 0.92rem; margin: 0; }

/* ── Summary cards ── */
.metric-row { display: flex; gap: 14px; margin-bottom: 1.5rem; flex-wrap: wrap; }
.metric-card {
    flex: 1 1 120px;
    border-radius: 12px;
    padding: 1rem 1.1rem;
    text-align: center;
    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
}
.metric-card .mc-num  { font-size: 2.2rem; font-weight: 700; line-height: 1.1; }
.metric-card .mc-lbl  { font-size: 0.78rem; font-weight: 600; text-transform: uppercase;
                         letter-spacing: 0.07em; opacity: 0.75; margin-top: 4px; }
.mc-total    { background: #e8eaf6; color: #1a237e; }
.mc-critical { background: #ffebee; color: #b71c1c; }
.mc-caution  { background: #fff3e0; color: #bf360c; }
.mc-good     { background: #e8f5e9; color: #1b5e20; }
.mc-err      { background: #f5f5f5; color: #555; }

/* ── Section headers ── */
.sev-header {
    border-radius: 10px;
    padding: 0.75rem 1.4rem;
    margin: 1.2rem 0 0.6rem;
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.02em;
}
.sev-critical { background: linear-gradient(90deg,#c0392b,#e74c3c); color:#fff; }
.sev-caution  { background: linear-gradient(90deg,#d35400,#e67e22); color:#fff; }
.sev-good     { background: linear-gradient(90deg,#27ae60,#2ecc71); color:#fff; }

/* ── Water gauge block ── */
.water-gauge-wrap {
    border-radius: 12px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1rem;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
}
.wg-value { font-size: 3rem; font-weight: 800; line-height: 1; }
.wg-unit  { font-size: 1rem; font-weight: 600; opacity: 0.65; margin-left: 4px; }
.wg-limit { font-size: 0.8rem; opacity: 0.6; margin-top: 4px; }

/* ── Progress bar ── */
.bar-track {
    background: #e0e0e0;
    border-radius: 50px;
    height: 14px;
    width: 100%;
    margin: 10px 0 4px;
    overflow: hidden;
}
.bar-fill { height: 100%; border-radius: 50px; transition: width 0.4s; }

/* ── Verdict banner ── */
.verdict-banner {
    border-radius: 8px;
    padding: 0.7rem 1.1rem;
    margin-bottom: 1rem;
    font-size: 1rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.verdict-critical { background:#fff0f0; border:2px solid #c0392b; color:#c0392b; }
.verdict-caution  { background:#fff8f0; border:2px solid #d35400; color:#d35400; }
.verdict-good     { background:#f0fff4; border:2px solid #27ae60; color:#27ae60; }

/* ── Recommendation boxes ── */
.rec-box {
    border-radius: 8px;
    padding: 0.8rem 1.1rem;
    margin-bottom: 0.5rem;
    font-size: 0.92rem;
    line-height: 1.55;
}
.rec-box strong {
    display: block;
    margin-bottom: 0.3rem;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    opacity: 0.7;
}
.rec-ost     { background:#e3f2fd; border-left:4px solid #1565c0; color:#0d2a5e; }
.rec-dga     { background:#e8f5e9; border-left:4px solid #27ae60; color:#1a4a2a; }
.rec-overall { background:#f3e5f5; border-left:4px solid #8e24aa; color:#3e0056; }
.rec-warn    { background:#fff8e6; border-left:4px solid #f39c12; color:#5c3a00; }

/* ── Section divider ── */
.section-label {
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #888;
    margin: 1.1rem 0 0.4rem;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid #e8e8e8;
}

/* ── OST detail table ── */
.ost-table { width:100%; border-collapse:collapse; font-size:0.88rem; margin-top:4px; }
.ost-table td { padding:5px 8px; }
.ost-table tr:nth-child(odd) td { background:#f8faff; }
.ost-table .ost-lbl { color:#555; }
.ost-table .ost-val { font-weight:700; color:#1a1a1a; text-align:right; }
.ost-table .ost-ok  { color:#27ae60; font-size:0.75rem; }
.ost-table .ost-fail{ color:#c0392b; font-size:0.75rem; }
.ost-table .ost-ns  { color:#aaa;    font-size:0.75rem; }

/* metric value size */
[data-testid="stMetricValue"] { font-size: 1rem !important; }
[data-testid="stMetricLabel"] { font-size: 0.74rem !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_float(v) -> float:
    try:
        s = str(v).strip().upper()
        return 0.0 if s in ("ND", "NOT FOUND", "", "NS", "NA") else float(s)
    except (TypeError, ValueError):
        return 0.0


def classify_by_water(data: dict) -> tuple[str, float]:
    """Return (severity, water_ppm) based purely on water content."""
    water = safe_float(data.get("water", 0))
    if water == 0.0:
        # if not found treat as GOOD but note it
        return "GOOD", water
    if water > WATER_LIMIT:
        return "CRITICAL", water
    if water >= WATER_CAUTION:
        return "CAUTION", water
    return "GOOD", water


def v(data, key, unit="") -> str:
    val = data.get(key, "")
    if not val or str(val).strip().upper() in ("ND", "NOT FOUND", "", "NS", "NA"):
        return "—"
    return f"{val} {unit}".strip()


def pct_bar_html(value: float, limit: float, color: str) -> str:
    pct = min(value / limit * 100, 100)
    return (
        f"<div class='bar-track'>"
        f"<div class='bar-fill' style='width:{pct:.1f}%;background:{color};'></div>"
        f"</div>"
        f"<div style='font-size:0.78rem;color:#888;'>{pct:.1f}% of limit ({limit:.0f} ppm)</div>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Hero banner
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="hero-banner">
  <h1>💧 Transformer Oil — Water Content Monitor</h1>
  <p>
    Classification solely by moisture level &nbsp;·&nbsp;
    BIS 1866-2017 limit: <strong>40 ppm max</strong> &nbsp;·&nbsp;
    🔴 CRITICAL &gt;40 &nbsp; 🟠 CAUTION 30–40 &nbsp; 🟢 GOOD &lt;30
  </p>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────────────────────────

pdfs = st.file_uploader(
    "📄 Upload Oil Test Report PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    help="TRU-FIL / SGS / CPRI format",
)

if not pdfs:
    st.markdown("""
    <div style="border:2px dashed #90caf9;border-radius:12px;padding:2.5rem;
                text-align:center;color:#5c85b5;margin-top:0.8rem;background:#f0f7ff;">
      <div style="font-size:3rem;margin-bottom:0.6rem;">💧</div>
      <div style="font-size:1.1rem;font-weight:600;">Drop transformer oil report PDFs here</div>
      <div style="font-size:0.88rem;margin-top:0.4rem;opacity:0.7;">
        Reports will be sorted by water content: Critical → Caution → Good
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# Parse + classify
# ─────────────────────────────────────────────────────────────────────────────

if "reports" not in st.session_state:
    st.session_state.reports = {}

reports  = st.session_state.reports
progress = st.progress(0, text="Parsing reports …")

for i, pdf_file in enumerate(pdfs):
    key = f"{pdf_file.name}:{pdf_file.size}"
    if key not in reports:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_file.read())
            tmp_path = tmp.name
        try:
            data = parse_pdf(tmp_path)
            severity, water_ppm = classify_by_water(data)
            reports[key] = dict(
                name=pdf_file.name, data=data,
                severity=severity, water_ppm=water_ppm,
                error=None,
            )
        except Exception as exc:
            reports[key] = dict(
                name=pdf_file.name, data={},
                severity="ERR", water_ppm=0.0,
                error=str(exc),
            )
        finally:
            os.unlink(tmp_path)
    progress.progress((i + 1) / len(pdfs), text=f"Parsed {i+1} / {len(pdfs)} …")

progress.empty()

all_reports = sorted(
    reports.values(),
    key=lambda r: (SEVERITY_ORDER.get(r["severity"], 99), -r["water_ppm"])
)

# ─────────────────────────────────────────────────────────────────────────────
# Summary metric row
# ─────────────────────────────────────────────────────────────────────────────

n_total    = len(all_reports)
n_critical = sum(1 for r in all_reports if r["severity"] == "CRITICAL")
n_caution  = sum(1 for r in all_reports if r["severity"] == "CAUTION")
n_good     = sum(1 for r in all_reports if r["severity"] == "GOOD")
n_err      = sum(1 for r in all_reports if r["severity"] == "ERR")

err_card = (
    f"<div class='metric-card mc-err'>"
    f"<div class='mc-num'>{n_err}</div>"
    f"<div class='mc-lbl'>❌ Errors</div></div>"
) if n_err else ""

st.markdown(f"""
<div class="metric-row">
  <div class="metric-card mc-total">
    <div class="mc-num">{n_total}</div>
    <div class="mc-lbl">📂 Total</div>
  </div>
  <div class="metric-card mc-critical">
    <div class="mc-num">{n_critical}</div>
    <div class="mc-lbl">🔴 Critical</div>
  </div>
  <div class="metric-card mc-caution">
    <div class="mc-num">{n_caution}</div>
    <div class="mc-lbl">🟠 Caution</div>
  </div>
  <div class="metric-card mc-good">
    <div class="mc-num">{n_good}</div>
    <div class="mc-lbl">🟢 Good</div>
  </div>
  {err_card}
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# OST full detail table helper
# ─────────────────────────────────────────────────────────────────────────────

OST_ROWS = [
    ("BDV",                  "bdv",            "kV",       30,    None,  "min"),
    ("Water Content",        "water",          "ppm",      None,  40,    "max"),
    ("IFT @ 27°C",           "ift",            "N/m",      0.020, None,  "min"),
    ("Neutralization Value", "neutralization", "mgKOH/g",  None,  0.3,   "max"),
    ("Density @ 29.5°C",     "density",        "g/cm³",    None,  0.890, "max"),
    ("Color (ASTM)",         "color",          "",         None,  7.0,   "max"),
    ("Flash Point",          "flash",          "°C",       125,   None,  "min"),
    ("OQI",                  "oqi",            "",         45,    None,  "min"),
    ("tan δ @ 27°C",         "tdf_27",         "",         None,  None,  "ns"),
    ("tan δ @ 90°C",         "tdf_90",         "",         None,  0.5,   "max"),
    ("Sp. Resistance @27°C", "sp_res_27",      "×10¹² Ω·cm", 0.4, None, "min"),
    ("Sp. Resistance @90°C", "sp_res_90",      "×10¹² Ω·cm", 0.02,None, "min"),
    ("Sediment & Sludge",    "sludge",         "%",        None,  0.1,   "max"),
]

def ost_status(raw_val, limit, direction):
    """Return ('✔ OK','ost-ok') / ('✘ FAIL','ost-fail') / ('—','ost-ns')."""
    if direction == "ns" or limit is None:
        return "NS", "ost-ns"
    try:
        fv = float(str(raw_val).strip())
    except (ValueError, TypeError):
        return "—", "ost-ns"
    if direction == "max":
        ok = fv <= limit
    else:
        ok = fv >= limit
    return ("✔ OK", "ost-ok") if ok else ("✘ FAIL", "ost-fail")


def render_ost_table(data: dict) -> str:
    rows_html = ""
    for label, key, unit, lim_min, lim_max, direction in OST_ROWS:
        raw  = data.get(key, "")
        disp = "—" if not raw or str(raw).strip().upper() in ("ND","NOT FOUND","","NS","NA") \
               else f"{raw} {unit}".strip()
        limit_val = lim_min if direction == "min" else lim_max
        status, css = ost_status(raw, limit_val, direction)
        # highlight water content row
        row_bg = "background:#e3f2fd;" if key == "water" else ""
        rows_html += (
            f"<tr style='{row_bg}'>"
            f"<td class='ost-lbl'>{label}</td>"
            f"<td class='ost-val'>{disp}</td>"
            f"<td class='{css}'>{status}</td>"
            f"</tr>"
        )
    return (
        "<table class='ost-table'>"
        "<thead><tr style='border-bottom:2px solid #dde;'>"
        "<th style='text-align:left;padding:5px 8px;font-size:0.72rem;text-transform:uppercase;"
        "letter-spacing:.07em;color:#666;'>Parameter</th>"
        "<th style='text-align:right;padding:5px 8px;font-size:0.72rem;text-transform:uppercase;"
        "letter-spacing:.07em;color:#666;'>Value</th>"
        "<th style='text-align:center;padding:5px 8px;font-size:0.72rem;text-transform:uppercase;"
        "letter-spacing:.07em;color:#666;'>Status</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Card renderer
# ─────────────────────────────────────────────────────────────────────────────

def render_card(rep: dict):
    data  = rep["data"]
    sev   = rep["severity"]
    water = rep["water_ppm"]

    if rep.get("error"):
        st.error(f"⚠️ Parse error — **{rep['name']}**: {rep['error']}")
        return

    equip = (
        v(data, "equipment_designation")
        or v(data, "css_name")
        or rep["name"]
    )
    sp = v(data, "sampling_point") or "—"

    label = f"{SEVERITY_ICON[sev]}  {equip}  ·  {sp}  ·  {rep['name']}"

    with st.expander(label, expanded=(sev in ("CRITICAL", "CAUTION"))):

        # ── Two-column main layout ────────────────────────────────────────────
        col_water, col_recs = st.columns([1, 2], gap="large")

        # ── LEFT: water gauge ─────────────────────────────────────────────────
        with col_water:
            sev_color = SEVERITY_COLOR[sev]
            sev_bg    = SEVERITY_BG[sev]

            # Verdict badge
            vc_css = {"CRITICAL":"critical","CAUTION":"caution","GOOD":"good"}.get(sev,"good")
            st.markdown(
                f"<div class='verdict-banner verdict-{vc_css}'>"
                f"{SEVERITY_ICON[sev]} Classification: <strong>{sev}</strong>"
                f"</div>",
                unsafe_allow_html=True,
            )

            water_display = f"{water:.1f}" if water else "N/D"
            st.markdown(
                f"<div class='water-gauge-wrap' style='background:{sev_bg};border:2px solid {sev_color};'>"
                f"  <div style='font-size:0.72rem;font-weight:700;text-transform:uppercase;"
                f"letter-spacing:.1em;color:{sev_color};margin-bottom:6px;'>💧 Water Content</div>"
                f"  <div class='wg-value' style='color:{sev_color};'>{water_display}"
                f"    <span class='wg-unit'>ppm</span>"
                f"  </div>"
                f"  <div class='wg-limit'>BIS limit: ≤ {WATER_LIMIT:.0f} ppm</div>"
                + (pct_bar_html(water, WATER_LIMIT, sev_color) if water else "")
                + f"</div>",
                unsafe_allow_html=True,
            )

        # ── RIGHT: vendor recommendations ─────────────────────────────────────
        with col_recs:
            st.markdown("<div class='section-label'>📋 Vendor Recommendations</div>", unsafe_allow_html=True)

            ost_rec = v(data, "ost_recommendation")
            dga_rec = v(data, "dga_recommendation")
            overall = v(data, "recommendation")
            is_oltc = "OLTC" in str(data.get("sampling_point","")).upper()

            # OST
            st.markdown(
                f"<div class='rec-box rec-ost'>"
                f"<strong>OST Recommendation</strong>"
                f"{ost_rec if ost_rec != '—' else '<em>Not found in report</em>'}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # DGA
            is_ns = is_oltc and dga_rec != "—" and "not specified" in dga_rec.lower()
            dga_display = (dga_rec + " <em>(Limits NS — OLTC sample)</em>") if is_ns \
                          else (dga_rec if dga_rec != "—" else "<em>Not found in report</em>")
            dga_cls = "rec-warn" if is_ns else "rec-dga"
            st.markdown(
                f"<div class='rec-box {dga_cls}'>"
                f"<strong>DGA Recommendation</strong>{dga_display}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Overall
            st.markdown(
                f"<div class='rec-box rec-overall'>"
                f"<strong>Overall Recommendation</strong>"
                f"{overall if overall != '—' else '<em>Not found in report</em>'}"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.divider()

        # ── Full details (collapsible) ────────────────────────────────────────
        with st.expander("🔍 Full Report Details", expanded=False):

            # Equipment identity
            st.markdown("<div class='section-label'>🏭 Equipment Identity</div>", unsafe_allow_html=True)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Equipment",       v(data, "equipment_designation") or "—")
            c2.metric("Owner / CSS",     v(data,"css_name") if v(data,"css_name") != "—" else v(data,"owner"))
            c3.metric("Manufacturer",    v(data, "manufacturer"))
            c4.metric("Mfr. Serial No.", v(data, "mfr_serial_no") or "—")

            c1b, c2b, c3b, c4b = st.columns(4)
            c1b.metric("Rating",         v(data, "rating"))
            c2b.metric("Voltage Class",  v(data, "voltage_class"))
            c3b.metric("Voltage Ratio",  v(data, "voltage_ratio"))
            c4b.metric("Sampling Point", v(data, "sampling_point"))

            # Report metadata
            st.markdown("<div class='section-label'>📋 Report Metadata</div>", unsafe_allow_html=True)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Report No.",      v(data, "report_no"))
            m2.metric("Report Date",     v(data, "report_date"))
            m3.metric("Sampling Date",   v(data, "sampling_date"))
            m4.metric("Reason for Sampling", v(data, "reason_for_sampling"))

            m1b, m2b, m3b, m4b = st.columns(4)
            m1b.metric("Weather",        v(data, "weather_condition"))
            m2b.metric("Oil Temp",       v(data, "oil_temperature", "°C"))
            m3b.metric("Winding Temp",   v(data, "winding_temperature", "°C"))
            m4b.metric("Condition on Receipt", v(data, "condition_on_receipt"))

            # OST table
            st.markdown("<div class='section-label'>🧪 Oil Screening Tests (OST) — Full Results</div>", unsafe_allow_html=True)
            st.markdown(render_ost_table(data), unsafe_allow_html=True)

            # DGA raw
            st.markdown("<div class='section-label'>🔬 Dissolved Gas Analysis (DGA)</div>", unsafe_allow_html=True)
            dga_keys = [
                ("H₂",  "h2"),  ("O₂",  "o2"),  ("N₂",  "n2"),
                ("CO",  "co"),  ("CH₄", "ch4"),  ("CO₂", "co2"),
                ("C₂H₂","c2h2"),("C₂H₄","c2h4"),("C₂H₆","c2h6"),
                ("C₃H₆","c3h6"),("C₃H₈","c3h8"),
                ("TDCG","tdcg"),("TGC", "tgc"),
            ]
            dga_col1, dga_col2, dga_col3, dga_col4 = st.columns(4)
            cols = [dga_col1, dga_col2, dga_col3, dga_col4]
            for idx, (label, key) in enumerate(dga_keys):
                cols[idx % 4].metric(label, v(data, key, "ppm"))

            # Raw JSON
            with st.expander("🗂 Raw Extracted JSON", expanded=False):
                st.json(data)


# ─────────────────────────────────────────────────────────────────────────────
# Grouped sections
# ─────────────────────────────────────────────────────────────────────────────

TIERS = [
    ("CRITICAL", "CRITICAL — Exceeds 40 ppm limit",        "critical"),
    ("CAUTION",  "CAUTION  — Approaching limit (30–40 ppm)","caution"),
    ("GOOD",     "GOOD     — Within safe range (<30 ppm)",  "good"),
]

shown = 0
for tier, label_str, css in TIERS:
    group = [r for r in all_reports if r["severity"] == tier]
    if not group:
        continue
    shown += 1
    st.markdown(
        f"<div class='sev-header sev-{css}'>"
        f"{SEVERITY_ICON[tier]}  {label_str} &nbsp;({len(group)})"
        f"</div>",
        unsafe_allow_html=True,
    )
    for rep in group:
        render_card(rep)

# Errors
err_group = [r for r in all_reports if r["severity"] == "ERR"]
if err_group:
    st.markdown("### ❌ Parse Errors")
    for rep in err_group:
        st.error(f"**{rep['name']}** — {rep.get('error','unknown error')}")