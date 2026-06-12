"""
app.py — Transformer DGA Dashboard  (v3)
=========================================
Run:  streamlit run app.py

Classification uses ONLY the vendor recommendation text.
Severity tiers:
  🔴 BAD    — danger keywords in recommendation
  🟠 MILD   — caution keywords in recommendation
  🟢 GOOD   — normal/clear keywords OR no flags
"""

import os, tempfile
import streamlit as st
import pandas as pd

from transformer_oil_extractor_v7 import parse_pdf
from duval_triangle_v7 import generate_duval_image, FAULT_MEANINGS, classify_duval

# ─────────────────────────────────────────────────────────────────────────────
# Keyword lists
# ─────────────────────────────────────────────────────────────────────────────
DANGER_KEYWORDS = [
    "immediate", "withdraw", "take out of service", "urgent", "critical",
    "serious", "severe", "shutdown", "isolate", "emergency", "replace oil",
    "not fit", "unfit", "condemned", "rejected",
]
MILD_KEYWORDS = [
    "attention", "caution", "investigate", "follow up", "retest",
    "resample", "monitor closely", "elevated", "abnormal", "concern",
    "trending", "review", "inspect", "check",
]
NORMAL_KEYWORDS = [
    "fit for service", "fit to be used", "no fault", "no overheating",
    "acceptable", "within acceptable", "normal operation", "no action",
    "conform", "satisfactory", "good oil",
]

SEVERITY_ICON  = {"BAD": "🔴", "MILD": "🟠", "GOOD": "🟢"}
SEVERITY_ORDER = {"BAD": 0, "MILD": 1, "GOOD": 2, "ERR": 3}
SEVERITY_COLOR = {"BAD": "#c0392b", "MILD": "#d35400", "GOOD": "#27ae60"}
SEVERITY_BG    = {"BAD": "#fff0f0", "MILD": "#fff8f0", "GOOD": "#f0fff4"}

ZONE_EMOJI = {
    "PD": "🔵", "T1": "🟢",
    "T2": "🟠", "T3": "🔴",
    "D1": "🟠", "D2": "🔴", "DT": "🔴",
}

GAS_TABLE = [
    ("H₂",   "h2",   50,   300),
    ("O₂",   "o2",   0,    0),
    ("N₂",   "n2",   0,    0),
    ("CO",   "co",   400,  1000),
    ("CH₄",  "ch4",  30,   120),
    ("CO₂",  "co2",  3800, 9000),
    ("C₂H₂", "c2h2", 2,    10),
    ("C₂H₄", "c2h4", 60,   200),
    ("C₂H₆", "c2h6", 20,   90),
    ("C₃H₆", "c3h6", 0,    0),
    ("C₃H₈", "c3h8", 0,    0),
]

# ─────────────────────────────────────────────────────────────────────────────
# Page config + global CSS
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Transformer DGA Dashboard",
    page_icon="⚡",
    layout="wide",
)

