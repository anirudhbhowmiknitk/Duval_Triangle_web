"""
water_content_app.py — Transformer Oil Water Content Analyser
=============================================================
Run:  streamlit run water_content_app.py
"""

import os, re, tempfile
import pdfplumber
import streamlit as st
import streamlit.components.v1 as components
from groq import Groq

# ─── Extraction ───────────────────────────────────────────────────────────────

_PRIMARY = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer"   
    r".*?"                                       
    r"(?:IS\s+\d+|ASTM\s+D\s*\d+|IEC\s+\d+)"  
    r"\s+"
    r"(\d+(?:\.\d+)?)",                          
    re.IGNORECASE,
)

_FULL = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer"
    r".*?(?:IS\s+\d+|ASTM\s+D\s*\d+|IEC\s+\d+)\s+"
    r"(\d+(?:\.\d+)?)"            
    r".*?"
    r"(\d+(?:\.\d+)?)\s*Max"      
    r"\s+"
    r"(Acceptable|Not Acceptable|Not Specified|NS)",  
    re.IGNORECASE,
)

_LABEL_SPLIT = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer\s*\n+\s*Method"
    r".*?(\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)

_NO_STD_FULL = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer(?:\s+Method)?"
    r"\s+mg/KG\s*\(ppm\)\s+"
    r"(\d+(?:\.\d+)?)"           
    r"\s+"
    r"(\d+(?:\.\d+)?)\s*Max"     
    r"\s+"
    r"(Acceptable|Not Acceptable|Not Specified|NS)",  
    re.IGNORECASE | re.DOTALL,
)

_EQUIP      = re.compile(r"Equipment\s+Designation\s+(.+?)(?:\n|Owner)", re.IGNORECASE)
_VOLTAGE    = re.compile(r"Voltage\s+Class\s+(\d+(?:\.\d+)?)\s*KV", re.IGNORECASE)
_RATING     = re.compile(r"\bRating\s+(\d[\d,\.]*\s*KVA)", re.IGNORECASE)
_REPORT     = re.compile(r"Oil\s+Test\s+Report\s*[-–]\s*([\w/]+)", re.IGNORECASE)
_DATE       = re.compile(r"Report\s+Date\s+(\d{2}-\d{2}-\d{4})", re.IGNORECASE)
_POINT      = re.compile(r"Sampling\s+Point\s+(.+?)(?:\n)", re.IGNORECASE)
_FLUID      = re.compile(r"Insulating\s+Fluid\s+(.+?)(?:\n)", re.IGNORECASE)
_LIMIT_STD  = re.compile(r"(IEEE\s+C57\.\d+|IS\s+1866|IEC\s+60422)", re.IGNORECASE)

def _gas_pattern(label):
    return re.compile(
        label + r".*?(?:IS\s+\d+|ASTM\s+D\s*\d+|IEC\s+\d+|ppm\s+v/v).*?"
        r"(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max",
        re.IGNORECASE | re.DOTALL,
    )

_GAS_PATTERNS = {
    "H2":   _gas_pattern(r"Hydrogen\s*\(?H2\)?"),
    "CH4":  _gas_pattern(r"Methane\s*\(?CH4\)?"),
    "C2H2": _gas_pattern(r"Acetylene\s*\(?C2H2\)?"),
    "C2H4": _gas_pattern(r"Ethylene\s*\(?C2H4\)?"),
    "C2H6": _gas_pattern(r"Ethane\s*\(?C2H6\)?"),
    "CO":   _gas_pattern(r"Carbon\s+Monoxide\s*\(?CO\)?(?!\s*2)"),
    "CO2":  _gas_pattern(r"Carbon\s+Dioxide\s*\(?CO2\)?"),
    "O2":   _gas_pattern(r"Oxygen\s*\(?O2\)?"),
    "N2":   _gas_pattern(r"Nitrogen\s*\(?N2\)?"),
}

_GAS_LIMITS = {
    "H2": 100, "CH4": 120, "C2H2": 35, "C2H4": 50,
    "C2H6": 65, "CO": 350, "CO2": 2500, "O2": 19000, "N2": 71000,
}
_GAS_LABELS = {
    "H2": "Hydrogen", "CH4": "Methane", "C2H2": "Acetylene",
    "C2H4": "Ethylene", "C2H6": "Ethane", "CO": "Carbon Monoxide",
    "CO2": "Carbon Dioxide", "O2": "Oxygen", "N2": "Nitrogen",
}

