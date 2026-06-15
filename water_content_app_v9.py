"""
water_content_app.py — Transformer Oil Water Content Analyser
=============================================================
Run:  streamlit run water_content_app.py

Extracts "Water Content By Karl Fischer Method" from TRU-FIL format PDFs.
Displays value, TRU-FIL's own verdict, and independent IS 1866 / IEC 60422 analysis.

Extraction strategy (matched to actual TRU-FIL PDF text structure):
  Line format in PDF:
    "Water Content By Karl Fischer  mg/KG (ppm)  IS 13567  <VALUE>  <LIMIT> Max  <VERDICT>"
  The label may wrap: first line ends at "Karl Fischer", second line is "Method".
  pdfplumber collapses both into one line in extract_text().
  Pattern: find label → skip IS standard number → first number = test result.
"""

import os, re, tempfile
import pdfplumber
import streamlit as st
import streamlit.components.v1 as components
from groq import Groq

# ─── Extraction ───────────────────────────────────────────────────────────────

# Primary: TRU-FIL line contains the full row in one line after pdfplumber extraction
# "Water Content By Karl Fischer  mg/KG (ppm)  IS 13567  12  40 Max  Acceptable"
_PRIMARY = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer"   # exact TRU-FIL label
    r".*?"                                       # unit field (mg/KG etc.)
    r"(?:IS\s+\d+|ASTM\s+D\s*\d+|IEC\s+\d+)"  # any standard: IS 13567, ASTM D1533, IEC 60814
    r"\s+"
    r"(\d+(?:\.\d+)?)",                          # TEST RESULT — first number after standard
    re.IGNORECASE,
)

# Full line pattern — also captures limit and TRU-FIL's own verdict
_FULL = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer"
    r".*?(?:IS\s+\d+|ASTM\s+D\s*\d+|IEC\s+\d+)\s+"
    r"(\d+(?:\.\d+)?)"            # [1] test result
    r".*?"
    r"(\d+(?:\.\d+)?)\s*Max"      # [2] limit
    r"\s+"
    r"(Acceptable|Not Acceptable|Not Specified|NS)",  # [3] TRU-FIL verdict
    re.IGNORECASE,
)

# Fallback: "Water Content By Karl Fischer Method" split across lines — join and retry
_LABEL_SPLIT = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer\s*\n+\s*Method"
    r".*?(\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)

# Fallback for OLTC-style PDFs where the test-method column is blank (no IS/ASTM/IEC number).
# Only fires when _FULL and _PRIMARY both fail (i.e. no standard code present in the row).
# Matches:  "Water Content By Karl Fischer [Method]  mg/KG (ppm)  <result>  <limit> Max  <verdict>"
_NO_STD_FULL = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer(?:\s+Method)?"
    r"\s+mg/KG\s*\(ppm\)\s+"
    r"(\d+(?:\.\d+)?)"           # [1] test result
    r"\s+"
    r"(\d+(?:\.\d+)?)\s*Max"     # [2] limit
    r"\s+"
    r"(Acceptable|Not Acceptable|Not Specified|NS)",  # [3] verdict
    re.IGNORECASE | re.DOTALL,
)

# Equipment / metadata patterns
_EQUIP      = re.compile(r"Equipment\s+Designation\s+(.+?)(?:\n|Owner)", re.IGNORECASE)
_VOLTAGE    = re.compile(r"Voltage\s+Class\s+(\d+(?:\.\d+)?)\s*KV", re.IGNORECASE)
_RATING     = re.compile(r"\bRating\s+(\d[\d,\.]*\s*KVA)", re.IGNORECASE)
_REPORT     = re.compile(r"Oil\s+Test\s+Report\s*[-–]\s*([\w/]+)", re.IGNORECASE)
_DATE       = re.compile(r"Report\s+Date\s+(\d{2}-\d{2}-\d{4})", re.IGNORECASE)
_POINT      = re.compile(r"Sampling\s+Point\s+(.+?)(?:\n)", re.IGNORECASE)
_FLUID      = re.compile(r"Insulating\s+Fluid\s+(.+?)(?:\n)", re.IGNORECASE)
_LIMIT_STD  = re.compile(r"(IEEE\s+C57\.\d+|IS\s+1866|IEC\s+60422)", re.IGNORECASE)

# ─── DGA gas extraction patterns ─────────────────────────────────────────────
# Matches TRU-FIL row: "Hydrogen (H2)  ppm v/v  IS 9434  <value>  <limit> Max  <verdict>"
# Also handles SGS/CPRI formats where gas name is followed directly by value.
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

# IEEE C57.104 / IS 9434 typical action limits (ppm v/v) for display reference
_GAS_LIMITS = {
    "H2": 100, "CH4": 120, "C2H2": 35, "C2H4": 50,
    "C2H6": 65, "CO": 350, "CO2": 2500, "O2": 19000, "N2": 71000,
}
_GAS_LABELS = {
    "H2": "Hydrogen", "CH4": "Methane", "C2H2": "Acetylene",
    "C2H4": "Ethylene", "C2H6": "Ethane", "CO": "Carbon Monoxide",
    "CO2": "Carbon Dioxide", "O2": "Oxygen", "N2": "Nitrogen",
}

