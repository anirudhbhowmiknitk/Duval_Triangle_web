"""
water_content_app_v10.py — Transformer Oil Water Content & DGA Analyser
=========================================================================
Run:  streamlit run water_content_app_v10.py

WHAT'S NEW IN v10
------------------
1. THREE INDEPENDENT MOISTURE EXTRACTORS, tried in order of specificity:
     Extractor 1 — TRU-FIL format   (row carries IS/ASTM/IEC code + Max + verdict)
     Extractor 2 — SGS / CPRI format (row carries "mg/KG (ppm)" + Max + verdict,
                                       no IS/ASTM/IEC code, or a generic table row)
     Extractor 3 — Generic / unknown format (last-resort line scan for any
                                       "Water Content" / "Moisture" line)
   The first extractor that returns a value wins, and the matched format is
   recorded and shown to the user.

2. FORMAT-AWARE LIMIT, "AS SUITED":
     - If the PDF itself states a limit for the water-content row, that value
       is ALWAYS used (highest priority — "as suited" to that report).
     - Otherwise, the default lower limit depends on the detected format:
         TRU-FIL            -> 40 ppm  (IS 1866:2017 Table-5, <220 kV)
         SGS / CPRI / Generic -> 45 ppm (default for non-TRU-FIL layouts)
     - Ester / IEEE C57.147 fluids (when stated) use a 450 ppm in-service limit.

3. NO AI / NO EXTERNAL API CALLS. All analysis is rule-based (IS 1866:2017,
   IEC 60422, IEEE C57.104 / IS 9434, IEEE C57.147).

4. Full DGA gas panel, physical/chemical tests (BDV, IFT, DDF, Acidity, TDCG),
   and equipment type/metadata are extracted and displayed for every report.

5. Multi-page PDF report — ONE TRANSFORMER PER PAGE, with water content,
   format/limit basis, full DGA gas table, physical/chemical test table and
   complete equipment & report metadata.

6. Dashboard summary cards: Total PDFs / Unacceptable / Acceptable / Not
   Detected, styled to match the reference dashboard layout.
"""

import os, re, io, tempfile, datetime
import pdfplumber
import streamlit as st


# ════════════════════════════════════════════════════════════════════════════
# EXTRACTOR 1 — TRU-FIL FORMAT
# ────────────────────────────────────────────────────────────────────────────
# Typical row (after pdfplumber collapses wrapped label onto one line):
#   "Water Content By Karl Fischer  mg/KG (ppm)  IS 13567  <value>  <limit> Max  <verdict>"
# The label itself may also wrap as "...Karl Fischer\nMethod" — handled by
# the label-split fallback below.
# ════════════════════════════════════════════════════════════════════════════

_E1_FULL = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer"
    r".*?(?:IS\s+\d+|ASTM\s+D\s*\d+|IEC\s+\d+)\s+"
    r"(\d+(?:\.\d+)?)"            # [1] test result
    r".*?"
    r"(\d+(?:\.\d+)?)\s*Max"      # [2] stated limit
    r"\s+"
    r"(Acceptable|Not Acceptable|Not Specified|NS)",  # [3] vendor verdict
    re.IGNORECASE | re.DOTALL,
)

_E1_PRIMARY = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer"
    r".*?"
    r"(?:IS\s+\d+|ASTM\s+D\s*\d+|IEC\s+\d+)"
    r"\s+"
    r"(\d+(?:\.\d+)?)",           # [1] test result only — no limit/verdict on row
    re.IGNORECASE | re.DOTALL,
)

_E1_LABEL_SPLIT = re.compile(
    r"Water\s+Content\s+By\s+Karl\s+Fischer\s*\n+\s*Method"
    r".*?(\d+(?:\.\d+)?)",
    re.IGNORECASE | re.DOTALL,
)


def _extractor1_trufil(full_text: str):
    """TRU-FIL format: IS/ASTM/IEC standard code present in the test row."""
    m = _E1_FULL.search(full_text)
    if m:
        return dict(
            ppm=float(m.group(1)), limit=float(m.group(2)),
            verdict=m.group(3).strip().title(),
            format="TRU-FIL", method="trufil_full_row",
        )
    m = _E1_PRIMARY.search(full_text)
    if m:
        return dict(
            ppm=float(m.group(1)), limit=None, verdict=None,
            format="TRU-FIL", method="trufil_primary",
        )
    m = _E1_LABEL_SPLIT.search(full_text)
    if m:
        return dict(
            ppm=float(m.group(1)), limit=None, verdict=None,
            format="TRU-FIL", method="trufil_label_split",
        )
    return None


# ════════════════════════════════════════════════════════════════════════════
# EXTRACTOR 2 — SGS / CPRI FORMAT
# ────────────────────────────────────────────────────────────────────────────
# Covers report layouts where the water-content row carries the unit
# "mg/KG (ppm)" with a "Max" limit + verdict but WITHOUT an inline
# IS/ASTM/IEC standard code (common in OLTC compartment rows and SGS/CPRI
# style tables), plus a looser generic-table variant:
#   "Water Content / Moisture Content  ...  <value> ppm/mg/kg  [<limit>]"
# ════════════════════════════════════════════════════════════════════════════

_E2_FULL = re.compile(
    r"(?:Water\s+Content|Moisture\s*Content)(?:\s+By\s+Karl\s+Fischer)?(?:\s+Method)?"
    r"\s+mg/KG\s*\(ppm\)\s+"
    r"(\d+(?:\.\d+)?)"            # [1] test result
    r"\s+"
    r"(\d+(?:\.\d+)?)\s*Max"      # [2] stated limit
    r"\s+"
    r"(Acceptable|Not Acceptable|Not Specified|NS)",  # [3] vendor verdict
    re.IGNORECASE | re.DOTALL,
)

_E2_TABLE = re.compile(
    r"(?:Water\s+Content|Moisture\s*Content)\s*(?:\(.*?\))?"
    r"[^\n\d]{0,40}"
    r"(\d+(?:\.\d+)?)\s*(?:ppm|mg/kg|mg/KG)\b"          # [1] result with explicit unit
    r"(?:[^\n\d]{0,40}(\d+(?:\.\d+)?)\s*(?:ppm|mg/kg|mg/KG)?\s*(?:Max|Limit|Specification)?)?",
    re.IGNORECASE,
)


def _extractor2_sgs_cpri(full_text: str):
    """SGS / CPRI / OLTC-style format: 'mg/KG (ppm)' row without IS/ASTM/IEC code,
    or a generic 'Water/Moisture Content ... <n> ppm' table row."""
    m = _E2_FULL.search(full_text)
    if m:
        return dict(
            ppm=float(m.group(1)), limit=float(m.group(2)),
            verdict=m.group(3).strip().title(),
            format="SGS/CPRI", method="sgs_cpri_full_row",
        )
    m = _E2_TABLE.search(full_text)
    if m:
        limit = float(m.group(2)) if m.group(2) else None
        return dict(
            ppm=float(m.group(1)), limit=limit, verdict=None,
            format="SGS/CPRI", method="sgs_cpri_table",
        )
    return None


