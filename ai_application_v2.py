import streamlit as st
import tempfile
import json
from openai import OpenAI
from transformer_oil_extractor_v7 import parse_pdf

# =====================================================
# PAGE CONFIG
# =====================================================

st.set_page_config(
    page_title="Transformer Condition Dashboard",
    page_icon="⚡",
    layout="wide"
)

# =====================================================
# SIDEBAR — API KEY
# =====================================================

with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.text_input(
        "Groq Key",
        type="password",
        placeholder="gsk_...",
        help="Get a free key at console.groq.com"
    )
    st.caption("Your key is never stored.")
    st.markdown("---")
    st.markdown("**Model:** llama-3.3-70b-versatile")
    st.markdown("**Provider:** [Groq](https://console.groq.com)")

# =====================================================
# TITLE
# =====================================================

st.title("⚡ Transformer Condition Dashboard")
st.write("Upload transformer oil reports — AI reads gas values AND recommendations, classifies each transformer, and explains where it disagrees with the vendor.")

# =====================================================
# SESSION STATE
# =====================================================

if "reports" not in st.session_state:
    st.session_state.reports = []

if "selected" not in st.session_state:
    st.session_state.selected = None

# =====================================================
# GAS THRESHOLDS  (IEC 60599 typical limits)
# =====================================================

GAS_LIMITS = {
    "h2":   ("Hydrogen (H2)",        100,  "ppm"),
    "ch4":  ("Methane (CH4)",         30,  "ppm"),
    "c2h2": ("Acetylene (C2H2)",       3,  "ppm"),
    "c2h4": ("Ethylene (C2H4)",       60,  "ppm"),
    "c2h6": ("Ethane (C2H6)",         20,  "ppm"),
    "co":   ("Carbon Monoxide (CO)", 400,  "ppm"),
    "co2":  ("Carbon Dioxide (CO2)",3800,  "ppm"),
    "tdcg": ("TDCG",                 720,  "ppm"),
}

# =====================================================
# BUILD FULL PROMPT — gases + recommendations
# =====================================================

def build_prompt(data: dict) -> str:

    def gas(key):
        val = data.get(key, "ND")
        if val in ("", "ND", None):
            return "ND"
        try:
            return float(val)
        except:
            return val

    gases_block = "\n".join([
        f"  {info[0]}: {gas(k)} {info[2]}  (limit: {info[1]} {info[2]})"
        for k, info in GAS_LIMITS.items()
    ])

    return f"""
Classification priority:

1. Read and understand the vendor recommendations.
2. Determine the vendor's intended severity.
3. Use DGA values and OST values only to validate the recommendation.
4. Final classification must reflect the actual recommendation given by the vendor.

Map recommendations as follows:

GOOD:
- Fit for service
- Normal
- Satisfactory
- No action required
- Continue in service
- Healthy transformer

MILD:
- Monitor
- Observe trend
- Retest
- Periodic monitoring
- Follow-up testing
- Condition watch
- Slight deterioration

BAD:
- Immediate action required
- Critical
- Investigate urgently
- Internal fault suspected
- Oil treatment required
- Filtration required
- Replacement recommended
- Shutdown recommended
- Major fault
- Attention required

If recommendation text is unclear:
Use DGA and OST values to determine severity.

Return:

{
  "classification": "GOOD" | "MILD" | "BAD",
  "vendor_severity": "GOOD" | "MILD" | "BAD",
  "ai_reasoning": "...",
  "vendor_recommendation_summary": "...",
  "vendor_agreement": true | false,
  "disagreement_reason": "..."
}
"""


# =====================================================
# CALL GROQ
# =====================================================

def classify(data: dict, client: OpenAI) -> dict:
    prompt = build_prompt(data)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# =====================================================
# GAS STATUS HELPER
# =====================================================

def gas_status(data: dict):
    rows = []
    for key, (label, limit, unit) in GAS_LIMITS.items():
        val_str = data.get(key, "ND")
        try:
            val = float(val_str)
            pct = round(val / limit * 100, 1)
            if val >= limit:
                status = "🔴 OVER LIMIT"
            elif val >= limit * 0.7:
                status = "🟠 Elevated"
            else:
                status = "🟢 Normal"
            rows.append({
                "Parameter": label,
                "Value": f"{val} {unit}",
                "Limit": f"{limit} {unit}",
                "% of Limit": pct,
                "Status": status
            })
        except:
            rows.append({
                "Parameter": label,
                "Value": "ND",
                "Limit": f"{limit} {unit}",
                "% of Limit": "—",
                "Status": "⚪ No Data"
            })
    return rows