# Additional physical/chemical tests
_BDV      = re.compile(r"Break\s*(?:down)?\s*Voltage.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Min", re.IGNORECASE | re.DOTALL)
_IFT      = re.compile(r"Interfacial\s+Tension.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Min", re.IGNORECASE | re.DOTALL)
_DDF      = re.compile(r"Dielectric\s+Dissipation\s+Factor.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_OST      = re.compile(r"Oil\s+Sediment.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_ACIDITY  = re.compile(r"Acidity.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_TDCG     = re.compile(r"Total\s+Dissolved\s+Combustible\s+Gas.*?(\d+(?:\.\d+)?)", re.IGNORECASE | re.DOTALL)
_OWNER    = re.compile(r"Owner\s+(.+?)(?:\n|Equipment)", re.IGNORECASE)
_LOCATION = re.compile(r"(?:Location|Station|Substation|Site)\s*[:\-]?\s*(.+?)(?:\n)", re.IGNORECASE)


def _extract_gases(full_text: str) -> dict:
    """Extract DGA gas values and limits from full PDF text."""
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
    """Extract BDV, IFT, DDF, acidity, TDCG, owner, location from full text."""
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
    """
    Returns dict with keys:
      ppm          float | None
      limit        float | None   (TRU-FIL stated limit from PDF)
      trufil_verdict str          (Acceptable / Not Acceptable / ND)
      equipment    str
      voltage_kv   float | None
      rating       str
      report_no    str
      report_date  str
      sampling_point str
      extraction_method str      (how we found the value)
    """
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

                # ── Metadata (page 1 mostly) ──────────────────────────────────
                if result["equipment"] == "—":
                    m = _EQUIP.search(txt)
                    if m:
                        result["equipment"] = m.group(1).strip()

                if result["voltage_kv"] is None:
                    m = _VOLTAGE.search(txt)
                    if m:
                        result["voltage_kv"] = float(m.group(1))

                if result["rating"] == "—":
                    m = _RATING.search(txt)
                    if m:
                        result["rating"] = m.group(1).strip()

                if result["report_no"] == "—":
                    m = _REPORT.search(txt)
                    if m:
                        result["report_no"] = m.group(1).strip()

                if result["report_date"] == "—":
                    m = _DATE.search(txt)
                    if m:
                        result["report_date"] = m.group(1)

                if result["sampling_point"] == "—":
                    m = _POINT.search(txt)
                    if m:
                        result["sampling_point"] = m.group(1).strip()

                if result["fluid_type"] == "—":
                    m = _FLUID.search(txt)
                    if m:
                        result["fluid_type"] = m.group(1).strip()

                if result["limit_std"] == "—":
                    m = _LIMIT_STD.search(txt)
                    if m:
                        result["limit_std"] = m.group(1).strip()

                # ── Water content (already found — skip) ─────────────────────
                if result["ppm"] is not None:
                    continue

                # Strategy 1: full row match (primary — works for TRU-FIL)
                m_full = _FULL.search(txt)
                if m_full:
                    result["ppm"]           = float(m_full.group(1))
                    result["limit"]         = float(m_full.group(2))
                    result["trufil_verdict"] = m_full.group(3).strip().title()
                    result["extraction_method"] = "full_row"
                    continue

                # Strategy 2: primary (value only, no verdict)
                m_prim = _PRIMARY.search(txt)
                if m_prim:
                    result["ppm"] = float(m_prim.group(1))
                    result["extraction_method"] = "primary"
                    continue

            # Strategy 3: split-label fallback on full document text
            if result["ppm"] is None:
                full_doc = "\n".join(all_pages_text)
                m = _LABEL_SPLIT.search(full_doc)
                if m:
                    result["ppm"] = float(m.group(1))
                    result["extraction_method"] = "split_label"

            # Strategy 4: OLTC-style rows — no IS/ASTM/IEC standard in the row.
            # Only runs when all three strategies above have failed.
            if result["ppm"] is None:
                full_doc = "\n".join(all_pages_text)
                m = _NO_STD_FULL.search(full_doc)
                if m:
                    result["ppm"]            = float(m.group(1))
                    result["limit"]          = float(m.group(2))
                    result["trufil_verdict"] = m.group(3).strip().title()
                    result["extraction_method"] = "no_std_full"

            # ── Gas & extra fields ────────────────────────────────────────────
            full_doc = "\n".join(all_pages_text)
            result["gases"] = _extract_gases(full_doc)
            result.update(_extract_extra(full_doc))

    except Exception as exc:
        result["extraction_method"] = f"error:{exc}"

    return result


# ─── Classification ───────────────────────────────────────────────────────────
#
# Priority: use the limit stated in the PDF itself (extracted from the report row).
# Fallback to IS 1866 : 2017 Table-5 when no PDF limit is available.
#
# IS 1866 / IEC 60422 (mineral oil):
#   ≥ 220 kV → reject > 35 ppm   caution > 25 ppm
#   < 220 kV → reject > 40 ppm   caution > 25 ppm
#
# IEEE C57.147 (natural esters / ester fluids):
#   In-service limit: 450 ppm — completely different scale.
#   Caution: > 300 ppm (IEC 62770 guidance)

def _is1866_limits(kv):
    # Two levels only: ACCEPTABLE (≤ 40 ppm) / UNACCEPTABLE (> 40 ppm)
    kv_label = f"<220 kV, {kv:.0f} kV" if (kv is not None and kv < 220) else "≥220 kV" if (kv is not None) else "unknown kV"
    return 40.0, f"IS 1866:2017 Table-5 ({kv_label})"


def classify(ppm, kv, pdf_limit, fluid_type, limit_std):
    """
    Two-level classification: ACCEPTABLE (≤ limit) / UNACCEPTABLE (> limit).
    Returns (verdict_code, verdict_label, hex_colour, reject_thresh, basis_note).
    The caution_thresh return value is kept as an alias of reject for API compatibility.
    """
    lim_std_upper = (limit_std or "").upper()
    fluid_upper   = (fluid_type or "").upper()
    is_ester = ("ESTER" in fluid_upper or "IEEE" in lim_std_upper)

    if is_ester and pdf_limit:
        reject = float(pdf_limit)
        basis  = f"{limit_std} (Natural Ester / Ester Fluid) — PDF limit {reject:.0f} ppm"
    elif pdf_limit and not is_ester:
        reject = float(pdf_limit)
        basis  = f"{limit_std} — PDF limit {reject:.0f} ppm"
    else:
        reject, basis = _is1866_limits(kv)

    if ppm is None:
        return "ND", "Not Detected / Not Reported in PDF", "#484f58", reject, reject, basis
    if ppm <= reject:
        return "ACCEPTABLE", f"Acceptable — ≤ {reject:.0f} ppm limit", "#3fb950", reject, reject, basis
    return "UNACCEPTABLE", f"Unacceptable — exceeds {reject:.0f} ppm limit. Corrective action required.", "#f85149", reject, reject, basis


# ─── Helper: per-PDF analysis text ───────────────────────────────────────────

def _analysis_text(ppm, v_code, kv, caution, reject) -> str:
    kv_str = f"{kv:.0f} kV" if kv else "unknown voltage class"
    if ppm is None:
        return (
            "Water content by Karl Fischer method was <strong>not found</strong> in this PDF. "
            "Verify the source document — the field may be absent or in a non-standard format."
        )
    if v_code == "ACCEPTABLE":
        return (
            f"At <strong>{ppm:.1f} ppm</strong>, moisture is within the acceptable limit "
            f"(≤ {reject:.0f} ppm). "
            f"No corrective action required. Continue routine monitoring."
        )
    if v_code == "UNACCEPTABLE":
        return (
            f"At <strong>{ppm:.1f} ppm</strong>, moisture exceeds the stated limit "
            f"({reject:.0f} ppm) for {kv_str}. "
            f"<br><strong>Immediate actions:</strong> "
            f"(1) Vacuum dehydration / filtration — target well below {reject:.0f} ppm post-treatment. "
            f"(2) Inspect breather / conservator seal for integrity failure. "
            f"(3) Verify BDV before continued operation at rated load. "
            f"(4) Assess remaining insulation life if paper moisture is suspected. "
            f"If moisture cannot be reduced after two passes, oil replacement is required."
        )
    return ""



# ─── Groq AI Analysis ────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _groq_analyse(ppm, kv, fluid_type, limit_std, trufil_verdict, equip, sampling_point, groq_key):
    """Call Groq llama-3.3-70b-versatile for independent moisture assessment. Cached per report."""
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
            f"Provide a concise expert assessment covering:\n"
            f"1. Moisture status and risk to insulation system (paper + oil)\n"
            f"2. Likely root causes if elevated\n"
            f"3. Specific remediation steps with priority\n"
            f"4. Monitoring / follow-up recommendation\n\n"
            f"Keep it technically precise, 150-200 words, no bullet headers, flowing prose."
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


# ─── Sidebar — Groq API key ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🤖 AI Analysis")
    st.markdown(
        "<div style='font-size:.8rem;color:#888;margin-bottom:.6rem;'>"
        "Optional — enter a Groq API key to enable per-report AI moisture assessment "
        "(llama-3.3-70b-versatile)."
        "</div>",
        unsafe_allow_html=True,
    )
    groq_key_input = st.text_input(
        "Groq API Key",
        type="password",
        placeholder="gsk_...",
        label_visibility="collapsed",
    )
    if groq_key_input:
        st.success("✅ AI analysis enabled", icon="🤖")
    else:
        st.caption("No key — app runs without AI panel.")


st.set_page_config(page_title="Water Content Analyser", page_icon="💧", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', sans-serif; }
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1200px; }

/* ── Hero ── */
.hero {
    background: linear-gradient(135deg, #060d1a 0%, #0a1f3d 40%, #0d3b6e 75%, #1565c0 100%);
    border-radius: 16px; padding: 2rem 2.4rem 1.8rem;
    margin-bottom: 1.8rem;
    box-shadow: 0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.06);
    position: relative; overflow: hidden;
}
.hero::before {
    content:''; position:absolute; top:-40px; right:-40px;
    width:220px; height:220px; border-radius:50%;
    background: radial-gradient(circle, rgba(21,101,192,0.3) 0%, transparent 70%);
    pointer-events: none;
}
.hero h1 { color: #e3f2fd; font-size: 1.85rem; font-weight: 800; margin: 0 0 .35rem;
           letter-spacing: -.02em; }
.hero p  { color: #90caf9; font-size: .875rem; margin: 0; opacity: .9; }
.hero-badge { display:inline-block; background:rgba(79,195,247,.15); border:1px solid rgba(79,195,247,.3);
              border-radius:20px; padding:.2rem .75rem; font-size:.72rem; font-weight:600;
              color:#4fc3f7; letter-spacing:.06em; margin-right:.4rem; margin-top:.6rem; }

/* ── Summary cards ── */
.metric-row { display:flex; gap:12px; margin-bottom:1.6rem; flex-wrap:wrap; }
.metric-card {
    flex:1 1 130px; border-radius:14px; padding:1.1rem 1.2rem;
    text-align:center; position:relative; overflow:hidden;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    border: 1px solid transparent;
}
.metric-card::after {
    content:''; position:absolute; bottom:0; left:0; right:0; height:3px;
}
.metric-card .mc-num { font-size:2.4rem; font-weight:800; line-height:1.05; letter-spacing:-.03em; }
.metric-card .mc-lbl { font-size:.7rem; font-weight:700; text-transform:uppercase;
                        letter-spacing:.1em; opacity:.7; margin-top:5px; }
.mc-total { background:linear-gradient(135deg,#e8eaf6,#ede7f6); color:#1a237e;
            border-color:#c5cae9; }
.mc-total::after { background: #3f51b5; }
.mc-bad   { background:linear-gradient(135deg,#ffebee,#fce4ec); color:#b71c1c;
            border-color:#ffcdd2; }
.mc-bad::after   { background: #e53935; }
.mc-good  { background:linear-gradient(135deg,#e8f5e9,#f1f8e9); color:#1b5e20;
            border-color:#c8e6c9; }
.mc-good::after  { background: #43a047; }
.mc-nd    { background:linear-gradient(135deg,#f5f5f5,#eeeeee); color:#424242;
            border-color:#e0e0e0; }

/* ── Tier headers ── */
.tier-header {
    border-radius:12px; padding:.85rem 1.5rem;
    margin: 1.4rem 0 .7rem; font-size:1.05rem; font-weight:700;
    display:flex; align-items:center; gap:.6rem;
    box-shadow: 0 3px 12px rgba(0,0,0,0.12);
}
.tier-bad  { background:linear-gradient(90deg,#7f0000,#c0392b,#e74c3c); color:#fff; }
.tier-good { background:linear-gradient(90deg,#1b5e20,#27ae60,#2ecc71); color:#fff; }
.tier-count { background:rgba(255,255,255,.22); border-radius:20px;
              padding:.1rem .65rem; font-size:.8rem; margin-left:auto; }

/* ── Expander card interior ── */
.verdict-strip {
    border-radius:10px; padding:.85rem 1.2rem; margin-bottom:.9rem;
    font-weight:700; display:flex; align-items:center; gap:.6rem;
    font-size:1rem; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.vs-bad  { background:linear-gradient(90deg,#fff5f5,#fff);
           border-left:5px solid #c0392b; color:#7f0000; }
.vs-good { background:linear-gradient(90deg,#f0fff8,#fff);
           border-left:5px solid #27ae60; color:#1b5e20; }
.vs-nd   { background:#f8f8f8; border-left:5px solid #bbb; color:#555; }
.vs-value { font-size:1.5rem; font-weight:800; letter-spacing:-.02em; }
.vs-meta  { font-size:.8rem; font-weight:500; opacity:.75; margin-top:.1rem; }

/* ── Water bar ── */
.wbar-wrap { margin:.4rem 0 1.2rem; }
.wbar-track { background:#e8e8e8; border-radius:50px; height:18px;
              width:100%; overflow:hidden; position:relative; }
.wbar-fill { height:100%; border-radius:50px;
             transition: width .6s cubic-bezier(.4,0,.2,1); }
.wbar-label { display:flex; justify-content:space-between; align-items:center;
              margin-top:.35rem; font-size:.75rem; color:#888; }
.wbar-label strong { font-weight:700; }

/* ── Analysis box ── */
.analysis-box {
    border-radius:10px; padding:.9rem 1.2rem; margin-bottom:.8rem;
    font-size:.875rem; line-height:1.7;
    box-shadow: inset 0 1px 0 rgba(255,255,255,.5);
}
.ab-bad  { background:#fff5f5; border-left:4px solid #c0392b; color:#4a0000; }
.ab-good { background:#f0fff8; border-left:4px solid #27ae60; color:#1a4a2a; }
.ab-nd   { background:#f8f8f8; border-left:4px solid #bbb;    color:#555; }

/* ── Section label ── */
.slabel {
    font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.12em;
    color:#999; margin: 1.2rem 0 .5rem; padding-bottom:.35rem;
    border-bottom: 1px solid #eaeaea; display:flex; align-items:center; gap:.4rem;
}

/* ── Gas bars ── */
.gas-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap:10px; margin-top:.4rem; }
.gas-card {
    background:#f9fbff; border:1px solid #e3eaf5; border-radius:10px;
    padding:.75rem 1rem; position:relative; overflow:hidden;
}
.gas-card::before {
    content:''; position:absolute; top:0; left:0; bottom:0; width:4px;
    border-radius:4px 0 0 4px;
}
.gas-card.gc-ok::before   { background:#27ae60; }
.gas-card.gc-warn::before { background:#f39c12; }
.gas-card.gc-bad::before  { background:#e74c3c; }
.gas-card.gc-na::before   { background:#cccccc; }
.gas-name { font-size:.7rem; font-weight:700; text-transform:uppercase;
            letter-spacing:.1em; color:#888; margin-bottom:.15rem; }
.gas-formula { font-size:.65rem; color:#aaa; margin-left:.3rem; }
.gas-value-row { display:flex; align-items:baseline; gap:.4rem; margin-bottom:.5rem; }
.gas-val  { font-size:1.45rem; font-weight:800; letter-spacing:-.02em; line-height:1; }
.gas-unit { font-size:.7rem; color:#999; font-weight:500; }
.gas-limit{ font-size:.7rem; color:#aaa; margin-left:auto; }
.gas-bar-track { background:#e8ecf0; border-radius:50px; height:7px; overflow:hidden; }
.gas-bar-fill  { height:100%; border-radius:50px; }
.gc-ok .gas-val   { color:#27ae60; }
.gc-warn .gas-val { color:#f39c12; }
.gc-bad .gas-val  { color:#e74c3c; }
.gc-na .gas-val   { color:#aaa; }

/* ── Extra tests row ── */
.extra-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr));
              gap:8px; margin-top:.4rem; }
.ecell { background:#f8faff; border:1px solid #dde3f0; border-radius:8px;
         padding:.65rem .9rem; }
.ecell-lbl { font-size:.6rem; font-weight:700; text-transform:uppercase;
             letter-spacing:.1em; color:#999; margin-bottom:.2rem; }
.ecell-val { font-size:1.05rem; font-weight:700; color:#1a237e; }
.ecell-sub { font-size:.65rem; color:#aaa; margin-top:.1rem; }
.ecell-ok  .ecell-val { color:#27ae60; }
.ecell-bad .ecell-val { color:#e74c3c; }

/* ── Meta grid ── */
.meta-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:.4rem; }
.mcell { background:#f8faff; border:1px solid #dde3f0; border-radius:8px; padding:.6rem .85rem; }
.mcell-lbl { font-size:.58rem; text-transform:uppercase; letter-spacing:.12em;
             color:#aaa; margin-bottom:3px; font-weight:600; }
.mcell-val { font-size:.82rem; font-weight:600; color:#1a237e; word-break:break-word; }

/* ── TRU-FIL / basis row ── */
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:.4rem; }
.info-pill { background:#f0f4ff; border:1px solid #c5cae9; border-radius:8px;
             padding:.65rem .9rem; }
.ip-lbl { font-size:.6rem; font-weight:700; text-transform:uppercase;
          letter-spacing:.1em; color:#9fa8da; margin-bottom:.25rem; }
.ip-val { font-size:.9rem; font-weight:700; color:#1a237e; }
.ip-sub { font-size:.72rem; color:#888; margin-top:.15rem; }

/* ── AI panel ── */
.ai-panel {
    background: linear-gradient(135deg, #060e1c 0%, #0d1b2e 100%);
    border: 1px solid #1e3a5f; border-left: 4px solid #4fc3f7;
    border-radius: 12px; padding: 1.1rem 1.4rem; margin-top: 1rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.2);
}
.ai-header {
    font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.14em;
    color:#4fc3f7; margin-bottom:.7rem; padding-bottom:.5rem;
    border-bottom:1px solid #1e3a5f; display:flex; align-items:center; gap:.4rem;
}
.ai-body { font-size:.875rem; line-height:1.75; color:#b0c4d8; }
.ai-body strong { color:#e3f2fd; }
.ai-hint { background:#060e1c; border:1px dashed #1e3a5f; border-radius:8px;
           padding:.6rem 1rem; font-size:.75rem; color:#4a7a9b; margin-top:.8rem;
           text-align:center; }

/* ── Reference box ── */
.ref-box {
    background: linear-gradient(135deg, #e3f2fd, #e8eaf6);
    border: 1px solid #bbdefb; border-left: 5px solid #1565c0;
    border-radius: 12px; padding: 1.1rem 1.5rem; margin-top: 1.8rem;
    box-shadow: 0 2px 10px rgba(21,101,192,0.08);
}
.ref-title { font-size:.9rem; font-weight:800; color:#0d3b6e;
             text-transform:uppercase; letter-spacing:.06em; margin-bottom:.7rem; }
.ref-row { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:.3rem; font-size:.82rem; }
.ref-lbl { color:#555; min-width:100px; font-weight:600; }

/* ── Misc ── */
[data-testid="stFileUploader"] { border-radius:12px !important; }
[data-testid="stProgressBar"] > div { background: linear-gradient(90deg,#1565c0,#4fc3f7) !important; }
#MainMenu,footer,header { visibility:hidden; }
.stExpander { border-radius:12px !important; border: 1px solid #e3eaf5 !important; }
</style>
""", unsafe_allow_html=True)

# ─── Hero ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero">
  <h1>💧 Transformer Oil — Water Content Analyser</h1>
  <p>Karl Fischer Method &nbsp;·&nbsp; IS 1866 : 2017 / IEC 60422 &nbsp;·&nbsp; Multi-PDF Batch Analysis</p>
  <div style="margin-top:.7rem;">
    <span class="hero-badge">IS 13567</span>
    <span class="hero-badge">ASTM D1533</span>
    <span class="hero-badge">IEC 60814</span>
    <span class="hero-badge">TRU-FIL · SGS · CPRI</span>
    <span class="hero-badge">40 ppm Limit</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ─── Upload ───────────────────────────────────────────────────────────────────
pdfs = st.file_uploader(
    "📄 Upload Oil Test Report PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

if not pdfs:
    st.markdown("""
    <div style="border:2px dashed #90caf9;border-radius:16px;padding:3rem;
                text-align:center;color:#5c85b5;margin-top:.8rem;
                background:linear-gradient(135deg,#f0f7ff,#e8f0fe);">
      <div style="font-size:3.5rem;margin-bottom:.8rem;">💧</div>
      <div style="font-size:1.15rem;font-weight:700;color:#0d3b6e;">Drop TRU-FIL / SGS / CPRI transformer oil report PDFs</div>
      <div style="font-size:.85rem;margin-top:.5rem;opacity:.7;max-width:440px;margin-left:auto;margin-right:auto;line-height:1.6;">
        Water content, DGA gases, BDV, IFT and more extracted automatically.<br>
        IS 1866 : 2017 · 40 ppm limit · Multi-PDF batch supported.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ─── Process ──────────────────────────────────────────────────────────────────
if "wc_cache" not in st.session_state:
    st.session_state.wc_cache = {}

cache = st.session_state.wc_cache
bar   = st.progress(0, text="Reading PDFs …")

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
                ppm=None, limit=None, trufil_verdict="ERR",
                equipment="—", voltage_kv=None, rating="—",
                report_no="—", report_date="—", sampling_point="—",
                fluid_type="—", limit_std="—",
                gases={}, tdcg=None, owner="—", location="—",
                extraction_method=f"error:{exc}",
            )
        os.unlink(tp)
        ppm = info["ppm"]
        kv  = info["voltage_kv"]
        v_code, v_label, colour, caution, reject, basis = classify(
            ppm, kv, info["limit"], info.get("fluid_type","—"), info.get("limit_std","—")
        )
        cache[key] = dict(
            name=f.name, **info,
            verdict=v_code, verdict_label=v_label,
            colour=colour, caution=caution, reject=reject, basis=basis,
        )
    bar.progress((i + 1) / len(pdfs), text=f"Processing {i+1}/{len(pdfs)} — {f.name}")

bar.empty()

# ─── Sort ─────────────────────────────────────────────────────────────────────
_ORDER = {"UNACCEPTABLE": 0, "ACCEPTABLE": 1, "ND": 2}
rlist  = sorted(
    cache.values(),
    key=lambda r: (_ORDER.get(r["verdict"], 9), r["name"].lower()),
)

n_total = len(rlist)
n_ok    = sum(1 for r in rlist if r["verdict"] == "ACCEPTABLE")
n_bad   = sum(1 for r in rlist if r["verdict"] == "UNACCEPTABLE")
n_nd    = sum(1 for r in rlist if r["verdict"] == "ND")

# ─── Summary metric cards ─────────────────────────────────────────────────────
nd_card = (f"<div class='metric-card mc-nd'>"
           f"<div class='mc-num'>{n_nd}</div>"
           f"<div class='mc-lbl'>⚪ Not Detected</div></div>") if n_nd else ""

st.markdown(f"""
<div class="metric-row">
  <div class="metric-card mc-total">
    <div class="mc-num">{n_total}</div><div class="mc-lbl">📋 Total PDFs</div></div>
  <div class="metric-card mc-bad">
    <div class="mc-num">{n_bad}</div><div class="mc-lbl">❌ Unacceptable</div></div>
  <div class="metric-card mc-good">
    <div class="mc-num">{n_ok}</div><div class="mc-lbl">✅ Acceptable</div></div>
  {nd_card}
</div>
""", unsafe_allow_html=True)


# ─── PDF Report builder ───────────────────────────────────────────────────────
import io, datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors as rl_colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

def _rl_safe(text):
    if text is None:
        return "—"
    s = str(text)
    s = s.replace("—", "-").replace("–", "-").replace("'", "'")
    s = s.replace("'", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u00a0", " ").replace("•", "*").replace("°", " deg")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s

def _build_pdf_report(rlist_data):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm,  bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_s  = ParagraphStyle("T",  parent=styles["Title"],
        fontSize=18, textColor=rl_colors.HexColor("#0d3b6e"), spaceAfter=4)
    h1_s     = ParagraphStyle("H1", parent=styles["Heading1"],
        fontSize=13, textColor=rl_colors.HexColor("#0d3b6e"), spaceBefore=14, spaceAfter=4)
    h2_s     = ParagraphStyle("H2", parent=styles["Heading2"],
        fontSize=10, textColor=rl_colors.HexColor("#444"), spaceBefore=8, spaceAfter=3)
    normal_s = ParagraphStyle("N",  parent=styles["Normal"], fontSize=9, leading=13)
    small_s  = ParagraphStyle("S",  parent=styles["Normal"],
        fontSize=8, textColor=rl_colors.HexColor("#666"), leading=11)

    story = []
    story.append(Paragraph("Transformer Oil Analysis", title_s))
    story.append(Paragraph("Water Content Diagnostic Report", h1_s))
    story.append(Paragraph(
        f"Generated: {datetime.datetime.now().strftime('%d-%m-%Y %H:%M')}  |  "
        "IS 1866:2017 Table-5  |  Limit: 40 ppm max  |  "
        "Method: Karl Fischer (IS 13567 / ASTM D1533 / IEC 60814)",
        small_s))
    story.append(HRFlowable(width="100%", thickness=1.5,
                             color=rl_colors.HexColor("#0d3b6e"), spaceAfter=12))

    n_b = sum(1 for r in rlist_data if r["verdict"] == "UNACCEPTABLE")
    n_g = sum(1 for r in rlist_data if r["verdict"] == "ACCEPTABLE")
    s_data = [
        ["Total Reports", "Unacceptable (>40 ppm)", "Acceptable (≤40 ppm)"],
        [str(len(rlist_data)), str(n_b), str(n_g)],
    ]
    s_tbl = Table(s_data, colWidths=[4.5*cm, 5*cm, 5*cm])
    s_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), rl_colors.HexColor("#0d3b6e")),
        ("TEXTCOLOR",     (0,0),(-1,0), rl_colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 10),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [rl_colors.HexColor("#f0f4ff"), rl_colors.white]),
        ("GRID",          (0,0),(-1,-1), 0.5, rl_colors.HexColor("#cccccc")),
        ("TOPPADDING",    (0,0),(-1,-1), 7),
        ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ("TEXTCOLOR",     (1,1),(1,1),  rl_colors.HexColor("#c0392b")),
        ("TEXTCOLOR",     (2,1),(2,1),  rl_colors.HexColor("#27ae60")),
        ("FONTNAME",      (0,1),(-1,1), "Helvetica-Bold"),
        ("FONTSIZE",      (0,1),(-1,1), 14),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 16))

    for r in rlist_data:
        ppm    = r["ppm"]
        v_code = r["verdict"]
        equip  = _rl_safe(r["equipment"])
        kv     = r["voltage_kv"]
        kv_str = f"{kv:.0f} kV" if kv else "—"
        ppm_s  = f"{ppm:.1f} ppm" if ppm is not None else "N/D"
        scolor = (rl_colors.HexColor("#c0392b") if v_code == "UNACCEPTABLE"
                  else rl_colors.HexColor("#27ae60"))
        icon   = "[UNACCEPTABLE]" if v_code == "UNACCEPTABLE" else "[ACCEPTABLE]"

        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=rl_colors.HexColor("#cccccc"), spaceBefore=6))
        story.append(Paragraph(
            f"<font color='#{'c0392b' if v_code=='UNACCEPTABLE' else '27ae60'}'>"
            f"{icon} {equip}</font>"
            f" - {_rl_safe(r['sampling_point'])}",
            h1_s))

        w_data = [
            ["Parameter",      "Value",   "Standard / Basis"],
            ["Water Content",  ppm_s,     _rl_safe(r.get("basis","IS 1866:2017 Table-5"))],
            ["Limit",          f"{r['reject']:.0f} ppm max", "IS 1866:2017 Table-5"],
            ["Voltage Class",  kv_str,    "From PDF"],
            ["TRU-FIL Verdict",_rl_safe(r["trufil_verdict"]), "As stated in PDF"],
        ]
        final_bg = (rl_colors.HexColor("#fff0f0") if v_code == "UNACCEPTABLE"
                    else rl_colors.HexColor("#f0fff4"))
        w_tbl = Table(w_data, colWidths=[4.5*cm, 3.5*cm, 9*cm])
        w_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  rl_colors.HexColor("#1565c0")),
            ("TEXTCOLOR",     (0,0),(-1,0),  rl_colors.white),
            ("FONTNAME",      (0,0),(-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8.5),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-2), [rl_colors.HexColor("#eef4ff"), rl_colors.white]),
            ("BACKGROUND",    (0,1),(-1,1),  final_bg),
            ("FONTNAME",      (0,1),(-1,1),  "Helvetica-Bold"),
            ("TEXTCOLOR",     (1,1),(1,1),   scolor),
            ("GRID",          (0,0),(-1,-1), 0.5, rl_colors.HexColor("#cccccc")),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ]))
        story.append(w_tbl)
        story.append(Spacer(1, 6))

        v_tbl = Table([[Paragraph(
            f"<font color='#{'c0392b' if v_code=='UNACCEPTABLE' else '27ae60'}'>"
            f"<b>{icon} {v_code}</b></font>"
            f" - Water: <b>{ppm_s}</b> | Limit: 40 ppm | {kv_str}",
            normal_s,
        )]], colWidths=[17*cm])
        v_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), final_bg),
            ("LINEABOVE",     (0,0),(-1,0),  2, scolor),
            ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ("TOPPADDING",    (0,0),(-1,-1), 7),
            ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ]))
        story.append(v_tbl)

        if v_code == "UNACCEPTABLE":
            story.append(Paragraph("Immediate Actions Required", h2_s))
            story.append(Paragraph(
                "(1) Vacuum dehydration / filtration — target well below 40 ppm post-treatment.  "
                "(2) Inspect breather / conservator seal for integrity failure.  "
                "(3) Verify BDV before continued operation at rated load.  "
                "(4) Assess remaining insulation life if paper moisture is suspected.",
                normal_s))

        story.append(Paragraph("Equipment Details", h2_s))
        eq_data = [
            ["Equipment",    equip,                        "Report No.",   _rl_safe(r["report_no"])],
            ["Voltage Class",kv_str,                       "Report Date",  _rl_safe(r["report_date"])],
            ["Rating",       _rl_safe(r["rating"]),        "Sampling Pt.", _rl_safe(r["sampling_point"])],
            ["Insul. Fluid", _rl_safe(r.get("fluid_type","—")), "Extraction", _rl_safe(r.get("extraction_method","—"))],
        ]
        eq_t = Table(eq_data, colWidths=[3.5*cm, 5*cm, 3.5*cm, 5*cm])
        eq_t.setStyle(TableStyle([
            ("FONTNAME",      (0,0),(0,-1), "Helvetica-Bold"),
            ("FONTNAME",      (2,0),(2,-1), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("ROWBACKGROUNDS",(0,0),(-1,-1), [rl_colors.HexColor("#f0f4ff"), rl_colors.white]),
            ("GRID",          (0,0),(-1,-1), 0.4, rl_colors.HexColor("#dddddd")),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ]))
        story.append(eq_t)
        story.append(Spacer(1, 10))

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ─── PDF Export button ────────────────────────────────────────────────────────
col_dl, _ = st.columns([1, 3])
with col_dl:
    try:
        pdf_bytes = _build_pdf_report(rlist)
        st.download_button(
            label="📄 Download PDF Report",
            data=pdf_bytes,
            file_name=f"water_content_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            use_container_width=True,
            type="primary",
        )
    except Exception as e:
        st.warning(f"PDF export unavailable: {e}")


# ─── Helpers ──────────────────────────────────────────────────────────────────
_ICONS = {"ACCEPTABLE": "🟢", "UNACCEPTABLE": "🔴", "ND": "⚪"}

def _gas_card_html(gas, data, ref_limit):
    """Render a single gas mini-card with coloured bar."""
    val   = data["value"]
    limit = data.get("limit") or ref_limit or None
    label = _GAS_LABELS.get(gas, gas)
    pct   = min(val / limit * 100, 100) if limit else 50
    if limit is None:
        cls, bar_col = "gc-na", "#cccccc"
    elif val >= limit:
        cls, bar_col = "gc-bad", "#e74c3c"
    elif val >= limit * 0.7:
        cls, bar_col = "gc-warn", "#f39c12"
    else:
        cls, bar_col = "gc-ok", "#27ae60"
    limit_str = f"Limit: {limit:.0f}" if limit else "No limit ref"
    return (
        f"<div class='gas-card {cls}'>"
        f"<div class='gas-name'>{label} <span class='gas-formula'>({gas})</span></div>"
        f"<div class='gas-value-row'>"
        f"<span class='gas-val'>{val:.1f}</span>"
        f"<span class='gas-unit'>ppm v/v</span>"
        f"<span class='gas-limit'>{limit_str}</span>"
        f"</div>"
        f"<div class='gas-bar-track'>"
        f"<div class='gas-bar-fill' style='width:{pct:.1f}%;background:{bar_col};'></div>"
        f"</div>"
        f"</div>"
    )


def _extra_cell(label, val, unit, limit=None, lower_better=True):
    if val is None:
        return f"<div class='ecell'><div class='ecell-lbl'>{label}</div><div class='ecell-val' style='color:#ccc;'>—</div></div>"
    ok = (val <= limit) if (limit and lower_better) else (val >= limit if limit else True)
    cls = "ecell-ok" if ok else "ecell-bad"
    lim_str = f"Limit: {limit} {unit}" if limit else ""
    return (
        f"<div class='ecell {cls}'>"
        f"<div class='ecell-lbl'>{label}</div>"
        f"<div class='ecell-val'>{val:.2f} <span style='font-size:.65rem;font-weight:400;color:#aaa;'>{unit}</span></div>"
        f"<div class='ecell-sub'>{lim_str}</div>"
        f"</div>"
    )


# ─── Grouped report cards ─────────────────────────────────────────────────────
TIERS = [
    ("UNACCEPTABLE", "Unacceptable — Exceeds 40 ppm", "bad"),
    ("ACCEPTABLE",   "Acceptable — Within safe range (≤ 40 ppm)", "good"),
]

for tier, label_str, css in TIERS:
    group = [r for r in rlist if r["verdict"] == tier]
    if not group:
        continue
    st.markdown(
        f"<div class='tier-header tier-{css}'>"
        f"{_ICONS[tier]} {label_str}"
        f"<span class='tier-count'>{len(group)}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    for r in group:
        ppm      = r["ppm"]
        v_code   = r["verdict"]
        kv       = r["voltage_kv"]
        ppm_disp = f"{ppm:.1f}" if ppm is not None else "N/D"
        kv_s     = f"{kv:.0f} kV" if kv else "—"
        equip    = r["equipment"]
        pct      = min(ppm / r["reject"] * 100, 100) if (ppm and r["reject"]) else 0
        bar_col  = "#e74c3c" if v_code == "UNACCEPTABLE" else "#27ae60"
        vc_css   = "bad" if v_code == "UNACCEPTABLE" else "good"
        gases    = r.get("gases", {})

        title = f"{_ICONS[v_code]}  {equip}   ·   {ppm_disp} ppm   ·   {kv_s}   ·   {r['name']}"

        with st.expander(title, expanded=(v_code == "UNACCEPTABLE")):

            # ── Verdict strip ─────────────────────────────────────────────────
            tf_color_hex = "27ae60" if r["trufil_verdict"] == "Acceptable" else (
                           "c0392b" if r["trufil_verdict"] == "Not Acceptable" else "888888")
            st.markdown(
                f"<div class='verdict-strip vs-{vc_css}'>"
                f"<div>"
                f"<div class='vs-value'>{ppm_disp} <span style='font-size:.9rem;font-weight:500;opacity:.7;'>ppm H₂O</span></div>"
                f"<div class='vs-meta'>Limit {r['reject']:.0f} ppm &nbsp;·&nbsp; {kv_s} &nbsp;·&nbsp; {r.get('fluid_type','Mineral Oil')}</div>"
                f"</div>"
                f"<div style='margin-left:auto;text-align:right;'>"
                f"<div style='font-size:1.1rem;font-weight:800;'>{_ICONS[v_code]} {v_code}</div>"
                f"<div style='font-size:.75rem;opacity:.7;margin-top:.1rem;'>TRU-FIL: "
                f"<span style='color:#{tf_color_hex};font-weight:700;'>{r['trufil_verdict']}</span></div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # ── Water content bar ─────────────────────────────────────────────
            if ppm is not None:
                bar_grad = f"linear-gradient(90deg, {'#c0392b,#e74c3c' if v_code=='UNACCEPTABLE' else '#27ae60,#2ecc71'})"
                st.markdown(
                    f"<div class='wbar-wrap'>"
                    f"<div class='wbar-track'>"
                    f"<div class='wbar-fill' style='width:{pct:.1f}%;background:{bar_grad};'></div>"
                    f"</div>"
                    f"<div class='wbar-label'>"
                    f"<span>0 ppm</span>"
                    f"<span><strong>{pct:.1f}%</strong> of {r['reject']:.0f} ppm limit</span>"
                    f"<span>{r['reject']:.0f} ppm</span>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # ── Analysis text ─────────────────────────────────────────────────
            st.markdown(
                f"<div class='analysis-box ab-{vc_css}'>"
                f"{_analysis_text(ppm, v_code, kv, r['caution'], r['reject'])}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # ── DGA Gas bars ──────────────────────────────────────────────────
            if gases:
                st.markdown("<div class='slabel'>🧪 Dissolved Gas Analysis (DGA)</div>",
                            unsafe_allow_html=True)
                # Show key fault gases first, then others
                key_order = ["H2", "C2H2", "C2H4", "CH4", "C2H6", "CO", "CO2", "O2", "N2"]
                ordered = [g for g in key_order if g in gases] + \
                          [g for g in gases if g not in key_order]
                cards_html = "".join(
                    _gas_card_html(g, gases[g], _GAS_LIMITS.get(g))
                    for g in ordered
                )
                st.markdown(f"<div class='gas-grid'>{cards_html}</div>", unsafe_allow_html=True)

                # TDCG
                tdcg = r.get("tdcg")
                if tdcg:
                    tdcg_col = "#e74c3c" if tdcg > 720 else ("#f39c12" if tdcg > 314 else "#27ae60")
                    st.markdown(
                        f"<div style='margin-top:.6rem;padding:.5rem 1rem;background:#f8faff;"
                        f"border-radius:8px;border:1px solid #e3eaf5;display:inline-block;'>"
                        f"<span style='font-size:.65rem;font-weight:700;text-transform:uppercase;"
                        f"letter-spacing:.1em;color:#aaa;'>TDCG</span>"
                        f"<span style='font-size:1.1rem;font-weight:800;color:{tdcg_col};"
                        f"margin-left:.6rem;'>{tdcg:.0f} ppm</span>"
                        f"<span style='font-size:.7rem;color:#aaa;margin-left:.4rem;'>"
                        f"(IEEE C57.104 L1&lt;314 · L2&lt;720 · L3&lt;1920)</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    "<div style='font-size:.78rem;color:#bbb;font-style:italic;margin:.4rem 0 .8rem;'>"
                    "ℹ️ No DGA gas values detected in this PDF.</div>",
                    unsafe_allow_html=True,
                )

            # ── Physical / chemical tests ─────────────────────────────────────
            bdv      = r.get("bdv")
            ift      = r.get("ift")
            ddf      = r.get("ddf")
            acidity  = r.get("acidity")
            has_extra = any(x is not None for x in [bdv, ift, ddf, acidity])
            if has_extra:
                st.markdown("<div class='slabel'>⚗️ Physical &amp; Chemical Tests</div>",
                            unsafe_allow_html=True)
                bdv_v = bdv["value"] if isinstance(bdv, dict) else bdv
                bdv_l = bdv["limit"] if isinstance(bdv, dict) else None
                ift_v = ift["value"] if isinstance(ift, dict) else ift
                ift_l = ift["limit"] if isinstance(ift, dict) else None
                ddf_v = ddf["value"] if isinstance(ddf, dict) else ddf
                ddf_l = ddf["limit"] if isinstance(ddf, dict) else None
                ac_v  = acidity["value"] if isinstance(acidity, dict) else acidity
                ac_l  = acidity["limit"] if isinstance(acidity, dict) else None
                cells = (
                    _extra_cell("BDV", bdv_v, "kV", bdv_l, lower_better=False) +
                    _extra_cell("IFT", ift_v, "mN/m", ift_l, lower_better=False) +
                    _extra_cell("DDF (90°C)", ddf_v, "%", ddf_l, lower_better=True) +
                    _extra_cell("Acidity", ac_v, "mg KOH/g", ac_l, lower_better=True)
                )
                st.markdown(f"<div class='extra-grid'>{cells}</div>", unsafe_allow_html=True)

            st.markdown("<div style='margin:.4rem 0;'></div>", unsafe_allow_html=True)

            # ── TRU-FIL verdict + basis ───────────────────────────────────────
            st.markdown("<div class='slabel'>📋 Verdict &amp; Assessment Basis</div>",
                        unsafe_allow_html=True)
            limit_str_tf = f"Stated limit: {r['limit']:.0f} ppm" if r.get('limit') else "Stated limit: —"
            st.markdown(
                f"<div class='two-col'>"
                f"<div class='info-pill'>"
                f"<div class='ip-lbl'>TRU-FIL PDF Verdict</div>"
                f"<div class='ip-val' style='color:#{tf_color_hex};'>{r['trufil_verdict']}</div>"
                f"<div class='ip-sub'>{limit_str_tf}</div>"
                f"</div>"
                f"<div class='info-pill'>"
                f"<div class='ip-lbl'>Assessment Basis</div>"
                f"<div class='ip-val' style='font-size:.82rem;color:#374151;'>{r['basis']}</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            st.divider()

            # ── Equipment & Report metadata grid ──────────────────────────────
            st.markdown("<div class='slabel'>🔧 Equipment &amp; Report Details</div>",
                        unsafe_allow_html=True)
            fluid    = r.get("fluid_type", "—")
            lim_s    = r.get("limit_std", "—")
            owner    = r.get("owner", "—") or "—"
            location = r.get("location", "—") or "—"
            st.markdown(
                f"<div class='meta-grid'>"
                f"<div class='mcell'><div class='mcell-lbl'>Report No.</div>"
                f"<div class='mcell-val'>{r['report_no']}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Report Date</div>"
                f"<div class='mcell-val'>{r['report_date']}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Voltage Class</div>"
                f"<div class='mcell-val'>{kv_s}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Rating</div>"
                f"<div class='mcell-val'>{r['rating']}</div></div>"
                f"<div class='mcell' style='grid-column:span 2'><div class='mcell-lbl'>Equipment Designation</div>"
                f"<div class='mcell-val'>{equip}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Insulating Fluid</div>"
                f"<div class='mcell-val'>{fluid}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Limit Standard</div>"
                f"<div class='mcell-val'>{lim_s}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Owner</div>"
                f"<div class='mcell-val'>{owner}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Location / Station</div>"
                f"<div class='mcell-val'>{location}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Extraction Method</div>"
                f"<div class='mcell-val'>{r.get('extraction_method','—')}</div></div>"
                f"<div class='mcell' style='grid-column:span 1'><div class='mcell-lbl'>Sampling Point</div>"
                f"<div class='mcell-val'>{r['sampling_point']}</div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # ── AI Analysis ───────────────────────────────────────────────────
            if groq_key_input and ppm is not None:
                st.markdown(
                    "<div class='ai-panel'>"
                    "<div class='ai-header'>🤖 &nbsp;AI Moisture Assessment — Groq / llama-3.3-70b</div>",
                    unsafe_allow_html=True,
                )
                ai_cache_key = f"ai_{r['name']}_{ppm}_{r.get('voltage_kv')}"
                if ai_cache_key not in st.session_state:
                    with st.spinner(""):
                        st.session_state[ai_cache_key] = _groq_analyse(
                            ppm, r["voltage_kv"], r.get("fluid_type", "—"),
                            r.get("limit_std", "—"), r["trufil_verdict"],
                            r["equipment"], r["sampling_point"], groq_key_input,
                        )
                ai_text = st.session_state.get(ai_cache_key, "")
                st.markdown(
                    f"<div class='ai-body'>{ai_text}</div></div>",
                    unsafe_allow_html=True,
                )
            elif not groq_key_input:
                st.markdown(
                    "<div class='ai-hint'>🤖 Add a Groq API key in the sidebar to enable AI moisture assessment</div>",
                    unsafe_allow_html=True,
                )

# ─── ND group ─────────────────────────────────────────────────────────────────
nd_group = [r for r in rlist if r["verdict"] == "ND"]
if nd_group:
    st.markdown(
        "<div class='tier-header' style='background:linear-gradient(90deg,#424242,#757575);color:#fff;'>"
        "⚪ Not Detected"
        f"<span class='tier-count'>{len(nd_group)}</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    for r in nd_group:
        st.warning(f"**{r['name']}** — water content not found in PDF. Verify source document format.")

# ─── Threshold reference ──────────────────────────────────────────────────────
st.markdown("""
<div class="ref-box">
  <div class="ref-title">📐 Threshold Reference — IS 1866 : 2017 / IEC 60422</div>
  <div class="ref-row">
    <span class="ref-lbl">≤ 40 ppm</span>
    <span style="color:#27ae60;font-weight:700">✅ Acceptable</span>
    <span style="color:#666"> — no action required</span>
  </div>
  <div class="ref-row">
    <span class="ref-lbl">&gt; 40 ppm</span>
    <span style="color:#c0392b;font-weight:700">❌ Unacceptable</span>
    <span style="color:#666"> — immediate filtration / oil replacement</span>
  </div>
  <div style="margin-top:.9rem;padding-top:.7rem;border-top:1px solid #bbdefb;">
    <div class="ref-title" style="font-size:.8rem;margin-bottom:.4rem;">DGA Reference Limits (IEEE C57.104 / IS 9434)</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:6px;font-size:.75rem;color:#444;">
      <span>H₂ &lt; 100 ppm</span><span>CH₄ &lt; 120 ppm</span>
      <span>C₂H₂ &lt; 35 ppm</span><span>C₂H₄ &lt; 50 ppm</span>
      <span>C₂H₆ &lt; 65 ppm</span><span>CO &lt; 350 ppm</span>
      <span>CO₂ &lt; 2500 ppm</span><span>TDCG L1 &lt; 314 ppm</span>
    </div>
  </div>
  <div style="margin-top:.7rem;color:#888;font-size:.72rem;">
    Test method: Karl Fischer titration per IS 13567 / ASTM D1533 / IEC 60814.
    Units: mg/kg (ppm by weight). Limit: 40 ppm max per IS 1866:2017 Table-5.
  </div>
</div>
""", unsafe_allow_html=True)
