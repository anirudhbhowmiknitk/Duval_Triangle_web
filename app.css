"""
app_v4.py — Transformer DGA Dashboard
=======================================
Run:  streamlit run app_v4.py

Classification uses ONLY the vendor recommendation text.
Severity tiers:
  🔴 BAD    — danger keywords
  🟠 MILD   — caution keywords
  🟢 GOOD   — normal/clear keywords OR no flags

Aesthetic: Industrial Precision — dark slate, copper/amber accents,
           monospace data, technical engineering feel.
"""

import os, tempfile
import streamlit as st
import pandas as pd

from transformer_oil_extractor_v7 import parse_pdf
from duval_triangle_v7 import generate_duval_image, FAULT_MEANINGS, classify_duval

# ─── Keyword lists ────────────────────────────────────────────────────────────
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

SEVERITY_ICON  = {"BAD": "🔴", "MILD": "🟠", "GOOD": "🟢"}
SEVERITY_ORDER = {"BAD": 0, "MILD": 1, "GOOD": 2, "ERR": 3}

ZONE_EMOJI = {
    "PD": "🔵", "T1": "🟢", "T2": "🟠", "T3": "🔴",
    "D1": "🟠", "D2": "🔴", "DT": "🔴",
}

GAS_TABLE = [
    ("H₂",   "h2",   50,   300),
    ("O₂",   "o2",   0,    0),
    ("N₂",   "n2",   0,    0),
    ("CO",   "co",   400,  1000),
    ("CH₄",  "ch4",  30,   120),
    ("CO₂",  "co2",  3800, 9000),
    ("C₂H₂", "c2h2", 2,    10),
    ("C₂H₄", "c2h4", 60,   200),
    ("C₂H₆", "c2h6", 20,   90),
    ("C₃H₆", "c3h6", 0,    0),
    ("C₃H₈", "c3h8", 0,    0),
]

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Transformer DGA Dashboard",
    page_icon="⚡",
    layout="wide",
)

# ─── Global CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Source+Sans+3:wght@300;400;600&display=swap');

