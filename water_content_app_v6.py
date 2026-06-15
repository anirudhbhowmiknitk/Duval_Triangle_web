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



# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Water Content Analyser", page_icon="💧", layout="wide")

st.markdown("""
<style>
html, body, [class*="css"] { font-family: 'Segoe UI', Arial, sans-serif; }
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

.hero-banner {
    background: linear-gradient(135deg, #0a2342 0%, #0d3b6e 55%, #1565c0 100%);
    border-radius: 14px; padding: 1.8rem 2.2rem 1.5rem;
    margin-bottom: 1.5rem; box-shadow: 0 6px 24px rgba(0,0,0,0.3);
}
.hero-banner h1 { color: #e3f2fd; font-size: 1.9rem; font-weight: 700; margin: 0 0 0.3rem; }
.hero-banner p  { color: #90caf9; font-size: 0.9rem; margin: 0; }

.metric-row { display:flex; gap:14px; margin-bottom:1.5rem; flex-wrap:wrap; }
.metric-card { flex:1 1 120px; border-radius:12px; padding:1rem 1.1rem;
               text-align:center; box-shadow:0 2px 10px rgba(0,0,0,0.1); }
.metric-card .mc-num { font-size:2.2rem; font-weight:700; line-height:1.1; }
.metric-card .mc-lbl { font-size:0.76rem; font-weight:600; text-transform:uppercase;
                        letter-spacing:.07em; opacity:.75; margin-top:4px; }
.mc-total { background:#e8eaf6; color:#1a237e; }
.mc-bad   { background:#ffebee; color:#b71c1c; }
.mc-good  { background:#e8f5e9; color:#1b5e20; }
.mc-nd    { background:#f5f5f5; color:#555;    }

.sev-header { border-radius:10px; padding:.75rem 1.4rem;
              margin:1.2rem 0 .6rem; font-size:1.1rem; font-weight:700; }
.sev-bad  { background:linear-gradient(90deg,#c0392b,#e74c3c); color:#fff; }
.sev-good { background:linear-gradient(90deg,#27ae60,#2ecc71); color:#fff; }

.verdict-banner { border-radius:8px; padding:.7rem 1.1rem; margin-bottom:.8rem;
                  font-size:1rem; font-weight:700; display:flex; align-items:center; gap:.5rem; }
.verdict-bad  { background:#fff0f0; border:2px solid #c0392b; color:#c0392b; }
.verdict-good { background:#f0fff4; border:2px solid #27ae60; color:#27ae60; }
.verdict-nd   { background:#f5f5f5; border:2px solid #aaa;    color:#666;    }

.section-label { font-size:.72rem; font-weight:700; text-transform:uppercase;
                 letter-spacing:.1em; color:#888; margin:1.1rem 0 .4rem;
                 padding-bottom:.3rem; border-bottom:1px solid #e8e8e8; }

.analysis-box { border-radius:8px; padding:.85rem 1.1rem; margin-bottom:.6rem;
                font-size:.9rem; line-height:1.6; }
.analysis-bad  { background:#fff5f5; border-left:4px solid #c0392b; color:#4a0000; }
.analysis-good { background:#f0fff4; border-left:4px solid #27ae60; color:#1a4a2a; }
.analysis-nd   { background:#f8f8f8; border-left:4px solid #aaa;    color:#555;    }

.meta-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:.6rem; }
.mcell { background:#f8faff; border:1px solid #dde3f0; border-radius:6px; padding:.6rem .8rem; }
.mcell-lbl { font-size:.58rem; text-transform:uppercase; letter-spacing:.12em;
             color:#888; margin-bottom:3px; font-weight:600; }
.mcell-val { font-size:.82rem; font-weight:600; color:#1a237e; word-break:break-word; }

.pct-bar-wrap { background:#e0e0e0; border-radius:50px; height:14px;
                width:100%; margin:10px 0 4px; overflow:hidden; }
.pct-bar-fill { height:100%; border-radius:50px; }

.ref-box { background:#e3f2fd; border:1px solid #90caf9; border-left:4px solid #1565c0;
           border-radius:8px; padding:1rem 1.4rem; margin-top:1.5rem; }
.ref-title { font-size:1rem; font-weight:700; color:#0d3b6e;
             text-transform:uppercase; letter-spacing:.06em; margin-bottom:.6rem; }
.ref-row { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:.25rem;
           font-size:.8rem; }
.ref-lbl { color:#555; min-width:160px; font-weight:600; }

[data-testid="stFileUploader"] { border-radius:10px !important; }
[data-testid="stProgressBar"] > div { background:#1565c0 !important; }
#MainMenu,footer,header { visibility:hidden; }
</style>
""", unsafe_allow_html=True)