# =====================================================
# FILE UPLOAD
# =====================================================

pdfs = st.file_uploader(
    "Upload Transformer Reports (PDF)",
    type=["pdf"],
    accept_multiple_files=True
)

run_btn = st.button(
    "▶ Analyse Reports",
    type="primary",
    disabled=(not pdfs or not api_key)
)

if not api_key:
    st.info("Enter your Groq API key in the sidebar to begin.")

# =====================================================
# ANALYSIS
# =====================================================

if run_btn and pdfs and api_key:

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1"
    )

    st.session_state.reports = []
    st.session_state.selected = None

    progress = st.progress(0, text="Starting analysis...")

    for i, pdf in enumerate(pdfs):

        progress.progress(
            i / len(pdfs),
            text=f"Analysing {pdf.name} ({i+1}/{len(pdfs)})..."
        )

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf.read())
                pdf_path = tmp.name

            data = parse_pdf(pdf_path)

            equipment = data.get("equipment_designation") or pdf.name

            ai_result = classify(data, client)

            st.session_state.reports.append({
                "file": pdf.name,
                "equipment": equipment,
                "data": data,
                "classification": ai_result["vendor_severity"],   # <-- use vendor severity
                "ai_reasoning": ai_result.get("ai_reasoning",""),
                "vendor_summary": ai_result.get(
                    "vendor_recommendation_summary",""
                ),
                "vendor_agreement": ai_result.get(
                    "vendor_agreement", True
                ),
                "disagreement_reason": ai_result.get(
                    "disagreement_reason"
                ),
                "gas_rows": gas_status(data),
            })

        except Exception as e:
            st.session_state.reports.append({
                "file":           pdf.name,
                "equipment":      pdf.name,
                "data":           {},
                "classification": "BAD",
                "ai_reasoning":   f"Error during processing: {e}",
                "vendor_agreement":    True,
                "disagreement_reason": None,
                "gas_rows":       [],
                "error":          str(e),
            })

    progress.progress(1.0, text="Done!")
    progress.empty()

# =====================================================
# DASHBOARD
# =====================================================

