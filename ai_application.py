import streamlit as st
import tempfile
from openai import OpenAI

from transformer_oil_extractor_v7 import parse_pdf

# =====================================================
# GROQ
# =====================================================

import os

api_key = os.getenv("GROQ_API_KEY")
client = OpenAI(
    api_key=api_key,
    base_url="https://api.groq.com/openai/v1"
)

# =====================================================
# PAGE
# =====================================================

st.set_page_config(
    page_title="Transformer Condition Sorter",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Transformer Condition Sorter")

st.write(
    "Upload transformer reports and automatically classify them using AI."
)

# =====================================================
# FILE UPLOAD
# =====================================================

pdfs = st.file_uploader(
    "Upload Transformer Reports",
    type=["pdf"],
    accept_multiple_files=True
)

if pdfs:

    good_reports = []
    mild_reports = []
    bad_reports = []

    progress = st.progress(0)

    for i, pdf in enumerate(pdfs):

        try:

            # ==========================================
            # SAVE PDF
            # ==========================================

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=".pdf"
            ) as tmp:

                tmp.write(pdf.read())
                pdf_path = tmp.name

            # ==========================================
            # EXTRACT DATA
            # ==========================================

            data = parse_pdf(pdf_path)

            equipment = (
                data.get("equipment_designation")
                or pdf.name
            )

            recommendation_text = f"""

OVERALL:
{data.get("recommendation","")}

DGA:
{data.get("dga_recommendation","")}

OST:
{data.get("ost_recommendation","")}

"""

            # ==========================================
            # AI CLASSIFICATION
            # ==========================================

            prompt = f"""
Classify this transformer report.

Recommendation Text:

{recommendation_text}

Return ONLY ONE WORD.

GOOD

or

MILD

or

BAD

Definitions:

GOOD:
fit for service,
normal operation,
satisfactory,
acceptable,
no action required

MILD:
monitor,
observe,
retest,
resample,
follow up,
investigate

BAD:
critical,
urgent,
withdraw from service,
shutdown,
severe fault,
major issue
"""

            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0
            )

            result = (
                response
                .choices[0]
                .message
                .content
                .strip()
                .upper()
            )

            report_info = {
                "equipment": equipment,
                "file": pdf.name,
                "recommendation": recommendation_text
            }

            # ==========================================
            # SORT
            # ==========================================

            if "GOOD" in result:

                good_reports.append(
                    report_info
                )

            elif "MILD" in result:

                mild_reports.append(
                    report_info
                )

            else:

                bad_reports.append(
                    report_info
                )

        except Exception as e:

            bad_reports.append(
                {
                    "equipment": pdf.name,
                    "file": pdf.name,
                    "recommendation": str(e)
                }
            )

        progress.progress(
            (i + 1) / len(pdfs)
        )

    progress.empty()

    # =====================================================
    # SUMMARY
    # =====================================================

    st.markdown("---")

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "🟢 GOOD",
        len(good_reports)
    )

    c2.metric(
        "🟠 MILD",
        len(mild_reports)
    )

    c3.metric(
        "🔴 BAD",
        len(bad_reports)
    )

    # =====================================================
    # GOOD
    # =====================================================

    st.markdown("---")
    st.header("🟢 GOOD CONDITION")

    for report in good_reports:

        st.success(
            f"{report['equipment']} | {report['file']}"
        )

    # =====================================================
    # MILD
    # =====================================================

    st.markdown("---")
    st.header("🟠 MILD CONDITION")

    for report in mild_reports:

        st.warning(
            f"{report['equipment']} | {report['file']}"
        )

    # =====================================================
    # BAD
    # =====================================================

    st.markdown("---")
    st.header("🔴 BAD CONDITION")

    for report in bad_reports:

        st.error(
            f"{report['equipment']} | {report['file']}"
        )