import streamlit as st
import tempfile
import json
from openai import OpenAI
from transformer_oil_extractor_v7 import parse_pdf

# =====================================================
# GROQ API KEY — hardcoded here
# =====================================================

import os

api_key = os.getenv("GROQ_API_KEY")
client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# =====================================================
# PAGE CONFIG
# =====================================================

st.set_page_config(
    page_title="Transformer Condition Dashboard",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Transformer Condition Dashboard")
st.write(
    "Upload multiple transformer oil reports — "
    "AI reads gas values AND recommendations, classifies each transformer, "
    "and explains where it disagrees with the vendor."
)

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
    "h2":   ("Hydrogen (H2)",         100,  "ppm"),
    "ch4":  ("Methane (CH4)",          30,  "ppm"),
    "c2h2": ("Acetylene (C2H2)",        3,  "ppm"),
    "c2h4": ("Ethylene (C2H4)",        60,  "ppm"),
    "c2h6": ("Ethane (C2H6)",          20,  "ppm"),
    "co":   ("Carbon Monoxide (CO)",  400,  "ppm"),
    "co2":  ("Carbon Dioxide (CO2)", 3800,  "ppm"),
    "tdcg": ("TDCG",                  720,  "ppm"),
}

# =====================================================
# BUILD PROMPT
# =====================================================

def build_prompt(data: dict) -> str:

    def gas(key):
        val = data.get(key, "ND")
        if val in ("", "ND", None):
            return "ND"
        try:
            return float(val)
        except Exception:
            return val

    gases_lines = []
    for k, (label, limit, unit) in GAS_LIMITS.items():
        gases_lines.append(
            f"  {label}: {gas(k)} {unit}  (limit: {limit} {unit})"
        )
    gases_block = "\n".join(gases_lines)

    vendor_overall = data.get("recommendation", "N/A")
    vendor_dga     = data.get("dga_recommendation", "N/A")
    vendor_ost     = data.get("ost_recommendation", "N/A")

    bdv            = data.get("bdv", "N/A")
    water          = data.get("water", "N/A")
    neutralization = data.get("neutralization", "N/A")
    ddf_90         = data.get("ddf_90", "N/A")
    sp_res_90      = data.get("sp_res_90", "N/A")

    prompt = (
        "You are an expert transformer oil diagnostics engineer.\n\n"
        "=== DISSOLVED GAS ANALYSIS (DGA) VALUES ===\n"
        + gases_block + "\n\n"
        "=== VENDOR RECOMMENDATIONS ===\n"
        f"Overall : {vendor_overall}\n"
        f"DGA     : {vendor_dga}\n"
        f"OST     : {vendor_ost}\n\n"
        "=== OIL QUALITY TESTS ===\n"
        f"BDV (kV)            : {bdv}\n"
        f"Water content mg/kg : {water}\n"
        f"Neutralisation      : {neutralization}\n"
        f"DDF @ 90C           : {ddf_90}\n"
        f"Sp. Resistance 90C  : {sp_res_90}\n\n"
        "Classification rules:\n"
        "GOOD — all gases within limits, no faults, fit for service.\n"
        "MILD — one or more gases slightly elevated, monitor / retest.\n"
        "BAD  — gases significantly above limits, fault detected, action required.\n\n"
        "The dashboard classification MUST be based on the vendor recommendation."
        "Determine vendor_severity from the recommendation text."
        "AI classification should be independent and based on gas values."
        "vendor_severity decides where the transformer is placed in the dashboard."
        "AI classification is only used for comparison and disagreement detection."
        "Never change vendor_severity based on gas values. "
        "intended severity. Then validate using gas values. Your final classification "
        "should reflect the actual situation — agree with the vendor if correct, "
        "disagree if the gas numbers tell a different story.\n\n"
        "Respond ONLY with a valid JSON object. No markdown. No extra text.\n"
        "{\n"
        '  "classification": "GOOD" or "MILD" or "BAD",\n'
        '  "vendor_severity": "GOOD" or "MILD" or "BAD",\n'
        '  "ai_reasoning": "2-3 sentences on YOUR classification based on gas values",\n'
        '  "vendor_recommendation_summary": "one sentence summary of vendor text",\n'
        '  "vendor_agreement": true or false,\n'
        '  "disagreement_reason": "explanation if vendor_agreement is false, else null"\n'
        "}"
    )

    return prompt


