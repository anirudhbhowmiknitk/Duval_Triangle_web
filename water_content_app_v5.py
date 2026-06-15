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
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=JetBrains+Mono:wght@400;600&family=Source+Sans+3:wght@300;400;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Source Sans 3', sans-serif;
    background-color: #0d1117 !important;
    color: #c9d1d9;
}
.block-container { padding: 1rem 2rem 3rem !important; max-width: 1300px; }
::-webkit-scrollbar { width:6px; } ::-webkit-scrollbar-track { background:#161b22; }
::-webkit-scrollbar-thumb { background:#388bfd; border-radius:3px; }

.hero {
    position:relative;
    background:linear-gradient(135deg,#0d1117 0%,#161b22 40%,#1c2128 100%);
    border:1px solid #30363d; border-bottom:3px solid #388bfd;
    border-radius:12px; padding:2rem 2.5rem 1.6rem; margin-bottom:2rem; overflow:hidden;
}
.hero::before {
    content:''; position:absolute; inset:0;
    background:radial-gradient(ellipse at 80% 50%,rgba(56,139,253,0.07) 0%,transparent 65%);
    pointer-events:none;
}
.hero-grid {
    position:absolute; inset:0;
    background-image: linear-gradient(rgba(56,139,253,0.04) 1px,transparent 1px),
                      linear-gradient(90deg,rgba(56,139,253,0.04) 1px,transparent 1px);
    background-size:40px 40px; pointer-events:none;
}
.hero h1 {
    font-family:'Rajdhani',sans-serif; font-size:2.4rem; font-weight:700;
    color:#e6edf3; margin:0 0 0.3rem; letter-spacing:0.04em; line-height:1.1;
}
.hero h1 span { color:#388bfd; }
.hero-sub { font-size:0.85rem; color:#6e7681; font-family:'JetBrains Mono',monospace; }
.badge {
    display:inline-block; background:rgba(56,139,253,0.12);
    border:1px solid rgba(56,139,253,0.35); color:#388bfd;
    font-family:'JetBrains Mono',monospace; font-size:0.68rem;
    padding:2px 10px; border-radius:20px; margin-right:8px; letter-spacing:0.08em;
}

.sum-strip {
    display:flex; background:#161b22; border:1px solid #30363d;
    border-radius:10px; margin-bottom:2rem; overflow:hidden;
}
.sc { flex:1; padding:1rem 1.2rem; border-right:1px solid #21262d; text-align:center; }
.sc:last-child { border-right:none; }
.sc-num { font-family:'Rajdhani',sans-serif; font-size:2.1rem; font-weight:700; line-height:1; margin-bottom:3px; }
.sc-lbl { font-family:'JetBrains Mono',monospace; font-size:0.6rem; text-transform:uppercase; letter-spacing:0.12em; opacity:0.6; }

.tbl { width:100%; border-collapse:collapse; font-family:'JetBrains Mono',monospace; font-size:0.8rem; }
.tbl thead th {
    font-size:0.58rem; text-transform:uppercase; letter-spacing:0.12em;
    color:#6e7681; padding:7px 10px; border-bottom:2px solid #30363d;
    background:#0d1117; text-align:left; white-space:nowrap;
}
.tbl tbody tr { border-bottom:1px solid #1c2128; }
.tbl tbody tr:hover { background:#161b22; }
.tbl td { padding:7px 10px; vertical-align:middle; }

.pill { display:inline-block; padding:2px 11px; border-radius:20px;
        font-size:0.7rem; font-weight:600; letter-spacing:0.06em; }
.p-ok  { background:rgba(63,185,80,0.15);  border:1px solid rgba(63,185,80,0.4);  color:#3fb950; }
.p-mg  { background:rgba(227,179,65,0.15); border:1px solid rgba(227,179,65,0.4); color:#e3b341; }
.p-bad { background:rgba(248,81,73,0.15);  border:1px solid rgba(248,81,73,0.4);  color:#f85149; }
.p-nd  { background:rgba(110,118,129,0.12);border:1px solid rgba(110,118,129,0.3);color:#8b949e; }
.p-tf-ok  { background:rgba(56,139,253,0.1);  border:1px solid rgba(56,139,253,0.3);  color:#388bfd; }
.p-tf-bad { background:rgba(248,81,73,0.1);   border:1px solid rgba(248,81,73,0.3);   color:#f85149; }

.sec-lbl {
    font-family:'JetBrains Mono',monospace; font-size:0.6rem;
    text-transform:uppercase; letter-spacing:0.15em; color:#388bfd;
    border-bottom:1px solid #21262d; padding-bottom:0.35rem; margin:1.2rem 0 0.8rem;
}

.big-val-wrap {
    background:#0d1117; border:1px solid #30363d; border-radius:8px;
    padding:1.4rem 1.2rem; text-align:center;
}
.big-val-lbl {
    font-family:'JetBrains Mono',monospace; font-size:0.58rem;
    text-transform:uppercase; letter-spacing:0.15em; color:#6e7681; margin-bottom:6px;
}
.big-val {
    font-family:'Rajdhani',sans-serif; font-size:3.2rem; font-weight:700; line-height:1;
}
.big-val-unit {
    font-family:'JetBrains Mono',monospace; font-size:0.75rem; color:#6e7681; margin-top:3px;
}

.analysis-box {
    background:#0d1117; border:1px solid #30363d; border-radius:8px;
    padding:1.1rem 1.3rem;
    font-family:'Source Sans 3',sans-serif; font-size:0.88rem; line-height:1.65; color:#c9d1d9;
}

.meta-grid {
    display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:0.6rem;
}
.mcell { background:#161b22; border:1px solid #21262d; border-radius:6px; padding:0.6rem 0.8rem; }
.mcell-lbl { font-family:'JetBrains Mono',monospace; font-size:0.58rem; text-transform:uppercase;
              letter-spacing:0.12em; color:#6e7681; margin-bottom:3px; }
.mcell-val { font-family:'JetBrains Mono',monospace; font-size:0.82rem; font-weight:600; color:#e6edf3; word-break:break-word; }

.ref-box {
    background:#161b22; border:1px solid #30363d; border-left:4px solid #388bfd;
    border-radius:8px; padding:1rem 1.4rem; margin-top:1.5rem;
}
.ref-title { font-family:'Rajdhani',sans-serif; font-size:1rem; font-weight:700;
             color:#388bfd; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:0.6rem; }
.ref-row { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:0.25rem;
           font-family:'JetBrains Mono',monospace; font-size:0.76rem; }
.ref-lbl { color:#6e7681; min-width:180px; }

.upload-zone {
    border:2px dashed #30363d; border-radius:12px; padding:2.5rem 2rem;
    text-align:center; background:#0d1117; margin:1rem 0 2rem;
    transition:border-color .3s;
}
.upload-zone:hover { border-color:#388bfd; }

[data-testid="stFileUploader"] { background:#161b22 !important; border:1px dashed #30363d !important; border-radius:10px !important; }
[data-testid="stProgressBar"] > div { background:#388bfd !important; }
#MainMenu,footer,header { visibility:hidden; }
hr { border-color:#21262d !important; margin:1rem 0 !important; }
details { background:#0d1117 !important; border:1px solid #21262d !important; border-radius:8px !important; margin-bottom:0.5rem !important; }
details summary { font-family:'Source Sans 3',sans-serif !important; font-size:0.9rem !important; color:#c9d1d9 !important; padding:0.75rem 1rem !important; }
</style>
""", unsafe_allow_html=True)

# ─── Hero ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero">
  <div class="hero-grid"></div>
  <h1>💧 Transformer Oil — <span>Water Content</span> Analyser</h1>
  <div class="hero-sub" style="margin-bottom:0.8rem">
    Karl Fischer Method (IS 13567) · IS 1866 : 2017 / IEC 60422 · TRU-FIL Report Format
  </div>
  <span class="badge">KARL FISCHER</span>
  <span class="badge">IS 1866 : 2017</span>
  <span class="badge">IEC 60422</span>
  <span class="badge">TRU-FIL FORMAT</span>
  <span class="badge">MULTI-PDF BATCH</span>
</div>
""", unsafe_allow_html=True)

# ─── Upload ───────────────────────────────────────────────────────────────────
pdfs = st.file_uploader(
    "Upload", type=["pdf"], accept_multiple_files=True, label_visibility="collapsed"
)

if not pdfs:
    st.markdown("""
    <div class="upload-zone">
      <div style="font-size:2.5rem;margin-bottom:0.6rem">📂</div>
      <div style="font-family:Rajdhani,sans-serif;font-size:1.3rem;font-weight:600;color:#e6edf3">
        Drop TRU-FIL PDF Reports Here</div>
      <div style="font-family:JetBrains Mono,monospace;font-size:0.72rem;color:#6e7681;margin-top:0.3rem">
        Multiple files supported · Water content extracted automatically</div>
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
                extraction_method=f"error:{exc}",
            )
        os.unlink(tp)
        ppm = info["ppm"]
        kv  = info["voltage_kv"]
        v_code, v_label, colour, caution, reject, basis = classify(
            ppm, kv, info["limit"], info["fluid_type"], info["limit_std"]
        )
        cache[key] = dict(
            name=f.name, **info,
            verdict=v_code, verdict_label=v_label,
            colour=colour, caution=caution, reject=reject, basis=basis,
        )
    bar.progress((i + 1) / len(pdfs), text=f"Processing {i+1}/{len(pdfs)} — {f.name}")

bar.empty()

# ─── Sort & summarise ─────────────────────────────────────────────────────────
_ORDER = {"UNACCEPTABLE": 0, "ACCEPTABLE": 1, "ND": 2}
rlist = sorted(
    cache.values(),
    key=lambda r: (_ORDER.get(r["verdict"], 9), r["name"].lower()),
)

n_total  = len(rlist)
n_ok     = sum(1 for r in rlist if r["verdict"] == "ACCEPTABLE")
n_mg     = 0  # Marginal level removed — two levels only
n_bad    = sum(1 for r in rlist if r["verdict"] == "UNACCEPTABLE")
n_nd     = sum(1 for r in rlist if r["verdict"] == "ND")

st.markdown(f"""
<div class="sum-strip">
  <div class="sc"><div class="sc-num" style="color:#8b949e">{n_total}</div><div class="sc-lbl">Total PDFs</div></div>
  <div class="sc"><div class="sc-num" style="color:#3fb950">{n_ok}</div><div class="sc-lbl">✅ Acceptable</div></div>
  <div class="sc"><div class="sc-num" style="color:#f85149">{n_bad}</div><div class="sc-lbl">❌ Unacceptable</div></div>
  <div class="sc"><div class="sc-num" style="color:#6e7681">{n_nd}</div><div class="sc-lbl">– Not Detected</div></div>
</div>
""", unsafe_allow_html=True)

# ─── Results table ────────────────────────────────────────────────────────────
st.markdown("""<div class="sec-lbl">📋 &nbsp;Water Content Results — All PDFs</div>""",
            unsafe_allow_html=True)

_PIL = {"ACCEPTABLE":"p-ok","UNACCEPTABLE":"p-bad","ND":"p-nd"}
_TF  = {"Acceptable":"p-tf-ok","Not Acceptable":"p-tf-bad"}

rows = ""
for r in rlist:
    ppm_str = f"{r['ppm']:.1f}" if r["ppm"] is not None else "—"
    lim_str = f"{r['limit']:.0f} ppm" if r["limit"] else "—"
    kv_str  = f"{r['voltage_kv']:.0f} kV" if r["voltage_kv"] else "—"
    tf_cls  = _TF.get(r["trufil_verdict"], "p-nd")
    v_cls   = _PIL.get(r["verdict"], "p-nd")
    equip   = r["equipment"][:40] + "…" if len(r["equipment"]) > 40 else r["equipment"]
    fluid   = r.get("fluid_type", "—")
    fluid_short = fluid[:22] + "…" if len(fluid) > 22 else fluid

    rows += f"""
    <tr>
      <td style="color:#c9d1d9;max-width:220px;word-break:break-word">{r['name']}</td>
      <td style="color:#8b949e;font-size:0.75rem">{equip}</td>
      <td style="color:#8b949e">{kv_str}</td>
      <td style="color:#6e7681;font-size:0.72rem">{fluid_short}</td>
      <td style="color:#e6edf3;font-weight:700;font-size:0.92rem">{ppm_str}</td>
      <td style="color:#6e7681">{lim_str}</td>
      <td><span class="pill {tf_cls}">{r['trufil_verdict']}</span></td>
      <td><span class="pill {v_cls}">{r['verdict']}</span></td>
    </tr>
    """

st.components.v1.html(f"""
<!DOCTYPE html><html><head><style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Source+Sans+3:wght@400;600&display=swap');
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:transparent; font-family:'JetBrains Mono',monospace; }}
table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
thead th {{
    font-size:0.58rem; text-transform:uppercase; letter-spacing:0.12em;
    color:#6e7681; padding:7px 10px; border-bottom:2px solid #30363d;
    background:#0d1117; text-align:left; white-space:nowrap;
}}
tbody tr {{ border-bottom:1px solid #1c2128; }}
tbody tr:hover {{ background:#161b22; }}
td {{ padding:7px 10px; vertical-align:middle; color:#c9d1d9; }}
.pill {{ display:inline-block; padding:2px 11px; border-radius:20px; font-size:0.7rem; font-weight:600; letter-spacing:0.06em; }}
.p-ok  {{ background:rgba(63,185,80,0.15);  border:1px solid rgba(63,185,80,0.4);  color:#3fb950; }}
.p-mg  {{ background:rgba(227,179,65,0.15); border:1px solid rgba(227,179,65,0.4); color:#e3b341; }}
.p-bad {{ background:rgba(248,81,73,0.15);  border:1px solid rgba(248,81,73,0.4);  color:#f85149; }}
.p-nd  {{ background:rgba(110,118,129,0.12);border:1px solid rgba(110,118,129,0.3);color:#8b949e; }}
.p-tf-ok  {{ background:rgba(56,139,253,0.1);  border:1px solid rgba(56,139,253,0.3);  color:#388bfd; }}
.p-tf-bad {{ background:rgba(248,81,73,0.1);   border:1px solid rgba(248,81,73,0.3);   color:#f85149; }}
</style></head><body>
<table>
  <thead><tr>
    <th>PDF File</th>
    <th>Equipment</th>
    <th>Voltage Class</th>
    <th>Insulating Fluid</th>
    <th>Water Content (ppm)</th>
    <th>PDF Limit</th>
    <th>TRU-FIL Verdict</th>
    <th>Assessment</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</body></html>
""", height=min(max(80 + len(rlist) * 44, 120), 420), scrolling=True)


# ─── PDF Report Download ──────────────────────────────────────────────────────
import io, datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

def _build_pdf_report(rlist_data):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    title_style  = ParagraphStyle("t", parent=styles["Title"],  fontSize=14, spaceAfter=4)
    sub_style    = ParagraphStyle("s", parent=styles["Normal"], fontSize=8,  textColor=colors.grey, spaceAfter=12)
    normal_style = ParagraphStyle("n", parent=styles["Normal"], fontSize=8)

    elems = []
    elems.append(Paragraph("Transformer Oil — Water Content Analysis Report", title_style))
    elems.append(Paragraph(
        f"Generated: {datetime.datetime.now().strftime('%d-%m-%Y %H:%M')}  |  "
        f"Standard: IS 1866:2017 Table-5  |  Limit: 40 ppm max  |  "
        f"Method: Karl Fischer (IS 13567 / ASTM D1533 / IEC 60814)",
        sub_style))

    # Summary row
    n_ok  = sum(1 for r in rlist_data if r["verdict"] == "ACCEPTABLE")
    n_bad = sum(1 for r in rlist_data if r["verdict"] == "UNACCEPTABLE")
    n_nd  = sum(1 for r in rlist_data if r["verdict"] == "ND")
    sum_data = [
        ["Total PDFs", "Acceptable", "Unacceptable", "Not Detected"],
        [str(len(rlist_data)), str(n_ok), str(n_bad), str(n_nd)],
    ]
    sum_tbl = Table(sum_data, colWidths=[40*mm]*4)
    sum_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1c2128")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.HexColor("#8b949e")),
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("FONTNAME",   (0,0), (-1,-1), "Helvetica"),
        ("FONTNAME",   (0,1), (-1,1), "Helvetica-Bold"),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,1), [colors.HexColor("#0d1117")]),
        ("TEXTCOLOR",  (1,1), (1,1), colors.HexColor("#3fb950")),
        ("TEXTCOLOR",  (2,1), (2,1), colors.HexColor("#f85149")),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#30363d")),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    elems.append(sum_tbl)
    elems.append(Spacer(1, 8*mm))

    # Main results table
    hdr = ["PDF File", "Equipment", "kV", "Fluid", "ppm", "Limit", "TRU-FIL", "Assessment"]
    tbl_data = [hdr]
    for r in rlist_data:
        ppm_s  = f"{r['ppm']:.1f}" if r["ppm"] is not None else "ND"
        lim_s  = f"{r['limit']:.0f}" if r["limit"] else "—"
        kv_s   = f"{r['voltage_kv']:.0f}" if r["voltage_kv"] else "—"
        name_s = r["name"][:38] + "…" if len(r["name"]) > 38 else r["name"]
        equip_s = r["equipment"][:30] + "…" if len(r["equipment"]) > 30 else r["equipment"]
        fluid_s = r.get("fluid_type","—")[:20] + "…" if len(r.get("fluid_type","—")) > 20 else r.get("fluid_type","—")
        tbl_data.append([name_s, equip_s, kv_s, fluid_s, ppm_s, lim_s, r["trufil_verdict"], r["verdict"]])

    col_w = [52*mm, 38*mm, 10*mm, 28*mm, 12*mm, 12*mm, 18*mm, 22*mm]
    res_tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)

    row_styles = [
        ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#161b22")),
        ("TEXTCOLOR",   (0,0), (-1,0), colors.HexColor("#6e7681")),
        ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 7),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("TEXTCOLOR",   (0,1), (-1,-1), colors.HexColor("#c9d1d9")),
        ("ALIGN",       (2,0), (6,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("GRID",        (0,0), (-1,-1), 0.4, colors.HexColor("#21262d")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#0d1117"), colors.HexColor("#0a0f16")]),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
    ]
    # Colour-code assessment column
    for i, r in enumerate(rlist_data, start=1):
        v = r["verdict"]
        if v == "ACCEPTABLE":
            row_styles.append(("TEXTCOLOR", (7,i), (7,i), colors.HexColor("#3fb950")))
        elif v == "UNACCEPTABLE":
            row_styles.append(("TEXTCOLOR", (7,i), (7,i), colors.HexColor("#f85149")))
            row_styles.append(("FONTNAME",  (7,i), (7,i), "Helvetica-Bold"))

    res_tbl.setStyle(TableStyle(row_styles))
    elems.append(res_tbl)
    elems.append(Spacer(1, 8*mm))

    # Per-report detail paragraphs for unacceptable only
    bad = [r for r in rlist_data if r["verdict"] == "UNACCEPTABLE"]
    if bad:
        elems.append(Paragraph("Unacceptable Results — Action Required", styles["Heading2"]))
        for r in bad:
            ppm = r["ppm"]
            kv  = r["voltage_kv"]
            kv_str = f"{kv:.0f} kV" if kv else "unknown kV"
            elems.append(Paragraph(
                f"<b>{r['equipment']}</b> ({r['report_no']}, {r['report_date']}) — "
                f"{ppm:.1f} ppm  |  Limit: {r['reject']:.0f} ppm  |  {kv_str}",
                normal_style))
            elems.append(Paragraph(
                "Immediate actions: (1) Vacuum dehydration/filtration. "
                "(2) Inspect breather/conservator seal. "
                "(3) Verify BDV before continued operation at rated load. "
                "(4) Assess remaining insulation life if paper moisture is suspected.",
                normal_style))
            elems.append(Spacer(1, 4*mm))

    doc.build(elems)
    buf.seek(0)
    return buf.read()

st.markdown("<hr>", unsafe_allow_html=True)
col_dl, _ = st.columns([1, 3])
with col_dl:
    try:
        pdf_bytes = _build_pdf_report(rlist)
        st.download_button(
            label="⬇️  Download PDF Report",
            data=pdf_bytes,
            file_name=f"water_content_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    except Exception as e:
        st.warning(f"PDF export unavailable: {e}")

# ─── Per-PDF detailed cards ───────────────────────────────────────────────────
st.markdown("<hr>", unsafe_allow_html=True)
st.markdown("""<div class="sec-lbl">🔍 &nbsp;Detailed Analysis per Report</div>""",
            unsafe_allow_html=True)

_ICONS = {"ACCEPTABLE":"✅","UNACCEPTABLE":"❌","ND":"–"}

for r in rlist:
    ppm    = r["ppm"]
    v_code = r["verdict"]
    colour = r["colour"]
    kv     = r["voltage_kv"]
    icon   = _ICONS.get(v_code, "–")
    ppm_disp = f"{ppm:.1f} ppm" if ppm is not None else "ND"
    equip  = r["equipment"]
    title  = f"{icon}  {equip}   ·   {ppm_disp}   ·   {r['name']}"

    with st.expander(title, expanded=(v_code == "UNACCEPTABLE")):

        # ── Value + verdict row ───────────────────────────────────────────────
        c1, c2, c3 = st.columns([1, 1, 2])

        with c1:
            st.markdown(
                f"<div class='big-val-wrap'>"
                f"<div class='big-val-lbl'>💧 Water Content (Karl Fischer)</div>"
                f"<div class='big-val' style='color:{colour}'>"
                f"{'%.1f' % ppm if ppm is not None else 'ND'}</div>"
                f"<div class='big-val-unit'>mg/kg (ppm)</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        with c2:
            lim_disp = f"{r['limit']:.0f} ppm max" if r["limit"] else "—"
            tf_cls   = _TF.get(r["trufil_verdict"], "p-nd")
            st.markdown(
                f"<div class='big-val-wrap' style='text-align:left'>"
                f"<div class='big-val-lbl'>TRU-FIL Stated Limit</div>"
                f"<div style='font-family:JetBrains Mono,monospace;font-size:1.1rem;"
                f"font-weight:600;color:#c9d1d9;margin-bottom:8px'>{lim_disp}</div>"
                f"<div class='big-val-lbl' style='margin-top:8px'>TRU-FIL Verdict</div>"
                f"<span class='pill {tf_cls}' style='font-size:0.78rem'>{r['trufil_verdict']}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        with c3:
            v_pill_cls = _PIL.get(v_code, "p-nd")
            st.markdown(
                f"<div class='analysis-box'>"
                f"<div style='margin-bottom:6px'>"
                f"<span class='pill {v_pill_cls}' style='font-size:0.78rem'>{v_code}</span>"
                f"&nbsp;&nbsp;<span style='font-size:0.78rem;color:#8b949e'>{r['basis']}</span>"
                f"</div>"
                f"{_analysis_text(ppm, v_code, kv, r['caution'], r['reject'])}"
                f"</div>",
                unsafe_allow_html=True,
            )

        # ── Metadata ─────────────────────────────────────────────────────────
        st.markdown("<div class='sec-lbl' style='margin-top:0.8rem'>Equipment & Report Details</div>",
                    unsafe_allow_html=True)
        kv_s  = f"{kv:.0f} kV" if kv else "—"
        fluid = r.get("fluid_type", "—")
        lim_s = r.get("limit_std", "—")
        st.markdown(
            f"<div class='meta-grid'>"
            f"<div class='mcell'><div class='mcell-lbl'>Report No.</div><div class='mcell-val'>{r['report_no']}</div></div>"
            f"<div class='mcell'><div class='mcell-lbl'>Report Date</div><div class='mcell-val'>{r['report_date']}</div></div>"
            f"<div class='mcell'><div class='mcell-lbl'>Voltage Class</div><div class='mcell-val'>{kv_s}</div></div>"
            f"<div class='mcell'><div class='mcell-lbl'>Rating</div><div class='mcell-val'>{r['rating']}</div></div>"
            f"<div class='mcell' style='grid-column:span 2'><div class='mcell-lbl'>Equipment Designation</div><div class='mcell-val'>{equip}</div></div>"
            f"<div class='mcell'><div class='mcell-lbl'>Insulating Fluid</div><div class='mcell-val'>{fluid}</div></div>"
            f"<div class='mcell'><div class='mcell-lbl'>Limit Standard</div><div class='mcell-val'>{lim_s}</div></div>"
            f"<div class='mcell' style='grid-column:span 4'><div class='mcell-lbl'>Sampling Point</div><div class='mcell-val'>{r['sampling_point']}</div></div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ─── Threshold reference ──────────────────────────────────────────────────────
st.markdown(f"""
<div class="ref-box">
  <div class="ref-title">📐 Threshold Reference — IS 1866 : 2017 / IEC 60422</div>
  <div class="ref-row"><span class="ref-lbl">≤ 40 ppm</span>
    <span style="color:#3fb950;font-weight:600">✅ Acceptable</span>
    <span style="color:#6e7681">— no action required</span></div>
  <div class="ref-row"><span class="ref-lbl">&gt; 40 ppm</span>
    <span style="color:#f85149;font-weight:600">❌ Unacceptable</span>
    <span style="color:#6e7681">— immediate filtration / oil replacement</span></div>
  <div style="margin-top:0.7rem;color:#484f58;font-size:0.7rem;font-family:JetBrains Mono,monospace">
    Test method: Karl Fischer titration per IS 13567 / ASTM D1533 / IEC 60814.
    Units: mg/kg (ppm by weight). Limit: 40 ppm max per IS 1866:2017 Table-5.
  </div>
</div>
""", unsafe_allow_html=True)
