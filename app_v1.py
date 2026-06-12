"""
app.py — Transformer DGA Dashboard
====================================
Run:  streamlit run app.py

Classification uses TWO independent signals, then takes the WORSE of the two:
  1. Duval Triangle  (from CH₄, C₂H₄, C₂H₂ percentages)
  2. Recommendation + gas thresholds  (catches OLTC "NS" edge-cases)

Severity tiers:
  🔴 DANGER  — Duval D2/DT/T3  OR  gas thresholds exceeded  OR  rec has danger keywords
  🟠 MILD    — Duval D1/T2/PD  OR  gas thresholds mildly elevated  OR  rec has caution keywords
  🟢 NORMAL  — all clear on both signals
"""

import os, tempfile
import streamlit as st
import pandas as pd

from transformer_oil_extractor_v7 import parse_pdf
from duval_triangle_v7 import generate_duval_image, FAULT_MEANINGS, classify_duval

# ─────────────────────────────────────────────────────────────────────────────
# IEC / BIS 10593-2018 gas thresholds (ppm) for main tank oil
# OLTC has no standard limits — we apply main-tank thresholds anyway as a
# safety override, but mark the source clearly.
# ─────────────────────────────────────────────────────────────────────────────
GAS_LIMITS = {
    # gas_key: (mild_threshold, danger_threshold)
    "h2":   (100,  300),
    "ch4":  ( 30,  120),
    "c2h2": (  2,   10),   # acetylene is very sensitive
    "c2h4": ( 60,  200),
    "c2h6": ( 20,   90),
    "co":   (400, 1000),
    "co2":  (3800, 9000),
    "tdcg": (720, 1920),
}

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

# Duval zone → severity
DANGER_ZONES = {"D2", "DT", "T3"}
MILD_ZONES   = {"D1", "T2", "PD"}

ZONE_EMOJI = {
    "PD": "🔵", "T1": "🟢",
    "T2": "🟠", "T3": "🔴",
    "D1": "🟠", "D2": "🔴", "DT": "🔴",
}
SEVERITY_ICON = {"DANGER": "🔴", "MILD": "🟠", "NORMAL": "🟢"}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_float(v) -> float:
    try:
        s = str(v).strip().upper()
        return 0.0 if s in ("ND", "NOT FOUND", "", "NS", "NA") else float(s)
    except (TypeError, ValueError):
        return 0.0


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


def classify_by_rec(data: dict) -> tuple:
    """
    Returns (severity, reason_string) based on recommendation text + gas thresholds.
    severity: 'DANGER' | 'MILD' | 'NORMAL'
    """
    texts = " ".join([
        str(data.get("recommendation", "")),
        str(data.get("ost_recommendation", "")),
        str(data.get("dga_recommendation", "")),
    ]).lower()

    # Keyword-based classification
    for kw in DANGER_KEYWORDS:
        if kw in texts:
            return "DANGER", f"Recommendation keyword: '{kw}'"

    # Gas threshold check — independent of limits stated in report
    triggered_danger = []
    triggered_mild   = []
    for gas, (mild_lim, danger_lim) in GAS_LIMITS.items():
        val = safe_float(data.get(gas, 0))
        if val > danger_lim:
            triggered_danger.append(f"{gas.upper()}={val:.1f} (>{danger_lim})")
        elif val > mild_lim:
            triggered_mild.append(f"{gas.upper()}={val:.1f} (>{mild_lim})")

    if triggered_danger:
        return "DANGER", "Gas thresholds exceeded: " + ", ".join(triggered_danger)
    if triggered_mild:
        return "MILD", "Elevated gases: " + ", ".join(triggered_mild)

    for kw in MILD_KEYWORDS:
        if kw in texts:
            return "MILD", f"Recommendation keyword: '{kw}'"

    return "NORMAL", "All gas levels acceptable, recommendation clear"


def combined_severity(duval_zone: str, rec_severity: str) -> str:
    """Takes the WORSE of Duval and Recommendation severity."""
    rank = {"DANGER": 2, "MILD": 1, "NORMAL": 0}
    duval_sev = "DANGER" if duval_zone in DANGER_ZONES else \
                "MILD"   if duval_zone in MILD_ZONES   else "NORMAL"
    return "DANGER" if max(rank[duval_sev], rank[rec_severity]) == 2 else \
           "MILD"   if max(rank[duval_sev], rank[rec_severity]) == 1 else "NORMAL"


def v(data, key, unit="") -> str:
    val = data.get(key, "")
    if not val or str(val).strip().upper() in ("ND", "NOT FOUND", "", "NS", "NA"):
        return "—"
    return f"{val} {unit}".strip()


