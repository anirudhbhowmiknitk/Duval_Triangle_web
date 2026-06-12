import streamlit as st
import tempfile
import json
import google.generativeai as genai

from transformer_oil_extractor_v7 import parse_pdf

# ==================================================
# GEMINI
# ==================================================

import os

api_key = os.getenv("GROQ_API_KEY")

genai.configure(api_key=API_KEY)

model = genai.GenerativeModel(
    "gemini-2.5-flash"
)

# ==================================================
# PAGE
# ==================================================

st.set_page_config(
    page_title="AI Transformer Analyzer",
    page_icon="🤖",
    layout="wide"
)

st.title("🤖 AI Transformer Fault Analyzer")

st.write(
    "Upload a transformer oil report PDF and let AI perform engineering analysis."
)

# ==================================================
# UPLOAD
# ==================================================

pdf_file = st.file_uploader(
    "Upload PDF",
    type=["pdf"]
)

if pdf_file:

    with st.spinner("Extracting Report..."):

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".pdf"
        ) as tmp:

            tmp.write(pdf_file.read())
            pdf_path = tmp.name

        data = parse_pdf(pdf_path)

    st.success("Data Extracted")

    # ============================================
    # AI PROMPT
    # ============================================

    prompt = f"""
You are a senior transformer diagnostic engineer
with 30 years of experience.

Analyze this transformer oil report.

Report Data:

{json.dumps(data, indent=2)}

Use:
1. DGA gases
2. OST values
3. Recommendations
4. Industry transformer engineering practices

Determine:

- Fault Type
- Severity
- Root Cause
- Maintenance Action
- Risk Level
- Confidence

Severity must be:

NORMAL
MILD
WARNING
DANGER
CRITICAL

Return ONLY valid JSON.

Format:

{{
"severity":"",
"fault_type":"",
"root_cause":"",
"risk_level":"",
"confidence":"",
"maintenance_action":"",
"explanation":""
}}
"""

    # ============================================
    # AI ANALYSIS
    # ============================================

    with st.spinner("AI Engineering Analysis..."):

        response = model.generate_content(
            prompt
        )

    # ============================================
    # PARSE RESULT
    # ============================================

    try:

        text = response.text

        text = text.replace("```json", "")
        text = text.replace("```", "")
        text = text.strip()

        result = json.loads(text)

        severity = result.get(
            "severity",
            "UNKNOWN"
        )

        if severity == "CRITICAL":
            st.error(f"🚨 {severity}")

        elif severity == "DANGER":
            st.error(f"🔴 {severity}")

        elif severity == "WARNING":
            st.warning(f"🟠 {severity}")

        elif severity == "MILD":
            st.warning(f"🟡 {severity}")

        else:
            st.success(f"🟢 {severity}")

        c1, c2 = st.columns(2)

        with c1:

            st.metric(
                "Fault Type",
                result.get("fault_type", "N/A")
            )

            st.metric(
                "Risk Level",
                result.get("risk_level", "N/A")
            )

        with c2:

            st.metric(
                "Confidence",
                result.get("confidence", "N/A")
            )

        st.markdown("---")

        st.subheader("Root Cause")

        st.write(
            result.get(
                "root_cause",
                ""
            )
        )

        st.subheader(
            "Maintenance Action"
        )

        st.write(
            result.get(
                "maintenance_action",
                ""
            )
        )

        st.subheader(
            "Engineering Explanation"
        )

        st.write(
            result.get(
                "explanation",
                ""
            )
        )

    except Exception as e:

        st.error(
            "AI response parsing failed"
        )

        st.code(
            response.text
        )

    # ============================================
    # RAW DATA
    # ============================================

    with st.expander(
        "Extracted Report Data"
    ):
        st.json(data)