if st.session_state.reports:

    reports = st.session_state.reports
    good  = [r for r in reports if r["classification"] == "GOOD"]
    mild  = [r for r in reports if r["classification"] == "MILD"]
    bad   = [r for r in reports if r["classification"] == "BAD"]
    disagree = [r for r in reports if not r["vendor_agreement"]]

    # ── summary metrics ──────────────────────────────

    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🟢 Good",      len(good))
    m2.metric("🟠 Mild",      len(mild))
    m3.metric("🔴 Bad",       len(bad))
    m4.metric("⚠️ AI vs Vendor disagreements", len(disagree))

    # ── three columns of cards ────────────────────────

    st.markdown("---")
    col_good, col_mild, col_bad = st.columns(3)

    def report_card(col, report, color):
        label = report["equipment"]
        fname = report["file"]
        badge = "✓" if report["vendor_agreement"] else "⚠ Disagrees"
        with col:
            if st.button(
                f"{label}\n{fname}\n{badge}",
                key=f"card_{report['file']}",
                use_container_width=True,
            ):
                st.session_state.selected = report["file"]

    with col_good:
        st.markdown("### 🟢 Good condition")
        if not good:
            st.caption("None")
        for r in good:
            report_card(col_good, r, "green")

    with col_mild:
        st.markdown("### 🟠 Mild condition")
        if not mild:
            st.caption("None")
        for r in mild:
            report_card(col_mild, r, "orange")

    with col_bad:
        st.markdown("### 🔴 Bad condition")
        if not bad:
            st.caption("None")
        for r in bad:
            report_card(col_bad, r, "red")

    # ── detail panel ─────────────────────────────────

    selected_file = st.session_state.selected
    if selected_file:

        report = next(
            (r for r in reports if r["file"] == selected_file), None
        )

        if report:
            st.markdown("---")

            clf = report["classification"]
            color_map = {"GOOD": "🟢", "MILD": "🟠", "BAD": "🔴"}

            st.header(
                f"{color_map.get(clf, '⚪')} {report['equipment']}"
            )
            st.caption(f"File: {report['file']}")

            # top tabs
            tab1, tab2, tab3, tab4 = st.tabs([
                "AI Analysis",
                "Gas Values",
                "Oil Quality",
                "Raw Data",
            ])

            # ── tab 1: AI analysis ────────────────────

            with tab1:

                a1, a2 = st.columns(2)

                with a1:
                    st.subheader("AI classification")
                    if clf == "GOOD":
                        st.success(f"**{clf}** — Fit for service")
                    elif clf == "MILD":
                        st.warning(f"**{clf}** — Monitor / investigate")
                    else:
                        st.error(f"**{clf}** — Action required")

                    st.markdown("**AI reasoning:**")
                    st.write(report["ai_reasoning"])

                with a2:
                    st.subheader("Vendor recommendations")
                    d = report["data"]
                    st.markdown(f"**Overall:** {d.get('recommendation','N/A')}")
                    st.markdown(f"**DGA:** {d.get('dga_recommendation','N/A')}")
                    st.markdown(f"**OST:** {d.get('ost_recommendation','N/A')}")

                st.markdown("---")

                if report["vendor_agreement"]:
                    st.success("✅ AI agrees with vendor recommendation.")
                else:
                    st.warning("⚠️ AI disagrees with vendor recommendation.")
                    st.markdown("**Why AI disagrees:**")
                    st.write(report["disagreement_reason"])

            # ── tab 2: gas values ─────────────────────

            with tab2:
                st.subheader("Dissolved gas analysis")
                if report["gas_rows"]:
                    st.dataframe(
                        report["gas_rows"],
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.caption("No gas data extracted.")

            # ── tab 3: oil quality ────────────────────

            with tab3:
                st.subheader("Oil quality tests")
                d = report["data"]
                ost_fields = {
                    "BDV (kV)":                d.get("bdv", "N/A"),
                    "Water content (mg/kg)":   d.get("water", "N/A"),
                    "Color":                   d.get("color", "N/A"),
                    "Density (g/cm³)":         d.get("density", "N/A"),
                    "Sp. Resistance @ 27°C":   d.get("sp_res_27", "N/A"),
                    "Sp. Resistance @ 90°C":   d.get("sp_res_90", "N/A"),
                    "DDF @ 27°C":              d.get("ddf_27", "N/A"),
                    "DDF @ 90°C":              d.get("ddf_90", "N/A"),
                    "IFT (N/m)":               d.get("ift", "N/A"),
                    "Neutralisation (mgKOH/g)":d.get("neutralization", "N/A"),
                    "Sediment & Sludge":       d.get("sediment", "N/A"),
                    "Flash Point (°C)":        d.get("flash", "N/A"),
                    "Oil Quality Index":       d.get("oqi", "N/A"),
                }
                rows = [
                    {"Parameter": k, "Value": v}
                    for k, v in ost_fields.items()
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)

            # ── tab 4: raw extracted data ─────────────

            with tab4:
                st.subheader("All extracted fields")
                d = report["data"]
                st.markdown(f"**Format detected:** `{d.get('fmt','unknown')}`")

                info_fields = {
                    "Equipment designation": d.get("equipment_designation"),
                    "Owner":                 d.get("owner"),
                    "Location":              d.get("installation_location"),
                    "Equipment type":        d.get("equipment_type"),
                    "Transformer no.":       d.get("transformer_no"),
                    "Manufacturer":          d.get("manufacturer"),
                    "Rating":                d.get("rating"),
                    "Voltage class":         d.get("voltage_class"),
                    "Voltage ratio":         d.get("voltage_ratio"),
                    "Cooling":               d.get("cooling"),
                    "Mfg. year":             d.get("manufacturing_year"),
                    "Oil type":              d.get("oil_type"),
                    "Report no.":            d.get("report_no"),
                    "Sample ID":             d.get("sample_id"),
                    "Report date":           d.get("report_date"),
                    "Sampling date":         d.get("sampling_date"),
                    "Sampling point":        d.get("sampling_point"),
                    "Weather":               d.get("weather_condition"),
                }
                rows = [
                    {"Field": k, "Value": v or "—"}
                    for k, v in info_fields.items()
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)

                if report.get("error"):
                    st.error(f"Processing error: {report['error']}")