"""
app.py — Transformer DGA Dashboard
====================================
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
# Recommendation keyword lists
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

ZONE_EMOJI = {
    "PD": "🔵", "T1": "🟢",
    "T2": "🟠", "T3": "🔴",
    "D1": "🟠", "D2": "🔴", "DT": "🔴",
}

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
    """
    Returns (severity, matched_keyword) based solely on recommendation text.
    severity: 'BAD' | 'MILD' | 'GOOD'
    """
    texts = " ".join([
        str(data.get("recommendation", "")),
        str(data.get("ost_recommendation", "")),
        str(data.get("dga_recommendation", "")),
    ]).lower()

    for kw in DANGER_KEYWORDS:
        if kw in texts:
            return "BAD", f"keyword: '{kw}'"

    for kw in MILD_KEYWORDS:
        if kw in texts:
            return "MILD", f"keyword: '{kw}'"

    # Check normal keywords explicitly, else default GOOD
    for kw in NORMAL_KEYWORDS:
        if kw in texts:
            return "GOOD", f"keyword: '{kw}'"

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
    val = safe_float(data.get(key, 0))
    raw = data.get(key, "ND")
    if str(raw).strip().upper() in ("ND", "", "NS"):
        return "ND", "off"
    if val > danger_lim: return f"{val:.2f}", "danger"
    if val > mild_lim:   return f"{val:.2f}", "mild"
    return f"{val:.2f}", "normal"


# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Transformer DGA Dashboard",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Transformer Oil Analysis Dashboard")
st.caption("Sorted by vendor recommendation: BAD → MILD → GOOD")

# ─────────────────────────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────────────────────────

pdfs = st.file_uploader(
    "📄 Upload DGA Report PDFs (select multiple)",
    type=["pdf"],
    accept_multiple_files=True,
)

if not pdfs:
    st.info("Upload one or more PDF reports to get started.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# Parse + classify all PDFs
# ─────────────────────────────────────────────────────────────────────────────

if "reports" not in st.session_state:
    st.session_state.reports = {}

reports = st.session_state.reports
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
            zone, p1, p2, p3 = get_duval_zone(data)

            img_path = os.path.join(
                tempfile.gettempdir(), f"duval_{abs(hash(key))}.png"
            )
            generate_duval_image(data, output_path=img_path)

            reports[key] = {
                "name":       pdf_file.name,
                "data":       data,
                "severity":   severity,
                "matched_kw": matched_kw,
                "zone":       zone,
                "pCH4":       p1, "pC2H4": p2, "pC2H2": p3,
                "img":        img_path,
                "error":      None,
            }
        except Exception as exc:
            reports[key] = {
                "name": pdf_file.name, "data": {}, "zone": "ERR",
                "severity": "ERR", "matched_kw": str(exc),
                "error": str(exc), "img": None,
            }
        finally:
            os.unlink(tmp_path)
    progress.progress((i + 1) / len(pdfs), text=f"Parsed {i+1}/{len(pdfs)} …")

progress.empty()

# Sort: BAD → MILD → GOOD → ERR
all_reports = sorted(
    reports.values(),
    key=lambda r: SEVERITY_ORDER.get(r["severity"], 99)
)

# ─────────────────────────────────────────────────────────────────────────────
# Summary bar
# ─────────────────────────────────────────────────────────────────────────────

n_total  = len(all_reports)
n_bad    = sum(1 for r in all_reports if r["severity"] == "BAD")
n_mild   = sum(1 for r in all_reports if r["severity"] == "MILD")
n_good   = sum(1 for r in all_reports if r["severity"] == "GOOD")
n_err    = sum(1 for r in all_reports if r["severity"] == "ERR")

st.markdown("---")
cols = st.columns(5)
cols[0].metric("📂 Total",  n_total)
cols[1].metric("🔴 BAD",    n_bad)
cols[2].metric("🟠 MILD",   n_mild)
cols[3].metric("🟢 GOOD",   n_good)
if n_err:
    cols[4].metric("❌ Errors", n_err)

# ─────────────────────────────────────────────────────────────────────────────
# Grouped sections: BAD → MILD → GOOD
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("---")

COLOR_CSS = {"danger": "🔴", "mild": "🟠", "normal": "🟢", "off": "⚪"}

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


def render_report_card(rep, card_idx):
    data = rep["data"]
    sev  = rep["severity"]

    if rep.get("error"):
        st.error(f"Parse error in **{rep['name']}**: {rep['error']}")
        return

    sev_color = "#FF4B4B" if sev == "BAD" else "#FFA500" if sev == "MILD" else "#2ecc71"
    equip = v(data, "equipment_designation") or v(data, "css_name") or rep["name"]
    sp    = v(data, "sampling_point")
    is_oltc = "OLTC" in str(data.get("sampling_point", "")).upper()

    label = f"{SEVERITY_ICON[sev]} {equip} | {sp} | {rep['name']}"
    with st.expander(label, expanded=False):

        # ── Vendor recommendation ────────────────────────────────────────────
        st.markdown(
            f"<div style='font-size:1.1em;color:{sev_color};font-weight:bold;'>"
            f"Vendor classification: {SEVERITY_ICON[sev]} {sev} — {rep['matched_kw']}"
            f"</div>",
            unsafe_allow_html=True,
        )
        ost_rec = v(data, "ost_recommendation")
        dga_rec = v(data, "dga_recommendation")
        overall = v(data, "recommendation")
        rc1, rc2 = st.columns(2)
        with rc1:
            st.markdown("**OST Recommendation**")
            st.info(ost_rec if ost_rec != "—" else "Not found")
            st.markdown("**DGA Recommendation**")
            if is_oltc and dga_rec != "—" and "not specified" in dga_rec.lower():
                st.warning(f"{dga_rec}  ← Limits NS (OLTC sample)")
            else:
                st.info(dga_rec if dga_rec != "—" else "Not found")
        with rc2:
            st.markdown("**Overall Recommendation**")
            st.success(overall if overall != "—" else "Not found")

        # ── Equipment Identity ───────────────────────────────────────────────
        st.markdown("---")
        st.subheader("🏭 Equipment")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Format",          data.get("fmt", "—"))
        c2.metric("Equipment",       v(data, "equipment_designation") or "—")
        c3.metric("CSS / Owner",     v(data, "css_name") if v(data, "css_name") != "—" else v(data, "owner"))
        c4.metric("Transformer No.", v(data, "transformer_no"))

        c1b, c2b, c3b, c4b = st.columns(4)
        c1b.metric("Manufacturer",   v(data, "manufacturer"))
        c2b.metric("Rating",         v(data, "rating"))
        c3b.metric("Voltage Class",  v(data, "voltage_class"))
        c4b.metric("Sampling Point", v(data, "sampling_point"))

        # ── Report Metadata ──────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("📋 Report Metadata")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Report No.",    v(data, "report_no"))
        m2.metric("Report Date",   v(data, "report_date"))
        m3.metric("Sampling Date", v(data, "sampling_date"))
        m4.metric("Weather",       v(data, "weather_condition"))

        # ── OST ─────────────────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("🧪 OST")
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

        # ── DGA + Duval ──────────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("🔬 DGA")
        left, right = st.columns([3, 2])

        with left:
            st.markdown("##### Duval Triangle")
            if rep["img"] and os.path.exists(rep["img"]):
                st.image(rep["img"], use_container_width=True)
            else:
                st.warning("Duval image unavailable.")

        with right:
            st.markdown("##### Gas Concentrations")
            for label, key, mild_l, danger_l in GAS_TABLE:
                val_str, color = gas_badge(data, key, mild_l, danger_l)
                icon = COLOR_CSS.get(color, "⚪")
                st.write(f"{icon} **{label}** : {val_str} ppm")
            st.markdown("---")
            st.write(f"**TDCG**  : {v(data, 'tdcg', 'ppm')}")
            st.write(f"**TGC**   : {v(data, 'tgc',  'v/v %')}")

        with st.expander("🗂 Raw Extracted Data"):
            st.json(data)


for tier, label, header_color in [
    ("BAD",  "BAD — Immediate Attention Required",  "#FF4B4B"),
    ("MILD", "MILD — Monitor / Investigate",        "#FFA500"),
    ("GOOD", "GOOD — No Action Required",           "#2ecc71"),
]:
    group = [r for r in all_reports if r["severity"] == tier]
    if not group:
        continue

    st.markdown(
        f"<h2 style='color:{header_color}'>{SEVERITY_ICON[tier]} {label} ({len(group)})</h2>",
        unsafe_allow_html=True,
    )

    for idx, rep in enumerate(group):
        render_report_card(rep, idx)

    st.markdown("---")

# Error group
err_group = [r for r in all_reports if r["severity"] == "ERR"]
if err_group:
    st.markdown("### ❌ Parse Errors")
    for rep in err_group:
        st.error(f"**{rep['name']}** — {rep.get('error', 'unknown error')}")