st.markdown("""
<style>
/* ── General typography ── */
html, body, [class*="css"] { font-family: 'Segoe UI', Arial, sans-serif; }

/* ── Hide default Streamlit header decoration ── */
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

/* ── Hero banner ── */
.hero-banner {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    border-radius: 12px;
    padding: 1.6rem 2rem 1.4rem;
    margin-bottom: 1.5rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.35);
}
.hero-banner h1 {
    color: #e0e6ff;
    font-size: 2rem;
    font-weight: 700;
    margin: 0 0 0.2rem;
    letter-spacing: -0.5px;
}
.hero-banner p  { color: #8fa8d8; font-size: 0.92rem; margin: 0; }

/* ── Summary metric cards ── */
.metric-row { display: flex; gap: 12px; margin-bottom: 1.4rem; flex-wrap: wrap; }
.metric-card {
    flex: 1 1 110px;
    border-radius: 10px;
    padding: 0.9rem 1rem;
    text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
}
.metric-card .mc-num  { font-size: 2rem; font-weight: 700; line-height: 1.1; }
.metric-card .mc-lbl  { font-size: 0.78rem; font-weight: 600; text-transform: uppercase;
                         letter-spacing: 0.06em; opacity: 0.8; margin-top: 2px; }
.mc-total  { background:#f0f4ff; color:#1a1a6e; }
.mc-bad    { background:#fff0f0; color:#c0392b; }
.mc-mild   { background:#fff8f0; color:#d35400; }
.mc-good   { background:#f0fff4; color:#27ae60; }
.mc-err    { background:#f8f8f8; color:#555; }

/* ── Section severity header ── */
.sev-header {
    border-radius: 8px;
    padding: 0.7rem 1.2rem;
    margin: 1rem 0 0.5rem;
    font-size: 1.15rem;
    font-weight: 700;
    letter-spacing: 0.02em;
}
.sev-bad  { background:#c0392b; color:#fff; }
.sev-mild { background:#d35400; color:#fff; }
.sev-good { background:#27ae60; color:#fff; }

/* ── Report expander label tweak ── */
details summary { font-size: 0.97rem !important; }

/* ── Recommendation boxes ── */
.rec-box {
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.5rem;
    font-size: 0.92rem;
    line-height: 1.5;
}
.rec-box strong { display:block; margin-bottom: 0.2rem; font-size: 0.78rem;
                  text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.75; }
.rec-ost     { background:#e8f4fd; border-left: 4px solid #2980b9; color:#1a3a5c; }
.rec-dga     { background:#eaf7ea; border-left: 4px solid #27ae60; color:#1a4a2a; }
.rec-overall { background:#f5eef8; border-left: 4px solid #8e44ad; color:#3a1a5c; }
.rec-warn    { background:#fff8e6; border-left: 4px solid #f39c12; color:#5c3a00; }

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
.verdict-bad  { background:#fff0f0; border: 1.5px solid #c0392b; color:#c0392b; }
.verdict-mild { background:#fff8f0; border: 1.5px solid #d35400; color:#d35400; }
.verdict-good { background:#f0fff4; border: 1.5px solid #27ae60; color:#27ae60; }

/* ── Section divider label ── */
.section-label {
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #888;
    margin: 1rem 0 0.4rem;
    padding-bottom: 0.3rem;
    border-bottom: 1px solid #e0e0e0;
}

/* ── Gas row styling ── */
.gas-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 5px 8px;
    border-radius: 5px;
    margin-bottom: 3px;
    font-size: 0.88rem;
}
.gas-name  { font-weight: 600; min-width: 50px; }
.gas-val   { font-weight: 700; }
.gas-norm  { background: transparent; }
.gas-mild  { background: #fff8e6; }
.gas-danger{ background: #fff0f0; }
.gas-nd    { color: #aaa; }

/* ── Metric tweaks ── */
[data-testid="stMetricValue"] { font-size: 1.05rem !important; }
[data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
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


def classify_by_vendor_rec(data: dict) -> tuple:
    texts = " ".join([
        str(data.get("recommendation", "")),
        str(data.get("ost_recommendation", "")),
        str(data.get("dga_recommendation", "")),
    ]).lower()

    for kw in DANGER_KEYWORDS:
        if kw in texts:
            return "BAD", f"'{kw}'"
    for kw in MILD_KEYWORDS:
        if kw in texts:
            return "MILD", f"'{kw}'"
    for kw in NORMAL_KEYWORDS:
        if kw in texts:
            return "GOOD", f"'{kw}'"
    return "GOOD", "no flagged keywords"


def get_duval_zone(data: dict):
    ch4  = safe_float(data.get("ch4",  0))
    c2h4 = safe_float(data.get("c2h4", 0))
    c2h2 = safe_float(data.get("c2h2", 0))
    total = ch4 + c2h4 + c2h2
    if total == 0:
        return "T1", 0.0, 0.0, 0.0
    p1 = ch4  / total * 100
    p2 = c2h4 / total * 100
    p3 = c2h2 / total * 100
    return classify_duval(p1, p2, p3), p1, p2, p3


def v(data, key, unit="") -> str:
    val = data.get(key, "")
    if not val or str(val).strip().upper() in ("ND", "NOT FOUND", "", "NS", "NA"):
        return "—"
    return f"{val} {unit}".strip()


def gas_badge(data, key, mild_lim, danger_lim):
    val  = safe_float(data.get(key, 0))
    raw  = data.get(key, "ND")
    if str(raw).strip().upper() in ("ND", "", "NS"):
        return "ND", "nd"
    if val > danger_lim > 0:
        return f"{val:.1f}", "danger"
    if val > mild_lim > 0:
        return f"{val:.1f}", "mild"
    return f"{val:.1f}", "norm"


def gas_html_table(data) -> str:
    COLOR_DOT = {"norm": "🟢", "mild": "🟠", "danger": "🔴", "nd": "⚫"}
    rows = []
    for label, key, mild_l, danger_l in GAS_TABLE:
        val_str, color = gas_badge(data, key, mild_l, danger_l)
        bg = {
            "norm":   "transparent",
            "mild":   "#fff8e6",
            "danger": "#fff0f0",
            "nd":     "transparent",
        }.get(color, "transparent")
        fc = {"norm": "#222", "mild": "#7a4000", "danger": "#900", "nd": "#aaa"}.get(color, "#222")
        dot = COLOR_DOT.get(color, "")
        rows.append(
            f"<tr style='background:{bg};'>"
            f"<td style='padding:4px 8px;font-weight:600;color:#444;width:60px'>{label}</td>"
            f"<td style='padding:4px 8px;font-weight:700;color:{fc};text-align:right;width:80px'>{val_str}</td>"
            f"<td style='padding:4px 4px;text-align:center;font-size:0.85rem'>{dot}</td>"
            f"<td style='padding:4px 8px;font-size:0.78rem;color:#888'>ppm</td>"
            f"</tr>"
        )
    return (
        "<table style='width:100%;border-collapse:collapse;font-size:0.9rem;'>"
        "<thead><tr style='border-bottom:2px solid #e0e0e0'>"
        "<th style='text-align:left;padding:4px 8px;color:#555;font-size:0.75rem;text-transform:uppercase;letter-spacing:.06em'>Gas</th>"
        "<th style='text-align:right;padding:4px 8px;color:#555;font-size:0.75rem;text-transform:uppercase;letter-spacing:.06em'>Value</th>"
        "<th></th><th></th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Hero banner
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="hero-banner">
  <h1>⚡ Transformer Oil DGA Dashboard</h1>
  <p>Dissolved Gas Analysis · Vendor-recommendation classification · Sorted BAD → MILD → GOOD</p>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────────────────────────

pdfs = st.file_uploader(
    "📄 Upload DGA Report PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    help="Select one or more lab report PDFs (TRU-FIL / SGS / CPRI format)",
)

if not pdfs:
    st.markdown("""
    <div style="border:2px dashed #ccd;border-radius:10px;padding:2rem;text-align:center;color:#8899bb;margin-top:0.5rem;">
      <div style="font-size:2.5rem;margin-bottom:0.5rem;">📂</div>
      <div style="font-size:1.1rem;font-weight:600;">Drop PDF reports here to begin</div>
      <div style="font-size:0.88rem;margin-top:0.3rem;">Supports TRU-FIL (IS standard) and SGS / CPRI formats</div>
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
            severity, matched_kw = classify_by_vendor_rec(data)
            zone, p1, p2, p3    = get_duval_zone(data)
            img_path = os.path.join(
                tempfile.gettempdir(), f"duval_{abs(hash(key))}.png"
            )
            generate_duval_image(data, output_path=img_path)
            reports[key] = dict(
                name=pdf_file.name, data=data,
                severity=severity, matched_kw=matched_kw,
                zone=zone, pCH4=p1, pC2H4=p2, pC2H2=p3,
                img=img_path, error=None,
            )
        except Exception as exc:
            reports[key] = dict(
                name=pdf_file.name, data={}, zone="ERR",
                severity="ERR", matched_kw=str(exc),
                error=str(exc), img=None,
            )
        finally:
            os.unlink(tmp_path)
    progress.progress((i + 1) / len(pdfs), text=f"Parsed {i+1} / {len(pdfs)} …")

progress.empty()

all_reports = sorted(
    reports.values(),
    key=lambda r: SEVERITY_ORDER.get(r["severity"], 99)
)

# ─────────────────────────────────────────────────────────────────────────────
# Summary metric row
# ─────────────────────────────────────────────────────────────────────────────

n_total = len(all_reports)
n_bad   = sum(1 for r in all_reports if r["severity"] == "BAD")
n_mild  = sum(1 for r in all_reports if r["severity"] == "MILD")
n_good  = sum(1 for r in all_reports if r["severity"] == "GOOD")
n_err   = sum(1 for r in all_reports if r["severity"] == "ERR")

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
  <div class="metric-card mc-bad">
    <div class="mc-num">{n_bad}</div>
    <div class="mc-lbl">🔴 BAD</div>
  </div>
  <div class="metric-card mc-mild">
    <div class="mc-num">{n_mild}</div>
    <div class="mc-lbl">🟠 MILD</div>
  </div>
  <div class="metric-card mc-good">
    <div class="mc-num">{n_good}</div>
    <div class="mc-lbl">🟢 GOOD</div>
  </div>
  {err_card}
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Report card renderer
# ─────────────────────────────────────────────────────────────────────────────

def render_report_card(rep: dict):
    data = rep["data"]
    sev  = rep["severity"]

    if rep.get("error"):
        st.error(f"⚠️ Parse error — **{rep['name']}**: {rep['error']}")
        return

    equip   = v(data, "equipment_designation") or v(data, "css_name") or rep["name"]
    sp      = v(data, "sampling_point")
    is_oltc = "OLTC" in str(data.get("sampling_point", "")).upper()

    label   = f"{SEVERITY_ICON[sev]}  {equip}  ·  {sp}  ·  {rep['name']}"
    with st.expander(label, expanded=(sev == "BAD")):

        # ── Verdict ──────────────────────────────────────────────────────────
        vc = {"BAD":"bad","MILD":"mild","GOOD":"good"}.get(sev,"good")
        st.markdown(
            f"<div class='verdict-banner verdict-{vc}'>"
            f"{SEVERITY_ICON[sev]} Vendor Classification: <strong>{sev}</strong>"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;Triggered by {rep['matched_kw']}"
            f"</div>",
            unsafe_allow_html=True,
        )

        # ── Recommendations ──────────────────────────────────────────────────
        ost_rec = v(data, "ost_recommendation")
        dga_rec = v(data, "dga_recommendation")
        overall = v(data, "recommendation")

        st.markdown("<div class='section-label'>Vendor Recommendations</div>", unsafe_allow_html=True)
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            st.markdown(
                f"<div class='rec-box rec-ost'><strong>OST</strong>"
                f"{ost_rec if ost_rec != '—' else '<em>Not found</em>'}</div>",
                unsafe_allow_html=True,
            )
        with rc2:
            is_ns = is_oltc and dga_rec != "—" and "not specified" in dga_rec.lower()
            cls   = "rec-warn" if is_ns else "rec-dga"
            dga_display = (dga_rec + " <em>(Limits NS — OLTC sample)</em>") if is_ns else \
                          (dga_rec if dga_rec != "—" else "<em>Not found</em>")
            st.markdown(
                f"<div class='rec-box {cls}'><strong>DGA</strong>{dga_display}</div>",
                unsafe_allow_html=True,
            )
        with rc3:
            st.markdown(
                f"<div class='rec-box rec-overall'><strong>Overall</strong>"
                f"{overall if overall != '—' else '<em>Not found</em>'}</div>",
                unsafe_allow_html=True,
            )

        # ── Equipment identity ────────────────────────────────────────────────
        st.markdown("<div class='section-label'>🏭 Equipment Identity</div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Format",          data.get("fmt", "—"))
        c2.metric("Equipment",       v(data, "equipment_designation") or "—")
        c3.metric("CSS / Owner",     v(data,"css_name") if v(data,"css_name") != "—" else v(data,"owner"))
        c4.metric("Transformer No.", v(data, "transformer_no"))

        c1b, c2b, c3b, c4b = st.columns(4)
        c1b.metric("Manufacturer",   v(data, "manufacturer"))
        c2b.metric("Rating",         v(data, "rating"))
        c3b.metric("Voltage Class",  v(data, "voltage_class"))
        c4b.metric("Sampling Point", v(data, "sampling_point"))

        # ── Report metadata ───────────────────────────────────────────────────
        st.markdown("<div class='section-label'>📋 Report Metadata</div>", unsafe_allow_html=True)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Report No.",    v(data, "report_no"))
        m2.metric("Report Date",   v(data, "report_date"))
        m3.metric("Sampling Date", v(data, "sampling_date"))
        m4.metric("Weather",       v(data, "weather_condition"))

        # ── OST ──────────────────────────────────────────────────────────────
        st.markdown("<div class='section-label'>🧪 Oil Sampling Test (OST)</div>", unsafe_allow_html=True)
        t1, t2, t3, t4 = st.columns(4)
        t1.metric("BDV",            v(data, "bdv",            "kV"))
        t2.metric("Water Content",  v(data, "water",          "ppm"))
        t3.metric("IFT",            v(data, "ift",            "N/m"))
        t4.metric("Neutralization", v(data, "neutralization", "mgKOH/g"))

        t1b, t2b, t3b, t4b = st.columns(4)
        t1b.metric("Density",      v(data, "density",  "g/cm³"))
        t2b.metric("Color (ASTM)", v(data, "color"))
        t3b.metric("Flash Point",  v(data, "flash",    "°C"))
        t4b.metric("OQI",          v(data, "oqi"))

        # ── DGA + Duval ───────────────────────────────────────────────────────
        st.markdown("<div class='section-label'>🔬 Dissolved Gas Analysis (DGA)</div>", unsafe_allow_html=True)
        left, right = st.columns([3, 2])

        with left:
            st.markdown("**Duval Triangle**  *(informational)*")
            if rep["img"] and os.path.exists(rep["img"]):
                zone_lbl = rep.get("zone", "—")
                fault_desc = FAULT_MEANINGS.get(zone_lbl, "")
                st.image(rep["img"], use_container_width=True)
                if zone_lbl and fault_desc:
                    st.caption(f"{ZONE_EMOJI.get(zone_lbl,'●')} Zone **{zone_lbl}** — {fault_desc}")
            else:
                st.warning("Duval image unavailable.")

        with right:
            st.markdown("**Gas Concentrations**")
            st.markdown(gas_html_table(data), unsafe_allow_html=True)
            st.markdown(
                f"<div style='margin-top:8px;padding:6px 8px;background:#f0f4ff;"
                f"border-radius:6px;font-size:0.88rem;'>"
                f"<b>TDCG</b>: {v(data,'tdcg','ppm')}&nbsp;&nbsp;|&nbsp;&nbsp;"
                f"<b>TGC</b>: {v(data,'tgc','v/v %')}</div>",
                unsafe_allow_html=True,
            )

        with st.expander("🗂 Raw Extracted Data (JSON)", expanded=False):
            st.json(data)


# ─────────────────────────────────────────────────────────────────────────────
# Grouped sections
# ─────────────────────────────────────────────────────────────────────────────

TIERS = [
    ("BAD",  "BAD — Immediate Attention Required", "bad"),
    ("MILD", "MILD — Monitor / Investigate",       "mild"),
    ("GOOD", "GOOD — No Action Required",          "good"),
]

for tier, label, css in TIERS:
    group = [r for r in all_reports if r["severity"] == tier]
    if not group:
        continue
    st.markdown(
        f"<div class='sev-header sev-{css}'>{SEVERITY_ICON[tier]}  {label} &nbsp;({len(group)})</div>",
        unsafe_allow_html=True,
    )
    for rep in group:
        render_report_card(rep)

# Errors
err_group = [r for r in all_reports if r["severity"] == "ERR"]
if err_group:
    st.markdown("### ❌ Parse Errors")
    for rep in err_group:
        st.error(f"**{rep['name']}** — {rep.get('error','unknown error')}")