/* ── Reset & base ── */
html, body, [class*="css"] {
    font-family: 'Source Sans 3', sans-serif;
    background-color: #0d1117 !important;
    color: #c9d1d9;
}
.block-container {
    padding: 1rem 2rem 3rem !important;
    max-width: 1400px;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #161b22; }
::-webkit-scrollbar-thumb { background: #c87941; border-radius: 3px; }

/* ═══════════════════════════════════════════════════════
   HERO
═══════════════════════════════════════════════════════ */
.hero {
    position: relative;
    background: linear-gradient(135deg, #0d1117 0%, #161b22 40%, #1c2128 100%);
    border: 1px solid #30363d;
    border-bottom: 3px solid #c87941;
    border-radius: 12px 12px 0 0;
    padding: 2rem 2.5rem 1.6rem;
    margin-bottom: 0;
    overflow: hidden;
}
.hero::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: radial-gradient(ellipse at 80% 50%, rgba(200,121,65,0.07) 0%, transparent 65%);
    pointer-events: none;
}
.hero-grid-bg {
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background-image:
        linear-gradient(rgba(200,121,65,0.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(200,121,65,0.05) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
}
.hero-eyebrow {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    color: #c87941;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    margin-bottom: 0.5rem;
}
.hero h1 {
    font-family: 'Rajdhani', sans-serif;
    font-size: 2.6rem;
    font-weight: 700;
    color: #e6edf3;
    margin: 0 0 0.3rem;
    letter-spacing: 0.04em;
    line-height: 1.1;
}
.hero h1 span { color: #c87941; }
.hero-sub {
    font-size: 0.88rem;
    color: #6e7681;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: 0.03em;
}
.hero-badge {
    display: inline-block;
    background: rgba(200,121,65,0.15);
    border: 1px solid rgba(200,121,65,0.4);
    color: #c87941;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem;
    padding: 2px 10px;
    border-radius: 20px;
    margin-right: 8px;
    letter-spacing: 0.08em;
}

/* ═══════════════════════════════════════════════════════
   SUMMARY STRIP
═══════════════════════════════════════════════════════ */
.summary-strip {
    display: flex;
    gap: 0;
    background: #161b22;
    border: 1px solid #30363d;
    border-top: none;
    border-radius: 0 0 12px 12px;
    margin-bottom: 2rem;
    overflow: hidden;
}
.sc {
    flex: 1;
    padding: 1.1rem 1.5rem;
    border-right: 1px solid #21262d;
    text-align: center;
    transition: background 0.2s;
}
.sc:last-child { border-right: none; }
.sc:hover { background: #1c2128; }
.sc-num {
    font-family: 'Rajdhani', sans-serif;
    font-size: 2.2rem;
    font-weight: 700;
    line-height: 1;
    margin-bottom: 4px;
}
.sc-lbl {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    opacity: 0.65;
}
.sc-total  { color: #8b949e; }
.sc-bad    { color: #f85149; }
.sc-mild   { color: #e3b341; }
.sc-good   { color: #3fb950; }
.sc-err    { color: #6e7681; }

/* ═══════════════════════════════════════════════════════
   UPLOAD ZONE
═══════════════════════════════════════════════════════ */
.upload-zone {
    border: 2px dashed #30363d;
    border-radius: 12px;
    padding: 3rem 2rem;
    text-align: center;
    background: #0d1117;
    margin: 1rem 0 2rem;
    transition: border-color 0.3s;
}
.upload-zone:hover { border-color: #c87941; }
.upload-icon { font-size: 3rem; margin-bottom: 0.8rem; }
.upload-title {
    font-family: 'Rajdhani', sans-serif;
    font-size: 1.4rem;
    font-weight: 600;
    color: #e6edf3;
    margin-bottom: 0.3rem;
}
.upload-sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    color: #6e7681;
}

/* ═══════════════════════════════════════════════════════
   SEVERITY SECTION HEADERS
═══════════════════════════════════════════════════════ */
.tier-header {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.8rem 1.4rem;
    border-radius: 8px;
    margin: 1.5rem 0 0.6rem;
    font-family: 'Rajdhani', sans-serif;
    font-size: 1.1rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.tier-bad  { background: rgba(248,81,73,0.12);  border-left: 4px solid #f85149; color: #f85149; }
.tier-mild { background: rgba(227,179,65,0.12); border-left: 4px solid #e3b341; color: #e3b341; }
.tier-good { background: rgba(63,185,80,0.12);  border-left: 4px solid #3fb950; color: #3fb950; }
.tier-count {
    margin-left: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    opacity: 0.7;
    letter-spacing: 0.05em;
}

/* ═══════════════════════════════════════════════════════
   VERDICT PILL
═══════════════════════════════════════════════════════ */
.verdict {
    display: inline-flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.5rem 1.1rem;
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    font-weight: 600;
    margin-bottom: 1rem;
    letter-spacing: 0.04em;
}
.verdict-bad  { background: rgba(248,81,73,0.12);  border: 1px solid rgba(248,81,73,0.4);  color: #f85149; }
.verdict-mild { background: rgba(227,179,65,0.12); border: 1px solid rgba(227,179,65,0.4); color: #e3b341; }
.verdict-good { background: rgba(63,185,80,0.12);  border: 1px solid rgba(63,185,80,0.4);  color: #3fb950; }

/* ═══════════════════════════════════════════════════════
   RECOMMENDATION CARDS
═══════════════════════════════════════════════════════ */
.rec-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.8rem;
    margin-bottom: 1.2rem;
}
.rec-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    padding: 0.9rem 1rem;
}
.rec-card-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.4rem;
}
.rec-card-body {
    font-size: 0.88rem;
    color: #c9d1d9;
    line-height: 1.5;
}
.rec-ost   { border-top: 2px solid #388bfd; }
.rec-ost   .rec-card-label { color: #388bfd; }
.rec-dga   { border-top: 2px solid #3fb950; }
.rec-dga   .rec-card-label { color: #3fb950; }
.rec-ovr   { border-top: 2px solid #bc8cff; }
.rec-ovr   .rec-card-label { color: #bc8cff; }
.rec-warn  { border-top: 2px solid #e3b341; }
.rec-warn  .rec-card-label { color: #e3b341; }

/* ═══════════════════════════════════════════════════════
   SECTION LABELS
═══════════════════════════════════════════════════════ */
.sec-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: #c87941;
    padding: 0.5rem 0 0.3rem;
    border-bottom: 1px solid #21262d;
    margin: 1rem 0 0.7rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.sec-label::after {
    content: '';
    flex: 1;
    height: 1px;
    background: linear-gradient(to right, #21262d, transparent);
}

/* ═══════════════════════════════════════════════════════
   METRIC CELLS  (replaces st.metric)
═══════════════════════════════════════════════════════ */
.mcell {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 0.65rem 0.85rem;
    margin-bottom: 0.5rem;
}
.mcell-lbl {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #6e7681;
    margin-bottom: 3px;
}
.mcell-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.9rem;
    font-weight: 600;
    color: #e6edf3;
    word-break: break-word;
}

/* ═══════════════════════════════════════════════════════
   GAS TABLE
═══════════════════════════════════════════════════════ */
.gas-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
}
.gas-table thead th {
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #6e7681;
    padding: 4px 8px;
    border-bottom: 1px solid #21262d;
    text-align: left;
}
.gas-table thead th:nth-child(2) { text-align: right; }
.gas-table tbody tr { border-bottom: 1px solid #161b22; transition: background 0.15s; }
.gas-table tbody tr:hover { background: #161b22; }
.gas-table td { padding: 5px 8px; }
.gas-table td:nth-child(2) { text-align: right; font-weight: 600; }
.gas-table td:nth-child(3) { text-align: center; width: 30px; }
.gas-table td:nth-child(4) { color: #6e7681; font-size: 0.7rem; }
.gn  { color: #8b949e; }
.gm  { color: #e3b341; background: rgba(227,179,65,0.06); }
.gd  { color: #f85149; background: rgba(248,81,73,0.08);  }
.gnd { color: #484f58; }

/* TDCG pill */
.tdcg-pill {
    display: flex;
    gap: 1rem;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 0.55rem 0.9rem;
    margin-top: 0.7rem;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
}
.tdcg-pill span { color: #6e7681; margin-right: 0.3rem; }

/* Duval caption */
.duval-caption {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 0.5rem 0.8rem;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    color: #8b949e;
    margin-top: 0.5rem;
    text-align: center;
}
.duval-caption strong { color: #c87941; }

/* ═══════════════════════════════════════════════════════
   STREAMLIT OVERRIDES
═══════════════════════════════════════════════════════ */
/* Expander */
details {
    background: #0d1117 !important;
    border: 1px solid #21262d !important;
    border-radius: 8px !important;
    margin-bottom: 0.5rem !important;
}
details[open] { border-color: #30363d !important; }
details summary {
    font-family: 'Source Sans 3', sans-serif !important;
    font-size: 0.95rem !important;
    color: #c9d1d9 !important;
    padding: 0.8rem 1rem !important;
    cursor: pointer !important;
}
details summary:hover { background: #161b22 !important; border-radius: 8px; }

/* File uploader */
[data-testid="stFileUploader"] {
    background: #161b22 !important;
    border: 1px dashed #30363d !important;
    border-radius: 10px !important;
}

/* Metrics */
[data-testid="stMetricValue"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.95rem !important;
    color: #e6edf3 !important;
}
[data-testid="stMetricLabel"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.6rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.1em !important;
    color: #6e7681 !important;
}

/* Info/warning/success */
[data-testid="stAlert"] {
    background: #161b22 !important;
    border-radius: 8px !important;
    font-family: 'Source Sans 3', sans-serif !important;
}

/* Progress */
[data-testid="stProgressBar"] > div { background: #c87941 !important; }

/* Divider */
hr { border-color: #21262d !important; margin: 1rem 0 !important; }

/* JSON */
[data-testid="stJson"] { background: #161b22 !important; }

/* Caption */
[data-testid="stCaptionContainer"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.72rem !important;
    color: #6e7681 !important;
}

/* Hide Streamlit branding in header */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def safe_float(val) -> float:
    try:
        s = str(val).strip().upper()
        return 0.0 if s in ("ND", "NOT FOUND", "", "NS", "NA") else float(s)
    except (TypeError, ValueError):
        return 0.0


def classify_by_vendor_rec(data: dict) -> tuple:
    texts = " ".join([
        str(data.get("recommendation", "")),
        str(data.get("ost_recommendation", "")),
        str(data.get("dga_recommendation", "")),
    ]).lower()
    for kw in DANGER_KEYWORDS:
        if kw in texts: return "BAD",  f"'{kw}'"
    for kw in MILD_KEYWORDS:
        if kw in texts: return "MILD", f"'{kw}'"
    for kw in NORMAL_KEYWORDS:
        if kw in texts: return "GOOD", f"'{kw}'"
    return "GOOD", "no flagged keywords"


def get_duval_zone(data: dict):
    ch4  = safe_float(data.get("ch4",  0))
    c2h4 = safe_float(data.get("c2h4", 0))
    c2h2 = safe_float(data.get("c2h2", 0))
    total = ch4 + c2h4 + c2h2
    if total == 0:
        return "T1", 0.0, 0.0, 0.0
    return classify_duval(ch4/total*100, c2h4/total*100, c2h2/total*100), \
           ch4/total*100, c2h4/total*100, c2h2/total*100


def v(data, key, unit="") -> str:
    val = data.get(key, "")
    if not val or str(val).strip().upper() in ("ND", "NOT FOUND", "", "NS", "NA"):
        return "—"
    return f"{val} {unit}".strip()


def gas_status(data, key, mild_lim, danger_lim):
    raw = data.get(key, "ND")
    if str(raw).strip().upper() in ("ND", "", "NS"): return "ND", "nd"
    val = safe_float(raw)
    if val > danger_lim > 0: return f"{val:.1f}", "danger"
    if val > mild_lim  > 0: return f"{val:.1f}", "mild"
    return f"{val:.1f}", "norm"


def mcell(label, value):
    """Render a dark-themed metric cell."""
    return (
        f"<div class='mcell'>"
        f"<div class='mcell-lbl'>{label}</div>"
        f"<div class='mcell-val'>{value}</div>"
        f"</div>"
    )


def mcell_row(pairs):
    """Render N metric cells in a CSS grid row."""
    cols = " ".join(
        f"<div>{mcell(lbl, val)}</div>"
        for lbl, val in pairs
    )
    n = len(pairs)
    return (
        f"<div style='display:grid;grid-template-columns:repeat({n},1fr);gap:8px;'>"
        f"{cols}</div>"
    )


def gas_html(data) -> str:
    DOT = {"norm": "●", "mild": "▲", "danger": "▶", "nd": "·"}
    CLR = {"norm": "#3fb950", "mild": "#e3b341", "danger": "#f85149", "nd": "#484f58"}
    rows = ""
    for label, key, mild_l, danger_l in GAS_TABLE:
        val_str, status = gas_status(data, key, mild_l, danger_l)
        c   = CLR.get(status, "#8b949e")
        dot = DOT.get(status, "")
        tr_cls = {"norm":"gn","mild":"gm","danger":"gd","nd":"gnd"}.get(status,"gn")
        rows += (
            f"<tr class='{tr_cls}'>"
            f"<td>{label}</td>"
            f"<td style='color:{c}'>{val_str}</td>"
            f"<td style='color:{c};font-size:0.65rem'>{dot}</td>"
            f"<td>ppm</td></tr>"
        )
    return (
        "<table class='gas-table'>"
        "<thead><tr>"
        "<th>Gas</th><th style='text-align:right'>Conc.</th><th></th><th>Unit</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )


# ─── Hero banner ──────────────────────────────────────────────────────────────

st.markdown("""
<div class="hero">
  <div class="hero-grid-bg"></div>
  <div class="hero-eyebrow">IEC 60599 · IS 9434 · DGA Analysis System</div>
  <h1>⚡ Transformer <span>Oil Analysis</span> Dashboard</h1>
  <div class="hero-sub" style="margin-bottom:0.8rem">
    Dissolved Gas Analysis · Vendor-Recommendation Classification · Multi-Report Batch Processing
  </div>
  <span class="hero-badge">DGA</span>
  <span class="hero-badge">OST</span>
  <span class="hero-badge">DUVAL TRIANGLE</span>
  <span class="hero-badge">TRU-FIL · SGS · CPRI</span>
</div>
""", unsafe_allow_html=True)

# ─── Upload ────────────────────────────────────────────────────────────────────

pdfs = st.file_uploader(
    "📄 Upload DGA Report PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

if not pdfs:
    st.markdown("""
    <div class="upload-zone">
      <div class="upload-icon">📂</div>
      <div class="upload-title">Drop PDF Reports Here</div>
      <div class="upload-sub">TRU-FIL (IS standard) &nbsp;·&nbsp; SGS &nbsp;·&nbsp; CPRI format
      &nbsp;·&nbsp; Multiple files supported</div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ─── Parse ─────────────────────────────────────────────────────────────────────

if "reports" not in st.session_state:
    st.session_state.reports = {}

reports  = st.session_state.reports
progress = st.progress(0, text="Parsing reports …")

for i, pdf_file in enumerate(pdfs):
    key = f"{pdf_file.name}:{pdf_file.size}"
    if key not in reports:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_file.read())
            tmp_path = tmp.name
        try:
            data = parse_pdf(tmp_path)
            severity, matched_kw = classify_by_vendor_rec(data)
            zone, p1, p2, p3    = get_duval_zone(data)
            img_path = os.path.join(
                tempfile.gettempdir(), f"duval_{abs(hash(key))}.png"
            )
            generate_duval_image(data, output_path=img_path)
            reports[key] = dict(
                name=pdf_file.name, data=data,
                severity=severity, matched_kw=matched_kw,
                zone=zone, pCH4=p1, pC2H4=p2, pC2H2=p3,
                img=img_path, error=None,
            )
        except Exception as exc:
            reports[key] = dict(
                name=pdf_file.name, data={}, zone="ERR",
                severity="ERR", matched_kw=str(exc),
                error=str(exc), img=None,
            )
        finally:
            os.unlink(tmp_path)
    progress.progress((i + 1) / len(pdfs), text=f"Parsing {i+1}/{len(pdfs)} — {pdf_file.name}")

progress.empty()

all_reports = sorted(
    reports.values(),
    key=lambda r: SEVERITY_ORDER.get(r["severity"], 99)
)

# ─── Summary strip ─────────────────────────────────────────────────────────────

n_total = len(all_reports)
n_bad   = sum(1 for r in all_reports if r["severity"] == "BAD")
n_mild  = sum(1 for r in all_reports if r["severity"] == "MILD")
n_good  = sum(1 for r in all_reports if r["severity"] == "GOOD")
n_err   = sum(1 for r in all_reports if r["severity"] == "ERR")

err_html = (
    f"<div class='sc sc-err'><div class='sc-num'>{n_err}</div>"
    f"<div class='sc-lbl'>ERR</div></div>"
) if n_err else ""

st.markdown(f"""
<div class="summary-strip">
  <div class="sc sc-total"><div class="sc-num">{n_total}</div><div class="sc-lbl">Total Reports</div></div>
  <div class="sc sc-bad"  ><div class="sc-num">{n_bad}</div><div class="sc-lbl">🔴 Critical</div></div>
  <div class="sc sc-mild" ><div class="sc-num">{n_mild}</div><div class="sc-lbl">🟠 Monitor</div></div>
  <div class="sc sc-good" ><div class="sc-num">{n_good}</div><div class="sc-lbl">🟢 Normal</div></div>
  {err_html}
</div>
""", unsafe_allow_html=True)


# ─── Report card ───────────────────────────────────────────────────────────────

def render_report_card(rep: dict):
    data = rep["data"]
    sev  = rep["severity"]

    if rep.get("error"):
        st.error(f"⚠ Parse error — **{rep['name']}**: {rep['error']}")
        return

    equip   = v(data, "equipment_designation") or v(data, "css_name") or rep["name"]
    sp      = v(data, "sampling_point")
    is_oltc = "OLTC" in str(data.get("sampling_point", "")).upper()
    fmt     = data.get("fmt", "")

    label = f"{SEVERITY_ICON[sev]}  {equip}   ·   {sp}   ·   {rep['name']}"

    with st.expander(label, expanded=(sev == "BAD")):

        # ── Verdict ──────────────────────────────────────────────────────────
        vc = {"BAD":"bad","MILD":"mild","GOOD":"good"}.get(sev,"good")
        st.markdown(
            f"<div class='verdict verdict-{vc}'>"
            f"{SEVERITY_ICON[sev]}&nbsp; Vendor Classification: <strong>{sev}</strong>"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;Trigger: {rep['matched_kw']}"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;Format: {fmt}"
            f"</div>",
            unsafe_allow_html=True,
        )

        # ── Recommendations ──────────────────────────────────────────────────
        st.markdown("<div class='sec-label'>Vendor Recommendations</div>", unsafe_allow_html=True)

        ost_rec = v(data, "ost_recommendation")
        dga_rec = v(data, "dga_recommendation")
        overall = v(data, "recommendation")
        is_ns   = is_oltc and dga_rec != "—" and "not specified" in dga_rec.lower()
        dga_cls = "rec-warn" if is_ns else "rec-dga"
        dga_body = (dga_rec + " <em style='color:#e3b341;font-size:0.8em'>(Limits NS — OLTC)</em>") \
                   if is_ns else (dga_rec if dga_rec != "—" else "<em style='color:#484f58'>Not found</em>")

        st.markdown(f"""
        <div class='rec-grid'>
          <div class='rec-card rec-ost'>
            <div class='rec-card-label'>⚡ OST</div>
            <div class='rec-card-body'>{ost_rec if ost_rec != "—" else "<em style='color:#484f58'>Not found</em>"}</div>
          </div>
          <div class='rec-card {dga_cls}'>
            <div class='rec-card-label'>🔬 DGA</div>
            <div class='rec-card-body'>{dga_body}</div>
          </div>
          <div class='rec-card rec-ovr'>
            <div class='rec-card-label'>📋 Overall</div>
            <div class='rec-card-body'>{overall if overall != "—" else "<em style='color:#484f58'>Not found</em>"}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Equipment ────────────────────────────────────────────────────────
        st.markdown("<div class='sec-label'>Equipment Identity</div>", unsafe_allow_html=True)
        st.markdown(mcell_row([
            ("Equipment Designation", v(data,"equipment_designation") or "—"),
            ("CSS / Owner",           v(data,"css_name") if v(data,"css_name") != "—" else v(data,"owner")),
            ("Transformer No.",       v(data,"transformer_no")),
            ("Format",                fmt or "—"),
        ]), unsafe_allow_html=True)
        st.markdown(mcell_row([
            ("Manufacturer",   v(data,"manufacturer")),
            ("Rating",         v(data,"rating")),
            ("Voltage Class",  v(data,"voltage_class")),
            ("Sampling Point", v(data,"sampling_point")),
        ]), unsafe_allow_html=True)

        # ── Report metadata ───────────────────────────────────────────────────
        st.markdown("<div class='sec-label'>Report Metadata</div>", unsafe_allow_html=True)
        st.markdown(mcell_row([
            ("Report No.",    v(data,"report_no")),
            ("Report Date",   v(data,"report_date")),
            ("Sampling Date", v(data,"sampling_date")),
            ("Weather",       v(data,"weather_condition")),
        ]), unsafe_allow_html=True)

        # ── OST ──────────────────────────────────────────────────────────────
        st.markdown("<div class='sec-label'>Oil Sampling Test (OST)</div>", unsafe_allow_html=True)
        st.markdown(mcell_row([
            ("BDV",            v(data,"bdv",            "kV")),
            ("Water Content",  v(data,"water",          "ppm")),
            ("IFT",            v(data,"ift",            "N/m")),
            ("Neutralization", v(data,"neutralization", "mgKOH/g")),
        ]), unsafe_allow_html=True)
        st.markdown(mcell_row([
            ("Density",      v(data,"density",  "g/cm³")),
            ("Color (ASTM)", v(data,"color")),
            ("Flash Point",  v(data,"flash",    "°C")),
            ("OQI",          v(data,"oqi")),
        ]), unsafe_allow_html=True)

        # ── DGA ───────────────────────────────────────────────────────────────
        st.markdown("<div class='sec-label'>Dissolved Gas Analysis (DGA)</div>", unsafe_allow_html=True)

        col_duval, col_gas = st.columns([3, 2])

        with col_duval:
            st.markdown(
                "<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;"
                "text-transform:uppercase;letter-spacing:0.12em;color:#6e7681;"
                "margin-bottom:0.4rem'>Duval Triangle — IEC 60599 (Informational)</div>",
                unsafe_allow_html=True
            )
            if rep["img"] and os.path.exists(rep["img"]):
                st.image(rep["img"], use_container_width=True)
                zone_lbl   = rep.get("zone", "—")
                fault_desc = FAULT_MEANINGS.get(zone_lbl, "")
                st.markdown(
                    f"<div class='duval-caption'>"
                    f"{ZONE_EMOJI.get(zone_lbl,'●')}&nbsp; Zone <strong>{zone_lbl}</strong>"
                    f" &nbsp;·&nbsp; {fault_desc}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.warning("Duval image unavailable.")

        with col_gas:
            st.markdown(
                "<div style='font-family:JetBrains Mono,monospace;font-size:0.65rem;"
                "text-transform:uppercase;letter-spacing:0.12em;color:#6e7681;"
                "margin-bottom:0.4rem'>Gas Concentrations</div>",
                unsafe_allow_html=True
            )
            st.markdown(gas_html(data), unsafe_allow_html=True)
            tdcg = v(data, "tdcg", "ppm")
            tgc  = v(data, "tgc",  "v/v%")
            st.markdown(
                f"<div class='tdcg-pill'>"
                f"<div><span>TDCG</span><strong>{tdcg}</strong></div>"
                f"<div><span>TGC</span><strong>{tgc}</strong></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # ── Raw data ─────────────────────────────────────────────────────────
        with st.expander("{ } Raw Extracted JSON", expanded=False):
            st.json(data)


# ─── Tier groups ───────────────────────────────────────────────────────────────

TIERS = [
    ("BAD",  "CRITICAL — Immediate Action Required", "bad"),
    ("MILD", "MONITOR — Investigate / Retest",       "mild"),
    ("GOOD", "NORMAL — No Action Required",          "good"),
]

for tier, label, css in TIERS:
    group = [r for r in all_reports if r["severity"] == tier]
    if not group: continue
    st.markdown(
        f"<div class='tier-header tier-{css}'>"
        f"{SEVERITY_ICON[tier]}&nbsp; {label}"
        f"<span class='tier-count'>{len(group)} report{'s' if len(group)!=1 else ''}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    for rep in group:
        render_report_card(rep)

# ─── Errors ────────────────────────────────────────────────────────────────────

err_group = [r for r in all_reports if r["severity"] == "ERR"]
if err_group:
    st.markdown("<div class='tier-header' style='border-left:4px solid #6e7681;color:#6e7681;background:rgba(110,118,129,0.08)'>❌ Parse Errors</div>", unsafe_allow_html=True)
    for rep in err_group:
        st.error(f"**{rep['name']}** — {rep.get('error','unknown error')}")