# ════════════════════════════════════════════════════════════════════════════
# EXTRACTOR 3 — GENERIC / UNKNOWN FORMAT (last resort)
# ────────────────────────────────────────────────────────────────────────────
# Scans every line of every page for "Water Content" / "Moisture" and grabs
# the first plausible ppm-range number (0-500) on that line. Used only when
# Extractors 1 and 2 both fail.
# ════════════════════════════════════════════════════════════════════════════

_E3_KEYWORD = re.compile(r"(?:Water\s*Content|Moisture)", re.IGNORECASE)
_E3_NUM     = re.compile(r"(\d+(?:\.\d+)?)")


def _extractor3_generic(pages_text: list):
    """Generic fallback: line-scan for a 'Water Content' / 'Moisture' keyword
    and the first plausible ppm value (0-500) on that line."""
    for txt in pages_text:
        for line in txt.splitlines():
            if _E3_KEYWORD.search(line):
                for n in _E3_NUM.findall(line):
                    v = float(n)
                    if 0 <= v <= 500:
                        return dict(
                            ppm=v, limit=None, verdict=None,
                            format="Generic", method="generic_line_scan",
                        )
    return None


# ════════════════════════════════════════════════════════════════════════════
# DISSOLVED GAS ANALYSIS (DGA) — gas value extraction
# ────────────────────────────────────────────────────────────────────────────
# Matches rows like: "Hydrogen (H2)  ppm v/v  IS 9434  <value>  <limit> Max"
# ════════════════════════════════════════════════════════════════════════════

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

# IEEE C57.104 / IS 9434 typical action limits (ppm v/v) — used as display
# reference when a row does not carry its own "Max" limit.
_GAS_LIMITS = {
    "H2": 100, "CH4": 120, "C2H2": 35, "C2H4": 50,
    "C2H6": 65, "CO": 350, "CO2": 2500, "O2": 19000, "N2": 71000,
}
_GAS_LABELS = {
    "H2": "Hydrogen", "CH4": "Methane", "C2H2": "Acetylene",
    "C2H4": "Ethylene", "C2H6": "Ethane", "CO": "Carbon Monoxide",
    "CO2": "Carbon Dioxide", "O2": "Oxygen", "N2": "Nitrogen",
}
_GAS_ORDER = ["H2", "C2H2", "C2H4", "CH4", "C2H6", "CO", "CO2", "O2", "N2"]


def _extract_gases(full_text: str) -> dict:
    """Extract all detectable DGA gas values + their stated limits."""
    gases = {}
    for gas, pat in _GAS_PATTERNS.items():
        m = pat.search(full_text)
        if m:
            try:
                gases[gas] = {"value": float(m.group(1)), "limit": float(m.group(2))}
            except (IndexError, ValueError):
                pass
    return gases


# ════════════════════════════════════════════════════════════════════════════
# EQUIPMENT / REPORT METADATA
# ════════════════════════════════════════════════════════════════════════════

_EQUIP      = re.compile(r"Equipment\s+Designation\s+(.+?)(?:\n|Owner)", re.IGNORECASE)
_VOLTAGE    = re.compile(r"Voltage\s+Class\s+(\d+(?:\.\d+)?)\s*KV", re.IGNORECASE)
_RATING     = re.compile(r"\bRating\s+(\d[\d,\.]*\s*KVA)", re.IGNORECASE)
_REPORT     = re.compile(r"Oil\s+Test\s+Report\s*[-–]\s*([\w/]+)", re.IGNORECASE)
_DATE       = re.compile(r"Report\s+Date\s+(\d{2}-\d{2}-\d{4})", re.IGNORECASE)
_POINT      = re.compile(r"Sampling\s+Point\s+(.+?)(?:\n)", re.IGNORECASE)
_FLUID      = re.compile(r"Insulating\s+Fluid\s+(.+?)(?:\n)", re.IGNORECASE)
_LIMIT_STD  = re.compile(r"(IEEE\s+C57\.\d+|IS\s+1866|IEC\s+60422)", re.IGNORECASE)
_OWNER      = re.compile(r"Owner\s+(.+?)(?:\n|Equipment)", re.IGNORECASE)
_LOCATION   = re.compile(r"(?:Location|Station|Substation|Site)\s*[:\-]?\s*(.+?)(?:\n)", re.IGNORECASE)

# Equipment TYPE — what kind of apparatus the oil sample was drawn from.
_EQUIP_TYPE = re.compile(
    r"\b(Power\s+Transformer|Distribution\s+Transformer|Auto\s*-?\s*Transformer|"
    r"Interconnecting\s+Transformer|Instrument\s+Transformer|"
    r"On[\s\-]?Load\s+Tap\s+Changer(?:\s*\(?OLTC\)?)?|OLTC|"
    r"Shunt\s+Reactor|Series\s+Reactor|Reactor|"
    r"Current\s+Transformer|Potential\s+Transformer|Voltage\s+Transformer|ICT)\b",
    re.IGNORECASE,
)


# ════════════════════════════════════════════════════════════════════════════
# PHYSICAL / CHEMICAL TESTS (BDV, IFT, DDF, Acidity, TDCG)
# ════════════════════════════════════════════════════════════════════════════