_BDV      = re.compile(r"Break\s*(?:down)?\s*Voltage.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Min", re.IGNORECASE | re.DOTALL)
_IFT      = re.compile(r"Interfacial\s+Tension.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Min", re.IGNORECASE | re.DOTALL)
_DDF      = re.compile(r"Dielectric\s+Dissipation\s+Factor.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_OST      = re.compile(r"Oil\s+Sediment.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_ACIDITY  = re.compile(r"Acidity.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_TDCG     = re.compile(r"Total\s+Dissolved\s+Combustible\s+Gas.*?(\d+(?:\.\d+)?)", re.IGNORECASE | re.DOTALL)
_OWNER    = re.compile(r"Owner\s+(.+?)(?:\n|Equipment)", re.IGNORECASE)
_LOCATION = re.compile(r"(?:Location|Station|Substation|Site)\s*[:\-]?\s*(.+?)(?:\n)", re.IGNORECASE)

def _extract_gases(full_text: str) -> dict:
    gases = {}
    for gas, pat in _GAS_PATTERNS.items():
        m = pat.search(full_text)
        if m:
            try:
                gases[gas] = {"value": float(m.group(1)), "limit": float(m.group(2))}
            except (IndexError, ValueError):
                pass
    return gases

def _extract_extra(full_text: str) -> dict:
    out = {}
    for key, pat in [("bdv", _BDV), ("ift", _IFT), ("ddf", _DDF), ("acidity", _ACIDITY)]:
        m = pat.search(full_text)
        if m:
            try:
                out[key] = {"value": float(m.group(1)), "limit": float(m.group(2))}
            except (IndexError, ValueError):
                pass
    m = _TDCG.search(full_text)
    if m:
        try:
            out["tdcg"] = float(m.group(1))
        except ValueError:
            pass
    m = _OWNER.search(full_text)
    if m:
        out["owner"] = m.group(1).strip()
    m = _LOCATION.search(full_text)
    if m:
        out["location"] = m.group(1).strip()
    return out

def _extract_from_pdf(pdf_path: str) -> dict:
    result = dict(
        ppm=None, limit=None, trufil_verdict="ND",
        equipment="—", voltage_kv=None, rating="—",
        report_no="—", report_date="—", sampling_point="—",
        fluid_type="—", limit_std="—",
        extraction_method="not_found",
    )
    try:
        with pdfplumber.open(pdf_path) as pdf:
            all_pages_text = []
            for page in pdf.pages:
                txt = page.extract_text() or ""
                all_pages_text.append(txt)

                if result["equipment"] == "—":
                    m = _EQUIP.search(txt)
                    if m: result["equipment"] = m.group(1).strip()
                if result["voltage_kv"] is None:
                    m = _VOLTAGE.search(txt)
                    if m: result["voltage_kv"] = float(m.group(1))
                if result["rating"] == "—":
                    m = _RATING.search(txt)
                    if m: result["rating"] = m.group(1).strip()
                if result["report_no"] == "—":
                    m = _REPORT.search(txt)
                    if m: result["report_no"] = m.group(1).strip()
                if result["report_date"] == "—":
                    m = _DATE.search(txt)
                    if m: result["report_date"] = m.group(1)
                if result["sampling_point"] == "—":
                    m = _POINT.search(txt)
                    if m: result["sampling_point"] = m.group(1).strip()
                if result["fluid_type"] == "—":
                    m = _FLUID.search(txt)
                    if m: result["fluid_type"] = m.group(1).strip()
                if result["limit_std"] == "—":
                    m = _LIMIT_STD.search(txt)
                    if m: result["limit_std"] = m.group(1).strip()

                if result["ppm"] is not None:
                    continue

                m_full = _FULL.search(txt)
                if m_full:
                    result["ppm"]           = float(m_full.group(1))
                    result["limit"]         = float(m_full.group(2))
                    result["trufil_verdict"] = m_full.group(3).strip().title()
                    result["extraction_method"] = "full_row"
                    continue

                m_prim = _PRIMARY.search(txt)
                if m_prim:
                    result["ppm"] = float(m_prim.group(1))
                    result["extraction_method"] = "primary"
                    continue

            if result["ppm"] is None:
                full_doc = "\n".join(all_pages_text)
                m = _LABEL_SPLIT.search(full_doc)
                if m:
                    result["ppm"] = float(m.group(1))
                    result["extraction_method"] = "split_label"

            if result["ppm"] is None:
                full_doc = "\n".join(all_pages_text)
                m = _NO_STD_FULL.search(full_doc)
                if m:
                    result["ppm"]            = float(m.group(1))
                    result["limit"]          = float(m.group(2))
                    result["trufil_verdict"] = m.group(3).strip().title()
                    result["extraction_method"] = "no_std_full"

            full_doc = "\n".join(all_pages_text)
            result["gases"] = _extract_gases(full_doc)
            result.update(_extract_extra(full_doc))
    except Exception as exc:
        result["extraction_method"] = f"error:{exc}"
    return result

# ─── Classification ───────────────────────────────────────────────────────────

def _is1866_limits(kv):
    kv_label = f"<220 kV, {kv:.0f} kV" if (kv is not None and kv < 220) else "≥220 kV" if (kv is not None) else "unknown kV"
    return 40.0, f"IS 1866:2017 Table-5 ({kv_label})"

def classify(ppm, kv, pdf_limit, fluid_type, limit_std):
    lim_std_upper = (limit_std or "").upper()
    fluid_upper   = (fluid_type or "").upper()
    is_ester = ("ESTER" in fluid_upper or "IEEE" in lim_std_upper)

    if is_ester and pdf_limit:
        reject = float(pdf_limit)
        basis  = f"{limit_std} (Natural Ester Fluid) — PDF limit {reject:.0f} ppm"
    elif pdf_limit and not is_ester:
        reject = float(pdf_limit)
        basis  = f"{limit_std} — PDF limit {reject:.0f} ppm"
    else:
        reject, basis = _is1866_limits(kv)

    if ppm is None:
        return "ND", "Not Detected / Not Reported", "#64748b", reject, reject, basis
    if ppm <= reject:
        return "ACCEPTABLE", f"Acceptable — ≤ {reject:.0f} ppm limit", "#10b981", reject, reject, basis
    return "UNACCEPTABLE", f"Unacceptable — exceeds {reject:.0f} ppm limit. Action required.", "#ef4444", reject, reject, basis

# ─── Helper: Analysis Text ───────────────────────────────────────────────────

def _analysis_text(ppm, v_code, kv, caution, reject) -> str:
    kv_str = f"{kv:.0f} kV" if kv else "unknown voltage class"
    if ppm is None:
        return "Water content by Karl Fischer method was <strong>not found</strong> in this PDF."
    if v_code == "ACCEPTABLE":
        return f"At <strong>{ppm:.1f} ppm</strong>, moisture is within acceptable operating limits (≤ {reject:.0f} ppm). No immediate corrective engineering action is needed."
    if v_code == "UNACCEPTABLE":
        return (
            f"At <strong>{ppm:.1f} ppm</strong>, moisture levels exceed the maximum benchmark limit of "
            f"({reject:.0f} ppm) configured for {kv_str}.<br>"
            f"<strong>Recommended Remediations:</strong><br>"
            f"1. Schedule offline vacuum hydration/oil filtration filtration system promptly.<br>"
            f"2. Check structural sealings of gasket pathways and air breathers.<br>"
            f"3. Verify BDV breakdown test profiles before high-load runs."
        )
    return ""

# ─── Groq AI Analysis ────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _groq_analyse(ppm, kv, fluid_type, limit_std, trufil_verdict, equip, sampling_point, groq_key):
    try:
        client = Groq(api_key=groq_key)
        kv_s   = f"{kv:.0f} kV" if kv else "unknown"
        prompt = (
            f"You are a transformer oil condition monitoring expert. Analyse this moisture result:\n\n"
            f"Equipment: {equip}\n"
            f"Sampling Point: {sampling_point}\n"
            f"Voltage Class: {kv_s}\n"
            f"Insulating Fluid: {fluid_type or 'Mineral Oil'}\n"
            f"Water Content (Karl Fischer): {ppm:.1f} ppm\n"
            f"Applicable Standard: {limit_std or 'IS 1866:2017'}\n"
            f"Vendor Verdict (TRU-FIL): {trufil_verdict}\n\n"
            f"Provide a concise expert assessment covering moisture risk, root cause, priority remediation, and monitoring. Keep it precise, 150 words max, single paragraph prose."
        )
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=350,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        return f"AI analysis unavailable: {exc}"

# ─── Page Setup & Custom Modern Premium Styles ────────────────────────────────

st.set_page_config(page_title="Water Content Analyser", page_icon="💧", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #f8fafc; }
.block-container { padding-top: 1.5rem; padding-bottom: 3rem; max-width: 1250px; }

/* Elegant Glassmorphic Dashboard Header */
.hero {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    border-radius: 20px; padding: 2.5rem;
    margin-bottom: 2rem;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
}
.hero h1 { color: #f8fafc; font-size: 2.2rem; font-weight: 800; margin: 0 0 .5rem; letter-spacing: -.03em; }
.hero p  { color: #94a3b8; font-size: 1rem; margin-bottom: 1rem; font-weight: 400; }
.hero-badge { 
    display: inline-block; background: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 30px; padding: .3rem .9rem; font-size: .75rem; font-weight: 600;
    color: #cbd5e1; letter-spacing: .02em; margin-right: .5rem; margin-top: .4rem; 
}

/* Premium KPI Grid Layout */
.metric-row { display: flex; gap: 16px; margin-bottom: 2rem; flex-wrap: wrap; }
.metric-card {
    flex: 1 1 200px; background: #ffffff; border-radius: 16px; padding: 1.5rem;
    text-align: left; position: relative; border: 1px solid #e2e8f0;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.02), 0 2px 4px -1px rgba(0, 0, 0, 0.01);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.metric-card:hover { transform: translateY(-2px); box-shadow: 0 12px 20px -3px rgba(0,0,0,0.05); }
.metric-card .mc-num { font-size: 2.6rem; font-weight: 800; line-height: 1; letter-spacing: -.04em; margin-bottom: 4px; }
.metric-card .mc-lbl { font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #64748b; }
.mc-total .mc-num { color: #1e293b; }
.mc-bad .mc-num   { color: #ef4444; }
.mc-good .mc-num  { color: #10b981; }

/* Status Banners */
.tier-header {
    border-radius: 14px; padding: 1rem 1.5rem;
    margin: 1.8rem 0 1rem; font-size: 1.1rem; font-weight: 700;
    display: flex; align-items: center; gap: .6rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.03);
}
.tier-bad  { background: #fef2f2; color: #991b1b; border: 1px solid #fca5a5; }
.tier-good { background: #ecfdf5; color: #065f46; border: 1px solid #6ee7b7; }
.tier-count { background: rgba(0, 0, 0, 0.06); border-radius: 20px; padding: .1rem .7rem; font-size: .85rem; margin-left: auto; }

/* Custom Accordion Sub-Elements */
.verdict-strip {
    border-radius: 12px; padding: 1.2rem; margin-bottom: 1.2rem;
    font-weight: 600; display: flex; align-items: center; justify-content: space-between;
    background: #f8fafc; border: 1px solid #e2e8f0;
}
.vs-value { font-size: 1.75rem; font-weight: 800; color: #0f172a; letter-spacing: -.03em; }
.vs-meta { font-size: .85rem; color: #64748b; margin-top: .2rem; font-weight: 400; }

/* Engineering Visual Progress Bar */
.wbar-wrap { margin: 1rem 0 1.5rem; }
.wbar-track { background: #e2e8f0; border-radius: 8px; height: 10px; width: 100%; overflow: hidden; }
.wbar-fill { height: 100%; border-radius: 8px; transition: width .6s ease; }
.wbar-label { display: flex; justify-content: space-between; margin-top: .5rem; font-size: .8rem; color: #64748b; font-weight: 500; }

/* Text Blocks */
.analysis-box { border-radius: 12px; padding: 1.2rem; margin-bottom: 1.5rem; font-size: .9rem; line-height: 1.6; }
.ab-bad  { background: #fff5f5; border-left: 4px solid #ef4444; color: #7f1d1d; }
.ab-good { background: #f6fdf9; border-left: 4px solid #10b981; color: #064e3b; }

/* Section Separators */
.slabel {
    font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em;
    color: #475569; margin: 1.5rem 0 .75rem; padding-bottom: .4rem;
    border-bottom: 1px solid #e2e8f0; display: flex; align-items: center; gap: .5rem;
}

/* Chemical & Gas Cards Components Grid */
.gas-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.gas-card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 1rem; }
.gas-name { font-size: .75rem; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: .25rem; }
.gas-formula { color: #94a3b8; font-size: .7rem; font-weight: 500; }
.gas-value-row { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: .5rem; }
.gas-val  { font-size: 1.5rem; font-weight: 800; color: #1e293b; letter-spacing: -.02em; }
.gas-limit { font-size: .75rem; color: #94a3b8; }
.gas-bar-track { background: #f1f5f9; border-radius: 4px; height: 6px; overflow: hidden; }
.gas-bar-fill  { height: 100%; border-radius: 4px; }

/* Grid Parameter Structures */
.extra-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.ecell { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 1rem; }
.ecell-lbl { font-size: .7rem; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: .25rem; }
.ecell-val { font-size: 1.25rem; font-weight: 700; color: #1e293b; }
.ecell-sub { font-size: .75rem; color: #94a3b8; margin-top: .25rem; }

/* Dense Descriptive Parameters Fields */
.meta-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.mcell { background: #ffffff; border: 1px solid #f1f5f9; border-radius: 10px; padding: .75rem 1rem; }
.mcell-lbl { font-size: .65rem; text-transform: uppercase; letter-spacing: .04em; color: #94a3b8; font-weight: 700; margin-bottom: 2px; }
.mcell-val { font-size: .85rem; font-weight: 600; color: #334155; word-break: break-all; }

/* Secondary Pills Layout */
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.info-pill { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 1rem; }
.ip-lbl { font-size: .7rem; font-weight: 700; text-transform: uppercase; color: #64748b; margin-bottom: .25rem; }
.ip-val { font-size: 1rem; font-weight: 700; color: #0f172a; }
.ip-sub { font-size: .8rem; color: #64748b; margin-top: .15rem; }

/* Llama Container Styles Wrapper */
.ai-panel { background: #fafafa; border: 1px solid #e4e4e7; border-left: 4px solid #3b82f6; border-radius: 14px; padding: 1.5rem; margin-top: 1.5rem; }
.ai-header { font-size: .75rem; font-weight: 700; text-transform: uppercase; color: #2563eb; letter-spacing: .05em; margin-bottom: .75rem; display: flex; align-items: center; gap: .5rem; }
.ai-body { font-size: .9rem; line-height: 1.6; color: #3f3f46; }

/* Legend Guidelines Footer Panels */
.ref-box { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 1.5rem; margin-top: 2.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
.ref-title { font-size: .85rem; font-weight: 700; color: #1e293b; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 1rem; }

/* Clean Layout Streamlit Framework Corrections */
[data-testid="stFileUploader"] { border-radius: 14px !important; box-shadow: none !important; }
.stExpander { background-color: #ffffff !important; border-radius: 14px !important; border: 1px solid #e2e8f0 !important; box-shadow: 0 1px 3px rgba(0,0,0,0.01) !important; margin-bottom: 0.5rem !important; }
#MainMenu, footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ─── Sidebar — Groq API key ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🤖 AI Diagnostics Configuration")
    st.markdown("<div style='font-size:.8rem; color:#64748b; margin-bottom:1rem;'>Provide a Groq cloud API access key to request an algorithmic runtime diagnosis evaluation utilizing Llama 3.3.</div>", unsafe_allow_html=True)
    groq_key_input = st.text_input("Groq API Key", type="password", placeholder="gsk_...", label_visibility="collapsed")
    if groq_key_input:
        st.success("AI Infrastructure Online")

# ─── Dashboard Hero Banner ───────────────────────────────────────────────────
st.markdown("""
<div class="hero">
  <h1>Transformer Oil — Water Content Analyser</h1>
  <p>Automated Karl Fischer Extraction & Diagnostic Analytics Pipeline (IS 1866 : 2017 / IEC 60422)</p>
  <div>
    <span class="hero-badge">IS 13567</span>
    <span class="hero-badge">ASTM D1533</span>
    <span class="hero-badge">IEC 60814</span>
    <span class="hero-badge">TRU-FIL · SGS · CPRI Engine</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ─── File Upload Logic ────────────────────────────────────────────────────────
pdfs = st.file_uploader("Select analytical chemistry lab reports (PDF format supported)", type=["pdf"], accept_multiple_files=True)

if not pdfs:
    st.markdown("""
    <div style="border: 2px dashed #cbd5e1; border-radius: 20px; padding: 4rem 2rem; text-align: center; color: #64748b; background: #ffffff; margin-top: 1rem;">
      <div style="font-size: 3rem; margin-bottom: 1rem;">📊</div>
      <div style="font-size: 1.2rem; font-weight: 700; color: #0f172a;">No Active Reports Queued</div>
      <div style="font-size: .875rem; margin-top: .5rem; max-width: 480px; margin-left: auto; margin-right: auto; line-height: 1.5; color: #94a3b8;">
        Upload transformer dielectric fluid inspection logs. Parsing structures extract Water Data metrics, multi-gases DGA profiles, and structural physical tests.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ─── Processing Array Pipeline ───────────────────────────────────────────────
if "wc_cache" not in st.session_state:
    st.session_state.wc_cache = {}

cache = st.session_state.wc_cache
bar   = st.progress(0, text="Initializing Parser Matrix...")

for i, f in enumerate(pdfs):
    key = f"{f.name}:{f.size}"
    if key not in cache:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(f.read())
            tp = tmp.name
        try:
            info = _extract_from_pdf(tp)
        except Exception as exc:
            info = dict(
                ppm=None, limit=None, trufil_verdict="ERR", equipment="—", voltage_kv=None,
                rating="—", report_no="—", report_date="—", sampling_point="—", fluid_type="—",
                limit_std="—", gases={}, tdcg=None, owner="—", location="—", extraction_method=f"error:{exc}"
            )
        os.unlink(tp)
        ppm = info["ppm"]
        kv  = info["voltage_kv"]
        v_code, v_label, colour, caution, reject, basis = classify(ppm, kv, info["limit"], info.get("fluid_type","—"), info.get("limit_std","—"))
        cache[key] = dict(name=f.name, **info, verdict=v_code, verdict_label=v_label, colour=colour, caution=caution, reject=reject, basis=basis)
    bar.progress((i + 1) / len(pdfs), text=f"Processing file matrix {i+1} of {len(pdfs)}: {f.name}")

bar.empty()

# ─── Data Arrangement Elements ────────────────────────────────────────────────
_ORDER = {"UNACCEPTABLE": 0, "ACCEPTABLE": 1, "ND": 2}
rlist  = sorted(cache.values(), key=lambda r: (_ORDER.get(r["verdict"], 9), r["name"].lower()))

n_total = len(rlist)
n_ok    = sum(1 for r in rlist if r["verdict"] == "ACCEPTABLE")
n_bad   = sum(1 for r in rlist if r["verdict"] == "UNACCEPTABLE")
n_nd    = sum(1 for r in rlist if r["verdict"] == "ND")

st.markdown(f"""
<div class="metric-row">
  <div class="metric-card mc-total"><div class="mc-num">{n_total}</div><div class="mc-lbl">Reports Extracted</div></div>
  <div class="metric-card mc-bad"><div class="mc-num">{n_bad}</div><div class="mc-lbl">Action Flagged</div></div>
  <div class="metric-card mc-good"><div class="mc-num">{n_ok}</div><div class="mc-lbl">Operational Normal</div></div>
</div>
""", unsafe_allow_html=True)

# ─── ReportLab Dynamic Export Components ─────────────────────────────────────
import io, datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors as rl_colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

def _rl_safe(text):
    if text is None: return "—"
    s = str(text).replace("—", "-").replace("–", "-")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _build_pdf_report(rlist_data):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_s  = ParagraphStyle("T", parent=styles["Title"], fontSize=18, textColor=rl_colors.HexColor("#0f172a"), spaceAfter=4)
    h1_s     = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=12, textColor=rl_colors.HexColor("#1e293b"), spaceBefore=14, spaceAfter=4)
    h2_s     = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=10, textColor=rl_colors.HexColor("#475569"), spaceBefore=8, spaceAfter=3)
    normal_s = ParagraphStyle("N", parent=styles["Normal"], fontSize=9, leading=13)
    small_s  = ParagraphStyle("S", parent=styles["Normal"], fontSize=8, textColor=rl_colors.HexColor("#64748b"), leading=11)

    story = [Paragraph("Transformer Analytics Summary", title_s), Paragraph(f"Generated: {datetime.datetime.now().strftime('%d-%m-%Y %H:%M')}", small_s), HRFlowable(width="100%", thickness=1.5, color=rl_colors.HexColor("#0f172a"), spaceAfter=12)]
    
    for r in rlist_data:
        ppm = r["ppm"]
        v_code = r["verdict"]
        equip = _rl_safe(r["equipment"])
        ppm_s = f"{ppm:.1f} ppm" if ppm is not None else "N/D"
        scolor = rl_colors.HexColor("#ef4444") if v_code == "UNACCEPTABLE" else rl_colors.HexColor("#10b981")
        
        story.append(Paragraph(f"<b>Asset Ref: {equip}</b>", h1_s))
        w_data = [
            ["Parameter Context", "Extracted Value", "Regulatory Evaluation Baseline"],
            ["Water Content (KF)", ppm_s, _rl_safe(r.get("basis", "IS 1866 Baseline"))],
            ["Assigned Operational Limits", f"{r['reject']:.0f} ppm max", "IS 1866 Class Rule"],
            ["Transformer Voltage Class", f"{r['voltage_kv'] or '—'} kV", "Asset Spec Matrix"]
        ]
        w_tbl = Table(w_data, colWidths=[5*cm, 4*cm, 8*cm])
        w_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0),(-1,0), rl_colors.HexColor("#1e293b")),
            ("TEXTCOLOR", (0,0),(-1,0), rl_colors.white),
            ("GRID", (0,0),(-1,-1), 0.5, rl_colors.HexColor("#e2e8f0")),
            ("FONTNAME", (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0),(-1,-1), 9),
            ("TOPPADDING", (0,0),(-1,-1), 6),
            ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ]))
        story.append(w_tbl)
        story.append(Spacer(1, 10))
    doc.build(story)
    buf.seek(0)
    return buf.read()

col_dl, _ = st.columns([1, 3])
with col_dl:
    try:
        st.download_button(label="📥 Export Dynamic PDF Executive Summary", data=_build_pdf_report(rlist), file_name="Fluid_Analysis_Report.pdf", mime="application/pdf", use_container_width=True)
    except Exception as e:
        st.caption(f"PDF Generator Deferred: {e}")

# ─── Component Rendering Utilities ───────────────────────────────────────────
def _render_gas_card(gas, data, standard_limit):
    val = data["value"]
    limit = data.get("limit") or standard_limit
    pct = min(val / limit * 100, 100) if limit else 0
    bar_color = "#ef4444" if (limit and val >= limit) else ("#f59e0b" if (limit and val >= limit * 0.7) else "#10b981")
    return f"""
    <div class="gas-card">
      <div class="gas-name">{_GAS_LABELS.get(gas, gas)} <span class="gas-formula">({gas})</span></div>
      <div class="gas-value-row">
        <span class="gas-val">{val:.1f}</span>
        <span class="gas-limit">Limit: {limit or '—'} ppm</span>
      </div>
      <div class="gas-bar-track"><div class="gas-bar-fill" style="width: {pct}%; background: {bar_color};"></div></div>
    </div>
    """

def _render_extra_cell(title, item, units, limit=None, low_is_good=True):
    if item is None: return f'<div class="ecell"><div class="ecell-lbl">{title}</div><div class="ecell-val" style="color:#94a3b8;">—</div></div>'
    val = item["value"] if isinstance(item, dict) else item
    return f"""
    <div class="ecell">
      <div class="ecell-lbl">{title}</div>
      <div class="ecell-val">{val:.2f} <span style="font-size: .75rem; font-weight:400; color:#64748b;">{units}</span></div>
      <div class="ecell-sub">{f"Limit: {limit}" if limit else ""}</div>
    </div>
    """

# ─── Group Accordions Structural View Loop ───────────────────────────────────
TIERS = [("UNACCEPTABLE", "Action Flagged Asset List", "bad"), ("ACCEPTABLE", "Satisfactory Fluid Properties", "good")]

for status_key, label_title, css_slug in TIERS:
    subset = [r for r in rlist if r["verdict"] == status_key]
    if not subset: continue
    
    st.markdown(f'<div class="tier-header tier-{css_slug}"><b>{label_title}</b> <span class="tier-count">{len(subset)} Assets</span></div>', unsafe_allow_html=True)
    
    for asset in subset:
        ppm = asset["ppm"]
        kv = asset["voltage_kv"]
        title_string = f"{'🔴' if status_key=='UNACCEPTABLE' else '🟢'} {asset['equipment']}  Obviously {f'{ppm:.1f} ppm' if ppm else 'No Data'}  {f'{kv:.0f} kV' if kv else 'kV Unknown'}"
        
        with st.expander(title_string, expanded=(status_key == "UNACCEPTABLE")):
            st.markdown(f"""
            <div class="verdict-strip">
              <div>
                <div class="vs-value">{f"{ppm:.1f}" if ppm else "—"} <span style="font-size:1rem; color:#64748b;">ppm H₂O</span></div>
                <div class="vs-meta">Assigned Threshold Rule: {asset['reject']:.0f} ppm · {asset.get('fluid_type', 'Mineral Base')}</div>
              </div>
              <div style="text-align: right;">
                <div style="font-weight: 800; color: {asset['colour']};">{asset['verdict']}</div>
                <div style="font-size: .8srem; color: #64748b;">Lab Log Verdict: {asset['trufil_verdict']}</div>
              </div>
            </div>
            """, unsafe_allow_html=True)
            
            if ppm:
                calc_percentage = min(ppm / asset["reject"] * 100, 100)
                st.markdown(f"""
                <div class="wbar-wrap">
                  <div class="wbar-track"><div class="wbar-fill" style="width: {calc_percentage}%; background: {asset['colour']};"></div></div>
                  <div class="wbar-label"><span>0 ppm</span><span>{calc_percentage:.1f}% of operating threshold limit</span><span>{asset['reject']:.0f} ppm</span></div>
                </div>
                """, unsafe_allow_html=True)
                
            st.markdown(f'<div class="analysis-box ab-{css_slug}">{_analysis_text(ppm, asset["verdict"], kv, asset["caution"], asset["reject"])}</div>', unsafe_allow_html=True)
            
            # ── DGA Integration ──────────────────────────────────────────────
            gases = asset.get("gases", {})
            if gases:
                st.markdown('<div class="slabel">🧪 Dissolved Gas Analysis (DGA Log Profile)</div>', unsafe_allow_html=True)
                cards_html = "".join(_render_gas_card(g, gases[g], _GAS_LIMITS.get(g)) for g in ["H2", "C2H2", "C2H4", "CH4", "C2H6", "CO", "CO2"] if g in gases)
                st.markdown(f'<div class="gas-grid">{cards_html}</div>', unsafe_allow_html=True)
                
            # ── Extra Properties ─────────────────────────────────────────────
            st.markdown('<div class="slabel">⚗️ Physical and Electromechanical Assessments</div>', unsafe_allow_html=True)
            cells_markup = (
                _render_extra_cell("Breakdown Voltage (BDV)", asset.get("bdv"), "kV", low_is_good=False) +
                _render_extra_cell("Interfacial Tension (IFT)", asset.get("ift"), "mN/m", low_is_good=False) +
                _render_extra_cell("Dissipation Factor (DDF)", asset.get("ddf"), "%") +
                _render_extra_cell("Fluid Neutralization Acidity", asset.get("acidity"), "mg KOH/g")
            )
            st.markdown(f'<div class="extra-grid">{cells_markup}</div>', unsafe_allow_html=True)
            
            # ── Meta Field Log Grid ──────────────────────────────────────────
            st.markdown('<div class="slabel">📋 Traceability & Asset Demographics</div>', unsafe_allow_html=True)
            st.markdown(f"""
            <div class="meta-grid">
              <div class="mcell"><div class="mcell-lbl">Report ID</div><div class="mcell-val">{asset['report_no']}</div></div>
              <div class="mcell"><div class="mcell-lbl">Execution Date</div><div class="mcell-val">{asset['report_date']}</div></div>
              <div class="mcell"><div class="mcell-lbl">MVA/kVA Rating</div><div class="mcell-val">{asset['rating']}</div></div>
              <div class="mcell"><div class="mcell-lbl">Sampling Location Context</div><div class="mcell-val">{asset['sampling_point']}</div></div>
              <div class="mcell"><div class="mcell-lbl">Regulatory Logic Rule</div><div class="mcell-val">{asset['basis']}</div></div>
              <div class="mcell"><div class="mcell-lbl">Parser Path Execution</div><div class="mcell-val">{asset['extraction_method']}</div></div>
            </div>
            """, unsafe_allow_html=True)

            # ── Groq LLM Framework Node ──────────────────────────────────────
            if groq_key_input and ppm is not None:
                st.markdown('<div class="ai-panel"><div class="ai-header">🤖 Core Inference Analytics Engine — Llama Diagnostics</div>', unsafe_allow_html=True)
                ai_text_response = _groq_analyse(ppm, kv, asset.get("fluid_type"), asset.get("limit_std"), asset["trufil_verdict"], asset["equipment"], asset["sampling_point"], groq_key_input)
                st.markdown(f'<div class="ai-body">{ai_text_response}</div></div>', unsafe_allow_html=True)

# ─── Context Baseline Legend Reference Card ─────────────────────────────────
st.markdown("""
<div class="ref-box">
  <div class="ref-title">📐 Standards Engineering Evaluation Matrix — IS 1866 / IEC 60422</div>
  <div style="font-size: .85rem; color: #475569; line-height: 1.5;">
    Dielectric stability baseline ranges are classified as acceptable (Normal Routine Monitoring) below 40 ppm. 
    Moisture concentrations above 40 ppm lower the overall Breakdown Voltage parameters, mandating systematic dehydration treatment protocols.
  </div>
</div>
""", unsafe_allow_html=True)