# ─── Hero ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero-banner">
  <h1>💧 Transformer Oil — Water Content Analyser</h1>
  <p>
    Karl Fischer Method (IS 13567 / ASTM D1533 / IEC 60814) &nbsp;·&nbsp;
    IS 1866 : 2017 / IEC 60422 &nbsp;·&nbsp; TRU-FIL Report Format &nbsp;·&nbsp;
    Limit: <strong>40 ppm max</strong>
  </p>
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
    <div style="border:2px dashed #90caf9;border-radius:12px;padding:2.5rem;
                text-align:center;color:#5c85b5;margin-top:.8rem;background:#f0f7ff;">
      <div style="font-size:3rem;margin-bottom:.6rem;">💧</div>
      <div style="font-size:1.1rem;font-weight:600;">Drop TRU-FIL transformer oil report PDFs here</div>
      <div style="font-size:.88rem;margin-top:.4rem;opacity:.7;">
        Water content extracted automatically · IS 1866:2017 · 40 ppm limit · Multi-PDF batch
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
           f"<div class='mc-lbl'>Not Detected</div></div>") if n_nd else ""

st.markdown(f"""
<div class="metric-row">
  <div class="metric-card mc-total">
    <div class="mc-num">{n_total}</div><div class="mc-lbl">Total PDFs</div></div>
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

    # Summary table
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

    # Per-report sections
    for r in rlist_data:
        ppm    = r["ppm"]
        v_code = r["verdict"]
        equip  = r["equipment"]
        kv     = r["voltage_kv"]
        kv_str = f"{kv:.0f} kV" if kv else "—"
        ppm_s  = f"{ppm:.1f} ppm" if ppm is not None else "N/D"
        scolor = (rl_colors.HexColor("#c0392b") if v_code == "UNACCEPTABLE"
                  else rl_colors.HexColor("#27ae60"))
        icon   = "[UNACCEPTABLE]" if v_code == "UNACCEPTABLE" else "[ACCEPTABLE]"

        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=rl_colors.HexColor("#cccccc"), spaceBefore=6))
        story.append(Paragraph(
            f"<font color='{'c0392b' if v_code=='UNACCEPTABLE' else '27ae60'}'>"
            f"{icon} {equip}</font>"
            f"  —  {r['sampling_point']}",
            h1_s))

        w_data = [
            ["Parameter",      "Value",   "Standard / Basis"],
            ["Water Content",  ppm_s,     r.get("basis","IS 1866:2017 Table-5")],
            ["Limit",          f"{r['reject']:.0f} ppm max", "IS 1866:2017 Table-5"],
            ["Voltage Class",  kv_str,    "From PDF"],
            ["TRU-FIL Verdict",r["trufil_verdict"], "As stated in PDF"],
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

        # Verdict box
        v_tbl = Table([[Paragraph(
            f"<font color='{'c0392b' if v_code=='UNACCEPTABLE' else '27ae60'}'>"
            f"<b>{icon} {v_code}</b></font>"
            f"  —  Water: <b>{ppm_s}</b>  |  Limit: 40 ppm  |  {kv_str}",
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

        # Equipment info
        story.append(Paragraph("Equipment Details", h2_s))
        eq_data = [
            ["Equipment",    equip,                 "Report No.",   r["report_no"]],
            ["Voltage Class",kv_str,                "Report Date",  r["report_date"]],
            ["Rating",       r["rating"],           "Sampling Pt.", r["sampling_point"]],
            ["Insul. Fluid", r.get("fluid_type","—"), "Extraction", r.get("extraction_method","—")],
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


# ─── Grouped report cards ─────────────────────────────────────────────────────
_ICONS = {"ACCEPTABLE": "🟢", "UNACCEPTABLE": "🔴", "ND": "⚪"}

TIERS = [
    ("UNACCEPTABLE", "UNACCEPTABLE — Exceeds 40 ppm limit", "bad"),
    ("ACCEPTABLE",   "ACCEPTABLE — Within safe range (≤ 40 ppm)", "good"),
]

for tier, label_str, css in TIERS:
    group = [r for r in rlist if r["verdict"] == tier]
    if not group:
        continue
    st.markdown(
        f"<div class='sev-header sev-{css}'>"
        f"{_ICONS[tier]}  {label_str}  ({len(group)})"
        f"</div>",
        unsafe_allow_html=True,
    )

    for r in group:
        ppm      = r["ppm"]
        v_code   = r["verdict"]
        kv       = r["voltage_kv"]
        ppm_disp = f"{ppm:.1f} ppm" if ppm is not None else "N/D"
        kv_s     = f"{kv:.0f} kV" if kv else "—"
        equip    = r["equipment"]
        pct      = min(ppm / r["reject"] * 100, 100) if (ppm and r["reject"]) else 0
        bar_col  = "#c0392b" if v_code == "UNACCEPTABLE" else "#27ae60"
        vc_css   = "bad" if v_code == "UNACCEPTABLE" else "good"

        title = f"{_ICONS[v_code]}  {equip}   ·   {ppm_disp}   ·   {r['name']}"

        with st.expander(title, expanded=(v_code == "UNACCEPTABLE")):

            # Verdict banner
            st.markdown(
                f"<div class='verdict-banner verdict-{vc_css}'>"
                f"{_ICONS[v_code]} <strong>{v_code}</strong>"
                f"&nbsp;—&nbsp; Water Content: <strong>{ppm_disp}</strong>"
                f"&nbsp;|&nbsp; Limit: {r['reject']:.0f} ppm"
                f"&nbsp;|&nbsp; {kv_s}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # % of limit bar
            if ppm is not None:
                st.markdown(
                    f"<div class='pct-bar-wrap'>"
                    f"<div class='pct-bar-fill' style='width:{pct:.1f}%;background:{bar_col};'></div>"
                    f"</div>"
                    f"<div style='font-size:.78rem;color:#888;margin-bottom:.6rem;'>"
                    f"{pct:.1f}% of {r['reject']:.0f} ppm limit</div>",
                    unsafe_allow_html=True,
                )

            # Analysis text
            st.markdown(
                f"<div class='analysis-box analysis-{vc_css}'>"
                f"{_analysis_text(ppm, v_code, kv, r['caution'], r['reject'])}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # TRU-FIL verdict + basis
            c1, c2 = st.columns(2)
            with c1:
                tf_color = "#27ae60" if r["trufil_verdict"] == "Acceptable" else (
                           "#c0392b" if r["trufil_verdict"] == "Not Acceptable" else "#888")
                st.markdown(
                    f"<div class='section-label'>TRU-FIL Verdict (from PDF)</div>"
                    f"<div style='font-size:1rem;font-weight:700;color:{tf_color};'>"
                    f"{r['trufil_verdict']}</div>"
                    f"<div style='font-size:.78rem;color:#888;margin-top:3px;'>"
                    f"Stated limit: {r['limit']:.0f} ppm" if r['limit'] else "Stated limit: —"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with c2:
                st.markdown(
                    f"<div class='section-label'>Assessment Basis</div>"
                    f"<div style='font-size:.85rem;color:#444;'>{r['basis']}</div>",
                    unsafe_allow_html=True,
                )

            st.divider()

            # Equipment & Report metadata grid
            st.markdown("<div class='section-label'>Equipment & Report Details</div>",
                        unsafe_allow_html=True)
            fluid = r.get("fluid_type", "—")
            lim_s = r.get("limit_std", "—")
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
                f"<div class='mcell' style='grid-column:span 4'><div class='mcell-lbl'>Sampling Point</div>"
                f"<div class='mcell-val'>{r['sampling_point']}</div></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

# ND group
nd_group = [r for r in rlist if r["verdict"] == "ND"]
if nd_group:
    st.markdown("### ⚪ Not Detected")
    for r in nd_group:
        st.warning(f"**{r['name']}** — water content not found in PDF. "
                   f"Verify source document format.")

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
  <div style="margin-top:.7rem;color:#888;font-size:.72rem;">
    Test method: Karl Fischer titration per IS 13567 / ASTM D1533 / IEC 60814.
    Units: mg/kg (ppm by weight). Limit: 40 ppm max per IS 1866:2017 Table-5.
  </div>
</div>
""", unsafe_allow_html=True)