_BDV      = re.compile(r"Break\s*(?:down)?\s*Voltage.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Min", re.IGNORECASE | re.DOTALL)
_IFT      = re.compile(r"Interfacial\s+Tension.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Min", re.IGNORECASE | re.DOTALL)
_DDF      = re.compile(r"Dielectric\s+Dissipation\s+Factor.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_ACIDITY  = re.compile(r"Acidity.*?(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*Max", re.IGNORECASE | re.DOTALL)
_TDCG     = re.compile(r"Total\s+Dissolved\s+Combustible\s+Gas.*?(\d+(?:\.\d+)?)", re.IGNORECASE | re.DOTALL)


def _extract_extra(full_text: str) -> dict:
    """BDV, IFT, DDF, Acidity, TDCG, Owner, Location."""
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


# ════════════════════════════════════════════════════════════════════════════
# MASTER EXTRACTION — combines the 3 moisture extractors + metadata + gases
# ════════════════════════════════════════════════════════════════════════════

def _extract_from_pdf(pdf_path: str) -> dict:
    """
    Returns a dict describing one PDF report:
      ppm, limit, source_verdict, format, extraction_method,
      equipment, equipment_type, voltage_kv, rating,
      report_no, report_date, sampling_point,
      fluid_type, limit_std, owner, location,
      gases (dict), bdv/ift/ddf/acidity (dict|None), tdcg (float|None)
    """
    result = dict(
        ppm=None, limit=None, source_verdict=None,
        format="Not Detected", extraction_method="not_found",
        equipment="—", equipment_type="—", voltage_kv=None, rating="—",
        report_no="—", report_date="—", sampling_point="—",
        fluid_type="—", limit_std="—", owner="—", location="—",
        gases={}, bdv=None, ift=None, ddf=None, acidity=None, tdcg=None,
    )

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                txt = page.extract_text() or ""
                pages_text.append(txt)

                # ── Metadata (usually page 1) ─────────────────────────────────
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

                if result["equipment_type"] == "—":
                    m = _EQUIP_TYPE.search(txt)
                    if m:
                        result["equipment_type"] = re.sub(
                            r"\s+", " ", m.group(1).strip()
                        ).title().replace("Oltc", "OLTC").replace("Ict", "ICT")

            full_doc = "\n".join(pages_text)

            # ── Moisture: try Extractor 1 -> 2 -> 3, first hit wins ───────────
            wc = _extractor1_trufil(full_doc)
            if wc is None:
                wc = _extractor2_sgs_cpri(full_doc)
            if wc is None:
                wc = _extractor3_generic(pages_text)

            if wc is not None:
                result["ppm"]               = wc["ppm"]
                result["limit"]              = wc["limit"]
                result["source_verdict"]     = wc["verdict"]
                result["format"]             = wc["format"]
                result["extraction_method"]  = wc["method"]

            # Equipment type fallback — scan full doc once more if page-wise missed it
            if result["equipment_type"] == "—":
                m = _EQUIP_TYPE.search(full_doc)
                if m:
                    result["equipment_type"] = re.sub(
                        r"\s+", " ", m.group(1).strip()
                    ).title().replace("Oltc", "OLTC").replace("Ict", "ICT")

            # ── Gas & extra fields ─────────────────────────────────────────────
            result["gases"] = _extract_gases(full_doc)
            result.update(_extract_extra(full_doc))

    except Exception as exc:
        result["extraction_method"] = f"error:{exc}"

    return result


# ════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION — "as suited" limit selection
# ────────────────────────────────────────────────────────────────────────────
# Priority order for the applicable limit:
#   1. The limit STATED IN THE PDF itself (from Extractor 1 or 2) — always wins.
#   2. Ester / IEEE C57.147 fluids (declared in "Insulating Fluid" /
#      "Limit Standard" fields) -> 450 ppm in-service limit.
#   3. Format-based default:
#        TRU-FIL              -> 40 ppm  (IS 1866:2017 Table-5, <220 kV)
#        SGS / CPRI / Generic -> 45 ppm  (default lower limit for non-TRU-FIL
#                                          report layouts when no limit is stated)
# ════════════════════════════════════════════════════════════════════════════

_FORMAT_DEFAULT_LIMIT = {"TRU-FIL": 40.0, "SGS/CPRI": 45.0, "Generic": 45.0}


def classify(ppm, pdf_limit, fmt_tag, fluid_type, limit_std):
    """Returns dict: code, label, colour, reject (applicable limit), basis."""
    fluid_upper   = (fluid_type or "").upper()
    lim_std_upper = (limit_std or "").upper()
    is_ester = ("ESTER" in fluid_upper) or ("C57.147" in lim_std_upper)

    if ppm is None:
        return dict(
            code="ND", label="Not Detected — value not found in PDF",
            colour="#9e9e9e", reject=None,
            basis="No moisture value could be located using any of the 3 extractors.",
        )

    if pdf_limit is not None:
        reject = float(pdf_limit)
        basis  = f"PDF-stated limit ({fmt_tag} format row) — {reject:.0f} ppm Max"
    elif is_ester:
        reject = 450.0
        basis  = "IEEE C57.147 (Natural Ester fluid) — 450 ppm in-service limit"
    else:
        reject = _FORMAT_DEFAULT_LIMIT.get(fmt_tag, 45.0)
        if fmt_tag == "TRU-FIL":
            basis = f"IS 1866:2017 Table-5 default (<220 kV, TRU-FIL format) — {reject:.0f} ppm"
        else:
            basis = f"{fmt_tag} format default lower limit — {reject:.0f} ppm (no limit stated in report)"

    if ppm <= reject:
        return dict(
            code="ACCEPTABLE", label=f"Acceptable — \u2264 {reject:.0f} ppm limit",
            colour="#27ae60", reject=reject, basis=basis,
        )
    return dict(
        code="UNACCEPTABLE",
        label=f"Unacceptable — exceeds {reject:.0f} ppm limit. Corrective action required.",
        colour="#e74c3c", reject=reject, basis=basis,
    )


def _analysis_text(ppm, code, reject, fmt_tag) -> str:
    if ppm is None:
        return (
            "Water content by Karl Fischer method could <strong>not be located</strong> in this "
            "PDF by any of the three extraction strategies (TRU-FIL / SGS-CPRI / Generic). "
            "Verify the source document — the field may be absent, scanned as an image, "
            "or laid out in an unsupported format."
        )
    if code == "ACCEPTABLE":
        return (
            f"At <strong>{ppm:.1f} ppm</strong>, moisture is within the applicable limit "
            f"(\u2264 {reject:.0f} ppm, {fmt_tag} basis). No corrective action required. "
            f"Continue routine monitoring per IS 1866:2017 / IEC 60422 schedule."
        )
    return (
        f"At <strong>{ppm:.1f} ppm</strong>, moisture exceeds the applicable limit "
        f"({reject:.0f} ppm, {fmt_tag} basis). "
        f"<br><strong>Immediate actions:</strong> "
        f"(1) Hot-oil circulation, vacuum dehydration or Fuller's Earth filtration — "
        f"target well below {reject:.0f} ppm post-treatment. "
        f"(2) Inspect breather, gaskets and conservator seal for moisture ingress. "
        f"(3) Verify BDV and DDF before continued operation at rated load. "
        f"(4) Re-test after filtration; trend the result. If moisture persists after two "
        f"passes, assess paper-insulation moisture and consider oil replacement."
    )


# ════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG + STYLING
# ════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="Transformer Oil — Water Content Analyser", page_icon="💧", layout="wide")

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
.mc-nd::after    { background: #9e9e9e; }

/* ── Tier headers ── */
.tier-header {
    border-radius:12px; padding:.85rem 1.5rem;
    margin: 1.4rem 0 .7rem; font-size:1.05rem; font-weight:700;
    display:flex; align-items:center; gap:.6rem;
    box-shadow: 0 3px 12px rgba(0,0,0,0.12);
}
.tier-bad  { background:linear-gradient(90deg,#7f0000,#c0392b,#e74c3c); color:#fff; }
.tier-good { background:linear-gradient(90deg,#1b5e20,#27ae60,#2ecc71); color:#fff; }
.tier-nd   { background:linear-gradient(90deg,#424242,#757575,#9e9e9e); color:#fff; }
.tier-count { background:rgba(255,255,255,.22); border-radius:20px;
              padding:.1rem .65rem; font-size:.8rem; margin-left:auto; }

/* ── Expander card interior ── */
.verdict-strip {
    border-radius:10px; padding:.85rem 1.2rem; margin-bottom:.9rem;
    font-weight:700; display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;
    font-size:1rem; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.vs-bad  { background:linear-gradient(90deg,#fff5f5,#fff);
           border-left:5px solid #c0392b; color:#7f0000; }
.vs-good { background:linear-gradient(90deg,#f0fff8,#fff);
           border-left:5px solid #27ae60; color:#1b5e20; }
.vs-nd   { background:#f8f8f8; border-left:5px solid #bbb; color:#555; }
.vs-value { font-size:1.5rem; font-weight:800; letter-spacing:-.02em; }
.vs-meta  { font-size:.8rem; font-weight:500; opacity:.75; margin-top:.1rem; }

/* ── Format / equipment-type badges ── */
.fmt-badge {
    display:inline-block; border-radius:20px; padding:.25rem .8rem;
    font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em;
    background:#eef4ff; color:#1565c0; border:1px solid #c5cae9;
}
.type-badge {
    display:inline-block; border-radius:20px; padding:.25rem .8rem;
    font-size:.68rem; font-weight:700; letter-spacing:.04em;
    background:#fff3e0; color:#e65100; border:1px solid #ffe0b2; margin-left:.4rem;
}

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

/* ── Verdict / basis row ── */
.two-col { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:.4rem; }
.info-pill { background:#f0f4ff; border:1px solid #c5cae9; border-radius:8px;
             padding:.65rem .9rem; }
.ip-lbl { font-size:.6rem; font-weight:700; text-transform:uppercase;
          letter-spacing:.1em; color:#9fa8da; margin-bottom:.25rem; }
.ip-val { font-size:.9rem; font-weight:700; color:#1a237e; }
.ip-sub { font-size:.72rem; color:#888; margin-top:.15rem; }

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
.ref-lbl { color:#555; min-width:140px; font-weight:600; }

/* ── Misc ── */
[data-testid="stFileUploader"] { border-radius:12px !important; }
[data-testid="stProgressBar"] > div { background: linear-gradient(90deg,#1565c0,#4fc3f7) !important; }
#MainMenu,footer,header { visibility:hidden; }
.stExpander { border-radius:12px !important; border: 1px solid #e3eaf5 !important; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# HERO
# ════════════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="hero">
  <h1>💧 Transformer Oil — Water Content &amp; DGA Analyser</h1>
  <p>Karl Fischer Method &nbsp;·&nbsp; IS 1866:2017 / IEC 60422 &nbsp;·&nbsp; 3-Format Extraction &nbsp;·&nbsp; Fully Rule-Based (No AI API)</p>
  <div style="margin-top:.7rem;">
    <span class="hero-badge">TRU-FIL → 40 ppm</span>
    <span class="hero-badge">SGS / CPRI → 45 ppm</span>
    <span class="hero-badge">Generic → 45 ppm</span>
    <span class="hero-badge">"as suited" — PDF limit wins</span>
    <span class="hero-badge">IS 9434 DGA</span>
  </div>
</div>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# UPLOAD
# ════════════════════════════════════════════════════════════════════════════

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
      <div style="font-size:1.15rem;font-weight:700;color:#0d3b6e;">Drop TRU-FIL / SGS-CPRI / generic transformer oil report PDFs</div>
      <div style="font-size:.85rem;margin-top:.5rem;opacity:.7;max-width:520px;margin-left:auto;margin-right:auto;line-height:1.6;">
        Water content, DGA gases, BDV, IFT, DDF, acidity, equipment type and full metadata
        extracted automatically — three extraction strategies cover TRU-FIL, SGS/CPRI and
        generic report layouts.<br>
        TRU-FIL default limit: 40 ppm &nbsp;·&nbsp; Other formats default: 45 ppm &nbsp;·&nbsp;
        Any limit stated in the PDF itself always takes priority.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ════════════════════════════════════════════════════════════════════════════
# PROCESS PDFs (cached per file)
# ════════════════════════════════════════════════════════════════════════════

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
                ppm=None, limit=None, source_verdict=None,
                format="Not Detected", extraction_method=f"error:{exc}",
                equipment="—", equipment_type="—", voltage_kv=None, rating="—",
                report_no="—", report_date="—", sampling_point="—",
                fluid_type="—", limit_std="—", owner="—", location="—",
                gases={}, bdv=None, ift=None, ddf=None, acidity=None, tdcg=None,
            )
        os.unlink(tp)

        verdict_info = classify(
            info["ppm"], info["limit"], info["format"],
            info.get("fluid_type", "—"), info.get("limit_std", "—"),
        )
        cache[key] = dict(name=f.name, **info, **{f"v_{k}": v for k, v in verdict_info.items()})
    bar.progress((i + 1) / len(pdfs), text=f"Processing {i+1}/{len(pdfs)} — {f.name}")

bar.empty()


# ════════════════════════════════════════════════════════════════════════════
# SORT & SUMMARY COUNTS
# ════════════════════════════════════════════════════════════════════════════

_ORDER = {"UNACCEPTABLE": 0, "ACCEPTABLE": 1, "ND": 2}
rlist  = sorted(
    cache.values(),
    key=lambda r: (_ORDER.get(r["v_code"], 9), r["name"].lower()),
)

n_total = len(rlist)
n_ok    = sum(1 for r in rlist if r["v_code"] == "ACCEPTABLE")
n_bad   = sum(1 for r in rlist if r["v_code"] == "UNACCEPTABLE")
n_nd    = sum(1 for r in rlist if r["v_code"] == "ND")


# ════════════════════════════════════════════════════════════════════════════
# SUMMARY METRIC CARDS  (Total / Unacceptable / Acceptable / Not Detected)
# ════════════════════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="metric-row">
  <div class="metric-card mc-total">
    <div class="mc-num">{n_total}</div><div class="mc-lbl">📋 Total PDFs</div></div>
  <div class="metric-card mc-bad">
    <div class="mc-num">{n_bad}</div><div class="mc-lbl">❌ Unacceptable</div></div>
  <div class="metric-card mc-good">
    <div class="mc-num">{n_ok}</div><div class="mc-lbl">✅ Acceptable</div></div>
  <div class="metric-card mc-nd">
    <div class="mc-num">{n_nd}</div><div class="mc-lbl">⚪ Not Detected</div></div>
</div>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# PDF REPORT BUILDER — one transformer per page, full details, no AI content
# ════════════════════════════════════════════════════════════════════════════

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors as rl_colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
)


def _rl_safe(text):
    """Sanitise text for ReportLab/Helvetica (strip unicode that breaks the PDF font)."""
    if text is None:
        return "—"
    s = str(text)
    s = s.replace("—", "-").replace("–", "-")
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u00a0", " ").replace("•", "*").replace("°", " deg")
    s = s.replace("\u2264", "<=").replace("\u2265", ">=")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s


def _gas_status(value, limit):
    """Return (status_text, hex_colour) for a gas value vs its limit."""
    if limit is None:
        return "—", rl_colors.HexColor("#888888")
    if value >= limit:
        return "HIGH", rl_colors.HexColor("#c0392b")
    if value >= 0.7 * limit:
        return "WATCH", rl_colors.HexColor("#e67e22")
    return "OK", rl_colors.HexColor("#27ae60")


def _build_pdf_report(rlist_data):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
    )
    styles = getSampleStyleSheet()
    title_s  = ParagraphStyle("T",  parent=styles["Title"],
        fontSize=18, textColor=rl_colors.HexColor("#0d3b6e"), spaceAfter=4)
    h1_s     = ParagraphStyle("H1", parent=styles["Heading1"],
        fontSize=13, textColor=rl_colors.HexColor("#0d3b6e"), spaceBefore=12, spaceAfter=4)
    h2_s     = ParagraphStyle("H2", parent=styles["Heading2"],
        fontSize=10, textColor=rl_colors.HexColor("#444"), spaceBefore=8, spaceAfter=3)
    normal_s = ParagraphStyle("N",  parent=styles["Normal"], fontSize=9, leading=13)
    small_s  = ParagraphStyle("S",  parent=styles["Normal"],
        fontSize=8, textColor=rl_colors.HexColor("#666"), leading=11)

    USABLE_W = 17 * cm

    # ── Cover / summary page ───────────────────────────────────────────────
    story = []
    story.append(Paragraph("Transformer Oil Analysis", title_s))
    story.append(Paragraph("Water Content &amp; DGA Diagnostic Report", h1_s))
    story.append(Paragraph(
        f"Generated: {datetime.datetime.now().strftime('%d-%m-%Y %H:%M')}  |  "
        "Method: Karl Fischer (IS 13567 / ASTM D1533 / IEC 60814)  |  "
        "Limits: TRU-FIL 40 ppm default, SGS/CPRI &amp; Generic 45 ppm default, "
        "PDF-stated limit always takes priority  |  No AI / external API used.",
        small_s))
    story.append(HRFlowable(width="100%", thickness=1.5,
                             color=rl_colors.HexColor("#0d3b6e"), spaceAfter=12))

    s_data = [
        ["Total Reports", "Unacceptable", "Acceptable", "Not Detected"],
        [str(len(rlist_data)),
         str(sum(1 for r in rlist_data if r["v_code"] == "UNACCEPTABLE")),
         str(sum(1 for r in rlist_data if r["v_code"] == "ACCEPTABLE")),
         str(sum(1 for r in rlist_data if r["v_code"] == "ND"))],
    ]
    s_tbl = Table(s_data, colWidths=[USABLE_W/4]*4)
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
        ("TEXTCOLOR",     (1,1),(1,1), rl_colors.HexColor("#c0392b")),
        ("TEXTCOLOR",     (2,1),(2,1), rl_colors.HexColor("#27ae60")),
        ("TEXTCOLOR",     (3,1),(3,1), rl_colors.HexColor("#757575")),
        ("FONTNAME",      (0,1),(-1,1), "Helvetica-Bold"),
        ("FONTSIZE",      (0,1),(-1,1), 14),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 10))

    # Index table — quick reference to which page each transformer is on
    idx_rows = [["#", "Equipment", "Format", "Water (ppm)", "Verdict"]]
    for i, r in enumerate(rlist_data, start=1):
        ppm_s = f"{r['ppm']:.1f}" if r["ppm"] is not None else "N/D"
        idx_rows.append([
            str(i), _rl_safe(r["equipment"]), _rl_safe(r["format"]),
            ppm_s, _rl_safe(r["v_code"]),
        ])
    idx_tbl = Table(idx_rows, colWidths=[1*cm, 7*cm, 3*cm, 2.8*cm, 3.2*cm])
    idx_style = [
        ("BACKGROUND",    (0,0),(-1,0), rl_colors.HexColor("#1565c0")),
        ("TEXTCOLOR",     (0,0),(-1,0), rl_colors.white),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 8),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [rl_colors.HexColor("#f7f9ff"), rl_colors.white]),
        ("GRID",          (0,0),(-1,-1), 0.4, rl_colors.HexColor("#dddddd")),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
    ]
    for i, r in enumerate(rlist_data, start=1):
        col = (rl_colors.HexColor("#c0392b") if r["v_code"] == "UNACCEPTABLE"
               else rl_colors.HexColor("#27ae60") if r["v_code"] == "ACCEPTABLE"
               else rl_colors.HexColor("#757575"))
        idx_style.append(("TEXTCOLOR", (4, i), (4, i), col))
        idx_style.append(("FONTNAME", (4, i), (4, i), "Helvetica-Bold"))
    idx_tbl.setStyle(TableStyle(idx_style))
    story.append(Paragraph("Report Index", h2_s))
    story.append(idx_tbl)
    story.append(PageBreak())

    # ── One page per transformer ───────────────────────────────────────────
    for r_i, r in enumerate(rlist_data):
        ppm     = r["ppm"]
        v_code  = r["v_code"]
        equip   = _rl_safe(r["equipment"])
        kv      = r["voltage_kv"]
        kv_str  = f"{kv:.0f} kV" if kv else "—"
        ppm_s   = f"{ppm:.1f} ppm" if ppm is not None else "Not Detected"
        scolor  = (rl_colors.HexColor("#c0392b") if v_code == "UNACCEPTABLE"
                   else rl_colors.HexColor("#27ae60") if v_code == "ACCEPTABLE"
                   else rl_colors.HexColor("#757575"))
        scolor_hex = ("c0392b" if v_code == "UNACCEPTABLE"
                       else "27ae60" if v_code == "ACCEPTABLE" else "757575")
        icon = (f"[{v_code}]")

        story.append(Paragraph(f"{r_i+1}. {equip}", title_s))
        story.append(Paragraph(
            f"<font color='#{scolor_hex}'><b>{icon}</b></font> &nbsp; "
            f"{_rl_safe(r['sampling_point'])} &nbsp;|&nbsp; {_rl_safe(r['name'])}",
            small_s))
        story.append(HRFlowable(width="100%", thickness=1.5,
                                 color=scolor, spaceAfter=8))

        # Verdict banner
        v_tbl = Table([[Paragraph(
            f"<font color='#{scolor_hex}'><b>{icon} {_rl_safe(r['v_label'])}</b></font>",
            normal_s,
        )]], colWidths=[USABLE_W])
        bg = (rl_colors.HexColor("#fff0f0") if v_code == "UNACCEPTABLE"
              else rl_colors.HexColor("#f0fff4") if v_code == "ACCEPTABLE"
              else rl_colors.HexColor("#f5f5f5"))
        v_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), bg),
            ("LINEABOVE",     (0,0),(-1,0),  2, scolor),
            ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ("TOPPADDING",    (0,0),(-1,-1), 7),
            ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ]))
        story.append(v_tbl)
        story.append(Spacer(1, 6))

        # Water content table
        reject = r["v_reject"]
        reject_s = f"{reject:.0f} ppm Max" if reject is not None else "—"
        w_data = [
            ["Parameter", "Value", "Basis / Notes"],
            ["Water Content (Karl Fischer)", ppm_s, f"Extractor: {_rl_safe(r['extraction_method'])}"],
            ["Detected Format", _rl_safe(r["format"]), "TRU-FIL / SGS-CPRI / Generic"],
            ["Applicable Limit", reject_s, _rl_safe(r["v_basis"])],
            ["Source (Vendor) Verdict", _rl_safe(r["source_verdict"] or "—"), "As stated in PDF (if present)"],
        ]
        w_tbl = Table(w_data, colWidths=[5*cm, 4*cm, 8*cm])
        w_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  rl_colors.HexColor("#1565c0")),
            ("TEXTCOLOR",     (0,0),(-1,0),  rl_colors.white),
            ("FONTNAME",      (0,0),(-1,0),  "Helvetica-Bold"),
            ("FONTNAME",      (0,1),(0,-1),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8.5),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [rl_colors.HexColor("#eef4ff"), rl_colors.white]),
            ("TEXTCOLOR",     (1,1),(1,1),   scolor),
            ("FONTNAME",      (1,1),(1,1),   "Helvetica-Bold"),
            ("GRID",          (0,0),(-1,-1), 0.5, rl_colors.HexColor("#cccccc")),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ]))
        story.append(w_tbl)

        if v_code == "UNACCEPTABLE":
            story.append(Paragraph("Immediate Actions Required", h2_s))
            story.append(Paragraph(
                f"(1) Hot-oil circulation / vacuum dehydration / Fuller's Earth filtration "
                f"&mdash; target well below {reject:.0f} ppm post-treatment. "
                f"(2) Inspect breather, gaskets and conservator seal for moisture ingress. "
                f"(3) Verify BDV and DDF before continued operation at rated load. "
                f"(4) Re-test after filtration and trend the result; if moisture persists "
                f"after two passes, assess paper-insulation moisture and consider oil replacement.",
                normal_s))

        # DGA gas table
        gases = r.get("gases", {})
        if gases:
            story.append(Paragraph("Dissolved Gas Analysis (DGA)", h2_s))
            g_rows = [["Gas", "Value (ppm v/v)", "Reference Limit", "Status"]]
            ordered = [g for g in _GAS_ORDER if g in gases] + \
                      [g for g in gases if g not in _GAS_ORDER]
            g_style = [
                ("BACKGROUND",    (0,0),(-1,0), rl_colors.HexColor("#0d3b6e")),
                ("TEXTCOLOR",     (0,0),(-1,0), rl_colors.white),
                ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
                ("FONTSIZE",      (0,0),(-1,-1), 8),
                ("ALIGN",         (1,0),(-1,-1), "CENTER"),
                ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
                ("ROWBACKGROUNDS",(0,1),(-1,-1), [rl_colors.HexColor("#f7f9ff"), rl_colors.white]),
                ("GRID",          (0,0),(-1,-1), 0.4, rl_colors.HexColor("#dddddd")),
                ("TOPPADDING",    (0,0),(-1,-1), 4),
                ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ]
            for gi, g in enumerate(ordered, start=1):
                val   = gases[g]["value"]
                limit = gases[g].get("limit") or _GAS_LIMITS.get(g)
                status_txt, status_col = _gas_status(val, limit)
                g_rows.append([
                    f"{_GAS_LABELS.get(g, g)} ({g})",
                    f"{val:.2f}",
                    f"{limit:.0f}" if limit else "—",
                    status_txt,
                ])
                g_style.append(("TEXTCOLOR", (3, gi), (3, gi), status_col))
                g_style.append(("FONTNAME", (3, gi), (3, gi), "Helvetica-Bold"))
            g_tbl = Table(g_rows, colWidths=[6*cm, 4*cm, 4*cm, 3*cm])
            g_tbl.setStyle(TableStyle(g_style))
            story.append(g_tbl)

            tdcg = r.get("tdcg")
            if tdcg:
                tdcg_hex = "c0392b" if tdcg > 720 else ("e67e22" if tdcg > 314 else "27ae60")
                story.append(Paragraph(
                    f"<font color='#{tdcg_hex}'><b>TDCG: {tdcg:.0f} ppm</b></font> "
                    f"&nbsp;(IEEE C57.104 - Condition 1 &lt;314, Condition 2 &lt;720, Condition 3 &lt;1920)",
                    small_s))
        else:
            story.append(Paragraph("Dissolved Gas Analysis (DGA)", h2_s))
            story.append(Paragraph("No DGA gas values were detected in this PDF.", small_s))

        # Physical / chemical tests table
        bdv, ift, ddf, acidity = r.get("bdv"), r.get("ift"), r.get("ddf"), r.get("acidity")
        if any(x is not None for x in [bdv, ift, ddf, acidity]):
            story.append(Paragraph("Physical &amp; Chemical Tests", h2_s))

            def _pc_row(label, data, unit, lower_better):
                if data is None:
                    return [label, "—", "—", "—"]
                v, l = data["value"], data.get("limit")
                ok = (v <= l) if (l is not None and lower_better) else \
                     (v >= l) if (l is not None) else None
                status = "—" if ok is None else ("OK" if ok else "OUT OF RANGE")
                return [label, f"{v:.2f} {unit}", f"{l:.2f} {unit}" if l is not None else "—", status]

            pc_rows = [["Test", "Value", "Limit", "Status"]]
            pc_rows.append(_pc_row("Breakdown Voltage (BDV)", bdv, "kV", False))
            pc_rows.append(_pc_row("Interfacial Tension (IFT)", ift, "mN/m", False))
            pc_rows.append(_pc_row("Dielectric Dissipation Factor (DDF, 90C)", ddf, "%", True))
            pc_rows.append(_pc_row("Acidity", acidity, "mg KOH/g", True))
            pc_tbl = Table(pc_rows, colWidths=[7*cm, 3.5*cm, 3.5*cm, 3*cm])
            pc_style = [
                ("BACKGROUND",    (0,0),(-1,0), rl_colors.HexColor("#0d3b6e")),
                ("TEXTCOLOR",     (0,0),(-1,0), rl_colors.white),
                ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
                ("FONTSIZE",      (0,0),(-1,-1), 8),
                ("ALIGN",         (1,0),(-1,-1), "CENTER"),
                ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
                ("ROWBACKGROUNDS",(0,1),(-1,-1), [rl_colors.HexColor("#f7f9ff"), rl_colors.white]),
                ("GRID",          (0,0),(-1,-1), 0.4, rl_colors.HexColor("#dddddd")),
                ("TOPPADDING",    (0,0),(-1,-1), 4),
                ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ]
            for ri, row in enumerate(pc_rows[1:], start=1):
                if row[3] == "OUT OF RANGE":
                    pc_style.append(("TEXTCOLOR", (3, ri), (3, ri), rl_colors.HexColor("#c0392b")))
                    pc_style.append(("FONTNAME", (3, ri), (3, ri), "Helvetica-Bold"))
                elif row[3] == "OK":
                    pc_style.append(("TEXTCOLOR", (3, ri), (3, ri), rl_colors.HexColor("#27ae60")))
            pc_tbl.setStyle(TableStyle(pc_style))
            story.append(pc_tbl)

        # Equipment & Report Details
        story.append(Paragraph("Equipment &amp; Report Details", h2_s))
        eq_data = [
            ["Equipment",     equip,                              "Equipment Type", _rl_safe(r["equipment_type"])],
            ["Voltage Class", kv_str,                             "Rating",         _rl_safe(r["rating"])],
            ["Report No.",    _rl_safe(r["report_no"]),           "Report Date",    _rl_safe(r["report_date"])],
            ["Sampling Pt.",  _rl_safe(r["sampling_point"]),      "Insul. Fluid",   _rl_safe(r.get("fluid_type","—"))],
            ["Limit Standard",_rl_safe(r.get("limit_std","—")),   "Owner",          _rl_safe(r.get("owner","—"))],
            ["Location",      _rl_safe(r.get("location","—")),    "Source File",    _rl_safe(r["name"])],
        ]
        eq_t = Table(eq_data, colWidths=[3*cm, 5.5*cm, 3*cm, 5.5*cm])
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

        if r_i != len(rlist_data) - 1:
            story.append(PageBreak())

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ════════════════════════════════════════════════════════════════════════════
# PDF EXPORT BUTTON
# ════════════════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════════════════
# IN-APP CARD RENDER HELPERS
# ════════════════════════════════════════════════════════════════════════════

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
        return (f"<div class='ecell'><div class='ecell-lbl'>{label}</div>"
                f"<div class='ecell-val' style='color:#ccc;'>—</div></div>")
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


# ════════════════════════════════════════════════════════════════════════════
# GROUPED REPORT CARDS
# ════════════════════════════════════════════════════════════════════════════

TIERS = [
    ("UNACCEPTABLE", "Unacceptable — Exceeds Applicable Limit", "bad"),
    ("ACCEPTABLE",   "Acceptable — Within Applicable Limit",    "good"),
    ("ND",           "Not Detected — Moisture Value Not Found", "nd"),
]

for tier, label_str, css in TIERS:
    group = [r for r in rlist if r["v_code"] == tier]
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
        v_code   = r["v_code"]
        kv       = r["voltage_kv"]
        ppm_disp = f"{ppm:.1f}" if ppm is not None else "N/D"
        kv_s     = f"{kv:.0f} kV" if kv else "—"
        equip    = r["equipment"]
        reject   = r["v_reject"]
        pct      = min(ppm / reject * 100, 100) if (ppm is not None and reject) else 0
        vc_css   = "bad" if v_code == "UNACCEPTABLE" else ("good" if v_code == "ACCEPTABLE" else "nd")
        gases    = r.get("gases", {})

        title = f"{_ICONS[v_code]}  {equip}   ·   {ppm_disp} ppm   ·   {kv_s}   ·   {r['name']}"

        with st.expander(title, expanded=(v_code == "UNACCEPTABLE")):

            # ── Verdict strip ───────────────────────────────────────────────
            src_v = r.get("source_verdict")
            src_color = ("27ae60" if src_v == "Acceptable" else
                          "c0392b" if src_v == "Not Acceptable" else "888888")
            src_html = (
                f"<div style='font-size:.75rem;opacity:.7;margin-top:.1rem;'>Source verdict: "
                f"<span style='color:#{src_color};font-weight:700;'>{src_v}</span></div>"
                if src_v else ""
            )
            reject_str = f"{reject:.0f} ppm" if reject is not None else "—"
            st.markdown(
                f"<div class='verdict-strip vs-{vc_css}'>"
                f"<div>"
                f"<div class='vs-value'>{ppm_disp} <span style='font-size:.9rem;font-weight:500;opacity:.7;'>ppm H₂O</span></div>"
                f"<div class='vs-meta'>Limit {reject_str} &nbsp;·&nbsp; {kv_s} &nbsp;·&nbsp; {r.get('fluid_type','Mineral Oil')}</div>"
                f"<div style='margin-top:.4rem;'>"
                f"<span class='fmt-badge'>{r['format']}</span>"
                f"<span class='type-badge'>{r['equipment_type']}</span>"
                f"</div>"
                f"</div>"
                f"<div style='margin-left:auto;text-align:right;'>"
                f"<div style='font-size:1.1rem;font-weight:800;'>{_ICONS[v_code]} {v_code}</div>"
                f"{src_html}"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # ── Water content bar ────────────────────────────────────────────
            if ppm is not None and reject is not None:
                bar_grad = (f"linear-gradient(90deg, {'#c0392b,#e74c3c' if v_code=='UNACCEPTABLE' else '#27ae60,#2ecc71'})")
                st.markdown(
                    f"<div class='wbar-wrap'>"
                    f"<div class='wbar-track'>"
                    f"<div class='wbar-fill' style='width:{pct:.1f}%;background:{bar_grad};'></div>"
                    f"</div>"
                    f"<div class='wbar-label'>"
                    f"<span>0 ppm</span>"
                    f"<span><strong>{pct:.1f}%</strong> of {reject:.0f} ppm limit</span>"
                    f"<span>{reject:.0f} ppm</span>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # ── Analysis text ───────────────────────────────────────────────
            st.markdown(
                f"<div class='analysis-box ab-{vc_css}'>"
                f"{_analysis_text(ppm, v_code, reject, r['format'])}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # ── DGA Gas bars ─────────────────────────────────────────────────
            if gases:
                st.markdown("<div class='slabel'>🧪 Dissolved Gas Analysis (DGA)</div>",
                            unsafe_allow_html=True)
                ordered = [g for g in _GAS_ORDER if g in gases] + \
                          [g for g in gases if g not in _GAS_ORDER]
                cards_html = "".join(
                    _gas_card_html(g, gases[g], _GAS_LIMITS.get(g))
                    for g in ordered
                )
                st.markdown(f"<div class='gas-grid'>{cards_html}</div>", unsafe_allow_html=True)

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
                        f"(IEEE C57.104 — Condition1&lt;314 · Condition2&lt;720 · Condition3&lt;1920)</span>"
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
            bdv     = r.get("bdv")
            ift     = r.get("ift")
            ddf     = r.get("ddf")
            acidity = r.get("acidity")
            if any(x is not None for x in [bdv, ift, ddf, acidity]):
                st.markdown("<div class='slabel'>⚗️ Physical &amp; Chemical Tests</div>",
                            unsafe_allow_html=True)
                bdv_v = bdv["value"] if bdv else None
                bdv_l = bdv["limit"] if bdv else None
                ift_v = ift["value"] if ift else None
                ift_l = ift["limit"] if ift else None
                ddf_v = ddf["value"] if ddf else None
                ddf_l = ddf["limit"] if ddf else None
                ac_v  = acidity["value"] if acidity else None
                ac_l  = acidity["limit"] if acidity else None
                cells = (
                    _extra_cell("BDV", bdv_v, "kV", bdv_l, lower_better=False) +
                    _extra_cell("IFT", ift_v, "mN/m", ift_l, lower_better=False) +
                    _extra_cell("DDF (90°C)", ddf_v, "%", ddf_l, lower_better=True) +
                    _extra_cell("Acidity", ac_v, "mg KOH/g", ac_l, lower_better=True)
                )
                st.markdown(f"<div class='extra-grid'>{cells}</div>", unsafe_allow_html=True)

            st.markdown("<div style='margin:.4rem 0;'></div>", unsafe_allow_html=True)

            # ── Verdict basis ──────────────────────────────────────────────────
            st.markdown("<div class='slabel'>📋 Verdict &amp; Assessment Basis</div>",
                        unsafe_allow_html=True)
            st.markdown(
                f"<div class='two-col'>"
                f"<div class='info-pill'>"
                f"<div class='ip-lbl'>Applicable Limit</div>"
                f"<div class='ip-val' style='color:#{('c0392b' if v_code=='UNACCEPTABLE' else '27ae60' if v_code=='ACCEPTABLE' else '888888')};'>{reject_str}</div>"
                f"<div class='ip-sub'>Detected format: {r['format']} &nbsp;·&nbsp; Extraction: {r['extraction_method']}</div>"
                f"</div>"
                f"<div class='info-pill'>"
                f"<div class='ip-lbl'>Assessment Basis</div>"
                f"<div class='ip-val' style='font-size:.82rem;color:#374151;'>{r['v_basis']}</div>"
                f"</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            st.divider()

            # ── Equipment & Report metadata grid ────────────────────────────
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
                f"<div class='mcell'><div class='mcell-lbl'>Equipment Type</div>"
                f"<div class='mcell-val'>{r['equipment_type']}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Insulating Fluid</div>"
                f"<div class='mcell-val'>{fluid}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Limit Standard</div>"
                f"<div class='mcell-val'>{lim_s}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Owner</div>"
                f"<div class='mcell-val'>{owner}</div></div>"
                f"<div class='mcell'><div class='mcell-lbl'>Location / Station</div>"
                f"<div class='mcell-val'>{location}</div></div>"
                f"<div class='mcell' style='grid-column:span 2'><div class='mcell-lbl'>Sampling Point</div>"
                f"<div class='mcell-val'>{r['sampling_point']}</div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )


# ════════════════════════════════════════════════════════════════════════════
# THRESHOLD REFERENCE
# ════════════════════════════════════════════════════════════════════════════

st.markdown("""
<div class="ref-box">
  <div class="ref-title">📐 Threshold Reference — "As Suited" Limit Logic</div>
  <div class="ref-row">
    <span class="ref-lbl">PDF states its own limit</span>
    <span style="color:#1565c0;font-weight:700">→ that limit is used</span>
    <span style="color:#666"> — highest priority, regardless of format</span>
  </div>
  <div class="ref-row">
    <span class="ref-lbl">TRU-FIL format, no stated limit</span>
    <span style="color:#0d3b6e;font-weight:700">→ 40 ppm (IS 1866:2017, &lt;220 kV)</span>
  </div>
  <div class="ref-row">
    <span class="ref-lbl">SGS/CPRI or Generic, no stated limit</span>
    <span style="color:#0d3b6e;font-weight:700">→ 45 ppm default</span>
  </div>
  <div class="ref-row">
    <span class="ref-lbl">Natural ester / IEEE C57.147 fluid</span>
    <span style="color:#0d3b6e;font-weight:700">→ 450 ppm in-service limit</span>
  </div>
  <div class="ref-row">
    <span class="ref-lbl">Result ≤ applicable limit</span>
    <span style="color:#27ae60;font-weight:700">✅ Acceptable</span>
    <span style="color:#666"> — no action required</span>
  </div>
  <div class="ref-row">
    <span class="ref-lbl">Result &gt; applicable limit</span>
    <span style="color:#c0392b;font-weight:700">❌ Unacceptable</span>
    <span style="color:#666"> — immediate filtration / oil replacement</span>
  </div>
  <div style="margin-top:.9rem;padding-top:.7rem;border-top:1px solid #bbdefb;">
    <div class="ref-title" style="font-size:.8rem;margin-bottom:.4rem;">DGA Reference Limits (IEEE C57.104 / IS 9434)</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:6px;font-size:.75rem;color:#444;">
      <span>H₂ &lt; 100 ppm</span><span>CH₄ &lt; 120 ppm</span>
      <span>C₂H₂ &lt; 35 ppm</span><span>C₂H₄ &lt; 50 ppm</span>
      <span>C₂H₆ &lt; 65 ppm</span><span>CO &lt; 350 ppm</span>
      <span>CO₂ &lt; 2500 ppm</span><span>TDCG Condition1 &lt; 314 ppm</span>
    </div>
  </div>
  <div style="margin-top:.7rem;color:#888;font-size:.72rem;">
    Test method: Karl Fischer titration per IS 13567 / ASTM D1533 / IEC 60814. Units: mg/kg (ppm by weight).
    Extraction: 3 independent strategies (TRU-FIL / SGS-CPRI / Generic). All analysis is rule-based —
    no AI model or external API is used.
  </div>
</div>
""", unsafe_allow_html=True)