def gas_badge(data, key, mild_lim, danger_lim):
    """Return (value_str, color) for a gas metric."""
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
st.caption("Multi-signal classification: Duval Triangle + Recommendation text + Gas thresholds")

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
            data  = parse_pdf(tmp_path)
            zone, p1, p2, p3 = get_duval_zone(data)
            rec_sev, rec_reason = classify_by_rec(data)
            final_sev = combined_severity(zone, rec_sev)

            duval_sev = "DANGER" if zone in DANGER_ZONES else \
                        "MILD"   if zone in MILD_ZONES   else "NORMAL"

            img_path = os.path.join(
                tempfile.gettempdir(), f"duval_{abs(hash(key))}.png"
            )
            generate_duval_image(data, output_path=img_path)

            reports[key] = {
                "name":       pdf_file.name,
                "data":       data,
                "zone":       zone,
                "pCH4":       p1, "pC2H4": p2, "pC2H2": p3,
                "duval_sev":  duval_sev,
                "rec_sev":    rec_sev,
                "rec_reason": rec_reason,
                "severity":   final_sev,
                "img":        img_path,
                "error":      None,
            }
        except Exception as exc:
            reports[key] = {
                "name": pdf_file.name, "data": {}, "zone": "ERR",
                "severity": "ERR", "error": str(exc), "img": None,
                "duval_sev": "ERR", "rec_sev": "ERR",
                "rec_reason": str(exc),
            }
        finally:
            os.unlink(tmp_path)
    progress.progress((i + 1) / len(pdfs), text=f"Parsed {i+1}/{len(pdfs)} …")

progress.empty()

all_reports = list(reports.values())

# ─────────────────────────────────────────────────────────────────────────────
# Summary bar
# ─────────────────────────────────────────────────────────────────────────────

n_total  = len(all_reports)
n_danger = sum(1 for r in all_reports if r["severity"] == "DANGER")
n_mild   = sum(1 for r in all_reports if r["severity"] == "MILD")
n_normal = sum(1 for r in all_reports if r["severity"] == "NORMAL")
n_err    = sum(1 for r in all_reports if r["severity"] == "ERR")

st.markdown("---")
cols = st.columns(5)
cols[0].metric("📂 Total",   n_total)
cols[1].metric("🔴 DANGER",  n_danger)
cols[2].metric("🟠 MILD",    n_mild)
cols[3].metric("🟢 NORMAL",  n_normal)
if n_err: cols[4].metric("❌ Errors", n_err)

# ─────────────────────────────────────────────────────────────────────────────
# Flagged reports
# ─────────────────────────────────────────────────────────────────────────────

flagged = [r for r in all_reports if r["severity"] in ("DANGER", "MILD")]

if not flagged:
    st.success("✅ All transformers NORMAL — no issues detected.")
    # Still show normal list below