# =====================================================
# CALL GROQ
# =====================================================

def classify(data: dict) -> dict:
    prompt = build_prompt(data)
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    raw = response.choices[0].message.content.strip()
    # strip any accidental markdown fences
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
                "Parameter":   label,
                "Value":       f"{val} {unit}",
                "Limit":       f"{limit} {unit}",
                "% of Limit":  pct,
                "Status":      status,
            })
        except Exception:
            rows.append({
                "Parameter":   label,
                "Value":       "ND",
                "Limit":       f"{limit} {unit}",
                "% of Limit":  "—",
                "Status":      "⚪ No Data",
            })
    return rows


# =====================================================
# FILE UPLOAD
# =====================================================

pdfs = st.file_uploader(
    "Upload Transformer Reports (PDF) — select as many as you want",
    type=["pdf"],
    accept_multiple_files=True
)

run_btn = st.button(
    "▶ Analyse Reports",
    type="primary",
    disabled=not pdfs
)

# =====================================================
# ANALYSIS
# =====================================================

if run_btn and pdfs:

    st.session_state.reports = []
    st.session_state.selected = None

    progress = st.progress(0, text="Starting analysis...")

    for i, pdf in enumerate(pdfs):

        progress.progress(
            i / len(pdfs),
            text=f"Analysing {pdf.name}  ({i + 1} / {len(pdfs)})..."
        )

        try:

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(pdf.read())
                pdf_path = tmp.name

            data      = parse_pdf(pdf_path)
            equipment = data.get("equipment_designation") or pdf.name
            ai_result = classify(data)
            st.session_state.reports.append({
                "file": pdf.name,
                "equipment": equipment,
                "data": data,

                "vendor_severity": ai_result.get("vendor_severity", "MILD"),
                "ai_classification": ai_result.get("classification", "MILD"),
                "ai_reasoning": ai_result.get("ai_reasoning", ""),
                    "ai_reasoning":   ai_result.get("ai_reasoning", ""),
                    "vendor_summary": ai_result.get("vendor_recommendation_summary", ""),
                    "vendor_agreement":    ai_result.get("vendor_agreement", True),
                    "disagreement_reason": ai_result.get("disagreement_reason", None),
                    "gas_rows":       gas_status(data),
                    "error":          None,
                })

        except Exception as e:

            st.session_state.reports.append({
                "file":           pdf.name,
                "equipment":      pdf.name,
                "data":           {},
                "vendor_severity": "BAD",
                "ai_classification": "BAD",                
                "ai_reasoning":   f"Error during processing: {e}",
                "vendor_summary": "",
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

    reports  = st.session_state.reports
    good = [r for r in reports if r["vendor_severity"] == "GOOD"]
    mild = [r for r in reports if r["vendor_severity"] == "MILD"]
    bad = [r for r in reports if r["vendor_severity"] == "BAD"]
    disagree = [r for r in reports if not r["vendor_agreement"]]

    # ── summary metrics ──────────────────────────────────────────────────────

    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🟢 Good",  len(good))
    m2.metric("🟠 Mild",  len(mild))
    m3.metric("🔴 Bad",   len(bad))
    m4.metric("⚠️ AI vs Vendor Disagreements", len(disagree))

    # ── three-column card layout ─────────────────────────────────────────────

    st.markdown("---")
    col_good, col_mild, col_bad = st.columns(3)

    def render_card(col, report):
        badge = "" if report["vendor_agreement"] else "  ⚠️ AI disagrees"
        label = f"{report['equipment']}{badge}"
        with col:
            if st.button(label, key=f"card_{report['file']}", use_container_width=True):
                st.session_state.selected = report["file"]

    with col_good:
        st.markdown("### 🟢 Good Condition")
        if not good:
            st.caption("None")
        for r in good:
            render_card(col_good, r)

    with col_mild:
        st.markdown("### 🟠 Mild Condition")
        if not mild:
            st.caption("None")
        for r in mild:
            render_card(col_mild, r)

    with col_bad:
        st.markdown("### 🔴 Bad Condition")
        if not bad:
            st.caption("None")
        for r in bad:
            render_card(col_bad, r)

    # ── detail panel ─────────────────────────────────────────────────────────

    selected_file = st.session_state.selected
    if selected_file:

        report = next((r for r in reports if r["file"] == selected_file), None)

        if report:

            st.markdown("---")

            clf = report["vendor_severity"]
            icon_map  = {"GOOD": "🟢", "MILD": "🟠", "BAD": "🔴"}

            st.header(f"{icon_map.get(clf, '⚪')} {report['equipment']}")
            st.caption(f"File: {report['file']}")

            tab1, tab2, tab3, tab4 = st.tabs([
                "🤖 AI Analysis",
                "💨 Gas Values",
                "🛢️ Oil Quality",
                "📋 Raw Data",
            ])

            # ── Tab 1 : AI analysis & vendor comparison ───────────────────────

            with tab1:

                col_ai, col_vendor = st.columns(2)

                with col_ai:
                    st.subheader("Vendor Classification")

                    vendor_clf = report["vendor_severity"]
                    ai_clf = report["ai_classification"]

                    if vendor_clf == "GOOD":
                        st.success(f"Vendor Severity: {vendor_clf}")
                    elif vendor_clf == "MILD":
                        st.warning(f"Vendor Severity: {vendor_clf}")
                    else:
                        st.error(f"Vendor Severity: {vendor_clf}")

                    st.markdown(f"**AI Assessment:** {ai_clf}")

                    st.markdown("**AI Analysis:**")
                    st.write(report["ai_reasoning"])
                with col_vendor:
                    st.subheader("Vendor Recommendation")
                    vendor_clf = report.get("vendor_severity", "")
                    if vendor_clf == "GOOD":
                        st.success(f"Vendor severity: **{vendor_clf}**")
                    elif vendor_clf == "MILD":
                        st.warning(f"Vendor severity: **{vendor_clf}**")
                    elif vendor_clf == "BAD":
                        st.error(f"Vendor severity: **{vendor_clf}**")
                    d = report["data"]
                    st.markdown(f"**Overall:** {d.get('recommendation', 'N/A')}")
                    st.markdown(f"**DGA:**     {d.get('dga_recommendation', 'N/A')}")
                    st.markdown(f"**OST:**     {d.get('ost_recommendation', 'N/A')}")
                    if report["vendor_summary"]:
                        st.caption(f"AI summary of vendor text: {report['vendor_summary']}")

                st.markdown("---")

                if report["vendor_agreement"]:
                    st.success("✅ AI agrees with the vendor recommendation.")
                else:
                    st.warning("⚠️ AI disagrees with the vendor recommendation.")
                    st.markdown("**Reason for disagreement:**")
                    st.write(report["disagreement_reason"])

            # ── Tab 2 : Gas values ───────────────────────────────────────────

            with tab2:
                st.subheader("Dissolved Gas Analysis")
                if report["gas_rows"]:
                    st.dataframe(
                        report["gas_rows"],
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.caption("No gas data could be extracted from this report.")

            # ── Tab 3 : Oil quality ──────────────────────────────────────────

            with tab3:
                st.subheader("Oil Quality Tests (OST)")
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
                st.dataframe(
                    [{"Parameter": k, "Value": v} for k, v in ost_fields.items()],
                    use_container_width=True,
                    hide_index=True
                )

            # ── Tab 4 : Raw extracted fields ─────────────────────────────────

            with tab4:
                st.subheader("All Extracted Fields")
                d = report["data"]
                st.markdown(f"**PDF format detected:** `{d.get('fmt', 'unknown')}`")

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
                st.dataframe(
                    [{"Field": k, "Value": v or "—"} for k, v in info_fields.items()],
                    use_container_width=True,
                    hide_index=True
                )

                if report.get("error"):
                    st.error(f"Processing error: {report['error']}")