else:
    st.markdown("---")
    st.subheader(f"⚠️ Flagged Transformers ({len(flagged)})")

    # Number buttons
    if "sel" not in st.session_state:
        st.session_state.sel = 0

    st.markdown("**Click a number to inspect that report:**")
    btn_cols = st.columns(min(len(flagged), 10))
    for i, rep in enumerate(flagged):
        icon = "🔴" if rep["severity"] == "DANGER" else "🟠"
        if btn_cols[i % 10].button(f"{icon} {i+1}", key=f"btn_{i}",
                                   help=rep["name"], use_container_width=True):
            st.session_state.sel = i

    idx = min(st.session_state.sel, len(flagged) - 1)

    # Index table
    with st.expander("📋 Flagged report index", expanded=False):
        rows = []
        for i, rep in enumerate(flagged):
            d = rep["data"]
            sp = d.get("sampling_point","")
            is_oltc = "OLTC" in sp.upper() if sp else False
            rows.append({
                "#":             i + 1,
                "Final Severity":f"{SEVERITY_ICON[rep['severity']]} {rep['severity']}",
                "Duval Zone":    f"{ZONE_EMOJI.get(rep['zone'],'')} {rep['zone']}",
                "Duval Signal":  rep["duval_sev"],
                "Rec Signal":    rep["rec_sev"],
                "Override Reason": rep["rec_reason"],
                "Sampling Point":sp,
                "OLTC?":         "⚠️ Yes" if is_oltc else "No",
                "File":          rep["name"],
                "Equipment":     v(d,"equipment_designation") or v(d,"css_name"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ── Detail card ──────────────────────────────────────────────────────────

    rep  = flagged[idx]
    data = rep["data"]
    is_oltc = "OLTC" in str(data.get("sampling_point","")).upper()

    sev_color = "#FF4B4B" if rep["severity"] == "DANGER" else "#FFA500"
    st.markdown(
        f"<h3 style='color:{sev_color}'>Report {idx+1}/{len(flagged)} — "
        f"{SEVERITY_ICON[rep['severity']]} {rep['severity']} &nbsp;|&nbsp; "
        f"{ZONE_EMOJI.get(rep['zone'],'')} {rep['zone']}: "
        f"{FAULT_MEANINGS.get(rep['zone'],'')}</h3>",
        unsafe_allow_html=True,
    )
    st.caption(f"📄 {rep['name']}  {'  ⚠️ OLTC sample — lab limits not specified' if is_oltc else ''}")

    if rep.get("error"):
        st.error(f"Parse error: {rep['error']}")
        st.stop()

    # ── Classification breakdown ─────────────────────────────────────────────

    st.markdown("---")
    st.subheader("🔎 Classification Breakdown")

    ca, cb, cc = st.columns(3)
    with ca:
        st.markdown("**Duval Triangle Signal**")
        d_icon = SEVERITY_ICON.get(rep["duval_sev"],"●")
        st.markdown(f"### {d_icon} {rep['duval_sev']}")
        st.markdown(f"Zone **{rep['zone']}** — {FAULT_MEANINGS.get(rep['zone'],'')}")
        if rep["pCH4"] + rep["pC2H4"] + rep["pC2H2"] > 0:
            st.markdown(
                f"CH₄={rep['pCH4']:.1f}%  C₂H₄={rep['pC2H4']:.1f}%  C₂H₂={rep['pC2H2']:.1f}%"
            )
        else:
            st.caption("Insufficient Duval gases for triangle plot")
    with cb:
        st.markdown("**Recommendation + Gas Signal**")
        r_icon = SEVERITY_ICON.get(rep["rec_sev"],"●")
        st.markdown(f"### {r_icon} {rep['rec_sev']}")
        st.markdown(f"*{rep['rec_reason']}*")
        if is_oltc:
            st.warning("OLTC sample — lab DGA limits 'Not Specified'. "
                       "Gas thresholds applied using main-tank IEC limits as safety override.")
    with cc:
        st.markdown("**Final Combined Severity**")
        f_icon = SEVERITY_ICON.get(rep["severity"],"●")
        sev_c = "#FF4B4B" if rep["severity"]=="DANGER" else \
                "#FFA500" if rep["severity"]=="MILD" else "#2ecc71"
        st.markdown(
            f"<h2 style='color:{sev_c}'>{f_icon} {rep['severity']}</h2>",
            unsafe_allow_html=True
        )
        st.caption("Worst of Duval signal and Recommendation signal")

    # ── Recommendation texts ─────────────────────────────────────────────────

    st.markdown("---")
    st.subheader("📝 Lab Recommendations")

    ost = v(data, "ost_recommendation")
    dga_rec = v(data, "dga_recommendation")
    overall = v(data, "recommendation")

    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**OST Test Recommendation**")
        st.info(ost if ost != "—" else "Not found in report")
        st.markdown("**DGA Test Recommendation**")
        if is_oltc and dga_rec != "—" and "not specified" in dga_rec.lower():
            st.warning(f"{dga_rec}  ← Limits NS; independent gas thresholds applied above")
        else:
            st.info(dga_rec if dga_rec != "—" else "Not found in report")
    with cc2:
        st.markdown("**Overall Recommendation**")
        st.success(overall if overall != "—" else "Not found in report")

    # ── Equipment Identity ────────────────────────────────────────────────────

    st.markdown("---")
    st.subheader("🏭 Equipment Identity")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Format",         data.get("fmt","—"))
    c2.metric("Equipment",      v(data,"equipment_designation") or "—")
    c3.metric("CSS / Owner",    v(data,"css_name") if v(data,"css_name")!="—" else v(data,"owner"))
    c4.metric("Transformer No.",v(data,"transformer_no"))

    c1b,c2b,c3b,c4b = st.columns(4)
    c1b.metric("Manufacturer",  v(data,"manufacturer"))
    c2b.metric("Mfr. Sl No.",   v(data,"manufacturer_slno"))
    c3b.metric("Rating",        v(data,"rating"))
    c4b.metric("Voltage Class", v(data,"voltage_class"))

    c1c,c2c,c3c,c4c = st.columns(4)
    c1c.metric("Cooling",       v(data,"cooling"))
    c2c.metric("Mfg. Year",     v(data,"manufacturing_year"))
    c3c.metric("Oil Type",      v(data,"oil_type"))
    c4c.metric("Sampling Point",v(data,"sampling_point"))

    # ── Report Metadata ───────────────────────────────────────────────────────

    st.markdown("---")
    st.subheader("📋 Report Metadata")

    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Report No.",    v(data,"report_no"))
    c2.metric("Sample ID",     v(data,"sample_id"))
    c3.metric("Report Date",   v(data,"report_date"))
    c4.metric("Sampling Date", v(data,"sampling_date"))
    c5.metric("Weather",       v(data,"weather_condition"))
    if v(data,"installation_location") != "—":
        st.markdown(f"**Location:** {v(data,'installation_location')}")

    # ── OST ───────────────────────────────────────────────────────────────────

    st.markdown("---")
    st.subheader("🧪 Oil Screening Tests (OST)")

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("BDV",            v(data,"bdv","kV"))
    c2.metric("Water Content",  v(data,"water","ppm"))
    c3.metric("IFT",            v(data,"ift","N/m"))
    c4.metric("Neutralization", v(data,"neutralization","mgKOH/g"))

    c1b,c2b,c3b,c4b = st.columns(4)
    c1b.metric("Density",       v(data,"density","g/cm³"))
    c2b.metric("Color (ASTM)",  v(data,"color"))
    c3b.metric("Flash Point",   v(data,"flash","°C"))
    c4b.metric("OQI",           v(data,"oqi"))

    c1c,c2c,c3c,c4c = st.columns(4)
    c1c.metric("Sp.Res @27°C",  v(data,"sp_res_27","×10¹² Ω·cm"))
    c2c.metric("Sp.Res @90°C",  v(data,"sp_res_90","×10¹² Ω·cm"))
    c3c.metric("DDF @27°C",     v(data,"ddf_27"))
    c4c.metric("DDF @90°C",     v(data,"ddf_90"))
    st.metric("Sediment",       v(data,"sediment","%"))

    # ── DGA + Duval ───────────────────────────────────────────────────────────

    st.markdown("---")
    st.subheader("🔬 Dissolved Gas Analysis (DGA)")

    left, right = st.columns([3, 2])

    COLOR_CSS = {
        "danger": "🔴", "mild": "🟠", "normal": "🟢", "off": "⚪"
    }

    with right:
        st.markdown("##### Gas Concentrations")

        gas_table = [
            ("H₂",    "h2",   50,   300),
            ("O₂",    "o2",   0,    0),
            ("N₂",    "n2",   0,    0),
            ("CO",    "co",   400,  1000),
            ("CH₄",   "ch4",  30,   120),
            ("CO₂",   "co2",  3800, 9000),
            ("C₂H₂",  "c2h2", 2,    10),
            ("C₂H₄",  "c2h4", 60,   200),
            ("C₂H₆",  "c2h6", 20,   90),
            ("C₃H₆",  "c3h6", 0,    0),
            ("C₃H₈",  "c3h8", 0,    0),
        ]

        for label, key, mild_l, danger_l in gas_table:
            val_str, color = gas_badge(data, key, mild_l, danger_l)
            icon = COLOR_CSS.get(color,"⚪")
            limit_hint = f"  *(limit >{danger_l})*" if danger_l > 0 and color == "danger" else \
                         f"  *(caution >{mild_l})*"  if mild_l  > 0 and color == "mild"   else ""
            st.write(f"{icon} **{label}** : {val_str} ppm{limit_hint}")

        st.markdown("---")
        st.write(f"**TDCG**     : {v(data,'tdcg','ppm')}")
        st.write(f"**TGC**      : {v(data,'tgc','v/v %')}")
        st.write(f"**TDCG/TGC** : {v(data,'tdcg_ratio','%')}")

    with left:
        st.markdown("##### Duval Triangle")
        if rep["img"] and os.path.exists(rep["img"]):
            st.image(rep["img"], use_container_width=True)
        else:
            st.warning("Duval Triangle image unavailable.")

    # ── Raw data ──────────────────────────────────────────────────────────────

    with st.expander("🗂 Raw Extracted Data"):
        st.json(data)

# ─────────────────────────────────────────────────────────────────────────────
# Normal transformers
# ─────────────────────────────────────────────────────────────────────────────

normal_list = [r for r in all_reports if r["severity"] == "NORMAL"]
if normal_list:
    st.markdown("---")
    with st.expander(f"🟢 Normal Transformers ({len(normal_list)}) — no action required"):
        for r in normal_list:
            d = r["data"]
            equip = v(d,"equipment_designation") or v(d,"css_name") or r["name"]
            sp = d.get("sampling_point","")
            st.write(f"✅ **{equip}** | {sp} | Zone T1 (Normal) | {r['name']}")