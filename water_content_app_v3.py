"""
app_water.py — Transformer Oil · Water Content Monitor  (Triple-Check Edition)
================================================================================
Water content is extracted by 3 independent deterministic methods:
  M1 — Extractor key     : data["water"] from transformer_oil_extractor_v7
  M2 — Regex on raw text : pdfplumber full-text regex scan
  M3 — Table cell scan   : pdfplumber table row search

Final value = median of all values that parsed successfully (>=1 required).
Classification (BIS 1866-2017, 45 ppm limit):
  BAD   > 45 ppm
  GOOD  ≤ 45 ppm

PDF summary report generated on demand with reportlab.

Run:  streamlit run app_water.py
"""

import io, os, re, tempfile, statistics
import pdfplumber
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

from transformer_oil_extractor_v7 import parse_pdf

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

WATER_LIMIT   = 45.0

SEV_ICON  = {"BAD": "🔴", "GOOD": "🟢"}
SEV_ORDER = {"BAD": 0, "GOOD": 1, "ERR": 2}
SEV_COLOR = {"BAD": "#c0392b", "GOOD": "#27ae60"}
SEV_BG    = {"BAD": "#fff0f0", "GOOD": "#f0fff4"}

# ─────────────────────────────────────────────────────────────────────────────
# Triple-extraction logic
# ─────────────────────────────────────────────────────────────────────────────

_WATER_PATTERNS = [
    r"water\s+content[^\n]*?(\d+\.?\d*)\s*(?:mg[/\\]kg|ppm|mg/kg)?",
    r"moisture\s+content[^\n]*?(\d+\.?\d*)\s*(?:mg[/\\]kg|ppm|mg/kg)?",
    r"karl\s+fischer[^\n]*?(\d+\.?\d*)\s*(?:mg[/\\]kg|ppm|mg/kg)?",
    r"water\s+by\s+karl[^\n]*?(\d+\.?\d*)\s*(?:mg[/\\]kg|ppm)?",
]

_TABLE_KEYWORDS = [
    "water content", "moisture content", "water by karl", "karl fischer", "h2o content"
]


def _safe_parse(s):
    try:
        val = float(str(s).strip().replace(",", "."))
        return val if 0 < val < 200 else None
    except (ValueError, TypeError):
        return None


def extract_water_m1(data: dict):
    return _safe_parse(data.get("water"))


def extract_water_m2(pdf_path: str):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join((p.extract_text() or "") for p in pdf.pages).lower()
        for pat in _WATER_PATTERNS:
            m = re.search(pat, full_text, re.IGNORECASE)
            if m:
                val = _safe_parse(m.group(1))
                if val is not None:
                    return val
    except Exception:
        pass
    return None


def extract_water_m3(pdf_path: str):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        if not row:
                            continue
                        row_text = " ".join(str(c or "").lower() for c in row)
                        if any(kw in row_text for kw in _TABLE_KEYWORDS):
                            for cell in row:
                                val = _safe_parse(cell)
                                if val is not None:
                                    return val
    except Exception:
        pass
    return None


def triple_extract_water(data: dict, pdf_path: str) -> dict:
    m1 = extract_water_m1(data)
    m2 = extract_water_m2(pdf_path)
    m3 = extract_water_m3(pdf_path)

    valid = [val for val in [m1, m2, m3] if val is not None]
    if not valid:
        final  = None
        agreed = False
    else:
        final  = statistics.median(valid)
        agreed = (max(valid) - min(valid)) <= 2.0

    return dict(m1=m1, m2=m2, m3=m3, final=final, agreed=agreed, valid_count=len(valid))


def classify(water_ppm) -> str:
    if water_ppm is None:
        return "GOOD"
    if water_ppm > WATER_LIMIT:
        return "BAD"
    return "GOOD"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_float(val) -> float:
    try:
        s = str(val).strip().upper()
        return 0.0 if s in ("ND","NOT FOUND","","NS","NA") else float(s)
    except (TypeError, ValueError):
        return 0.0


def v(data, key, unit="") -> str:
    val = data.get(key, "")
    if not val or str(val).strip().upper() in ("ND","NOT FOUND","","NS","NA"):
        return "—"
    return f"{val} {unit}".strip()


def pct_bar_html(value: float, limit: float, color: str) -> str:
    pct = min(value / limit * 100, 100)
    return (
        f"<div style='background:#e0e0e0;border-radius:50px;height:14px;"
        f"width:100%;margin:10px 0 4px;overflow:hidden;'>"
        f"<div style='height:100%;width:{pct:.1f}%;background:{color};"
        f"border-radius:50px;'></div></div>"
        f"<div style='font-size:0.78rem;color:#888;'>{pct:.1f}% of {limit:.0f} ppm limit</div>"
    )


def _ost_status(data, key, lim_min, lim_max, direction) -> str:
    raw = data.get(key, "")
    if not raw or str(raw).strip().upper() in ("ND","NOT FOUND","","NS","NA"):
        return "—"
    try:
        fv = float(str(raw).strip())
    except (ValueError, TypeError):
        return "—"
    if direction == "min":
        return "PASS" if fv >= lim_min else "FAIL"
    else:
        return "PASS" if fv <= lim_max else "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# PDF report builder (reportlab)
# ─────────────────────────────────────────────────────────────────────────────

def sev_hex(sev):
    return {"BAD":"c0392b","GOOD":"27ae60"}.get(sev,"333333")

def icon_text(sev):
    return {"BAD":"[BAD]","GOOD":"[GOOD]"}.get(sev,"")

def _sev_rgb(sev):
    return {
        "BAD":  colors.HexColor("#c0392b"),
        "GOOD": colors.HexColor("#27ae60"),
    }.get(sev, colors.black)


def build_pdf_report(all_reports: list) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm,  bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()
    title_s = ParagraphStyle("Title2", parent=styles["Title"],
        fontSize=18, textColor=colors.HexColor("#0d3b6e"), spaceAfter=4)
    h1_s = ParagraphStyle("H1", parent=styles["Heading1"],
        fontSize=13, textColor=colors.HexColor("#0d3b6e"), spaceBefore=14, spaceAfter=4)
    h2_s = ParagraphStyle("H2", parent=styles["Heading2"],
        fontSize=10, textColor=colors.HexColor("#444"), spaceBefore=8, spaceAfter=3)
    normal_s = ParagraphStyle("N", parent=styles["Normal"], fontSize=9, leading=13)
    small_s  = ParagraphStyle("S", parent=styles["Normal"],
        fontSize=8, textColor=colors.HexColor("#666"), leading=11)

    story = []

    # Cover header
    story.append(Paragraph("Transformer Oil Analysis", title_s))
    story.append(Paragraph("Water Content Diagnostic Report", h1_s))
    story.append(Paragraph(
        "Triple-Check Water Monitor  |  BIS 1866-2017 limit: 45 ppm max", small_s))
    story.append(HRFlowable(width="100%", thickness=1.5,
                             color=colors.HexColor("#0d3b6e"), spaceAfter=12))

    # Summary table
    n_b = sum(1 for r in all_reports if r["severity"]=="BAD")
    n_g = sum(1 for r in all_reports if r["severity"]=="GOOD")

    summary_data = [
        ["Total Reports", "Bad (>45 ppm)", "Good (≤45 ppm)"],
        [str(len(all_reports)), str(n_b), str(n_g)],
    ]
    s_tbl = Table(summary_data, colWidths=[4*cm, 4.5*cm, 4.5*cm])
    s_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0),(-1,0), colors.HexColor("#0d3b6e")),
        ("TEXTCOLOR",  (0,0),(-1,0), colors.white),
        ("FONTNAME",   (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0),(-1,-1), 10),
        ("ALIGN",      (0,0),(-1,-1), "CENTER"),
        ("VALIGN",     (0,0),(-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#f0f4ff"),colors.white]),
        ("GRID",       (0,0),(-1,-1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0,0),(-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1), 7),
        ("TEXTCOLOR",  (1,1),(1,1), colors.HexColor("#c0392b")),
        ("TEXTCOLOR",  (2,1),(2,1), colors.HexColor("#27ae60")),
        ("FONTNAME",   (0,1),(-1,1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,1),(-1,1), 14),
    ]))
    story.append(s_tbl)
    story.append(Spacer(1, 16))

    # Per-report sections
    for rep in all_reports:
        if rep.get("error"):
            story.append(Paragraph(f"ERROR: {rep['name']} — {rep['error']}", normal_s))
            continue

        data   = rep["data"]
        sev    = rep["severity"]
        wd     = rep["water_data"]
        scolor = _sev_rgb(sev)
        final  = wd["final"]

        equip = data.get("equipment_designation") or data.get("css_name") or rep["name"]
        sp    = data.get("sampling_point") or "—"

        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color=colors.HexColor("#cccccc"), spaceBefore=6))
        story.append(Paragraph(
            f"<font color='#{sev_hex(sev)}'>{icon_text(sev)} {equip}</font>"
            f"  -  {sp}",
            h1_s,
        ))

        # Triple-check table
        story.append(Paragraph("Water Content — Triple Extraction", h2_s))
        w_display = f"{final:.1f} ppm" if final is not None else "N/D"
        pct_str   = f"{final/WATER_LIMIT*100:.1f}%" if final else "—"
        agreed_text = "All methods agree (within 2 ppm)" if wd["agreed"] else "Methods diverge — median used"

        def fmt_v(val):
            return f"{val:.1f} ppm" if val is not None else "Not detected"

        w_data = [
            ["Method", "Value", "Notes"],
            ["M1 — Extractor Key",   fmt_v(wd["m1"]), "transformer_oil_extractor_v7.parse_pdf()"],
            ["M2 — Regex Text Scan", fmt_v(wd["m2"]), "pdfplumber full-text regex"],
            ["M3 — Table Cell Scan", fmt_v(wd["m3"]), "pdfplumber table row lookup"],
            ["FINAL (median)",       w_display,        f"{pct_str} of 45 ppm  |  {agreed_text}"],
        ]
        w_tbl = Table(w_data, colWidths=[4.5*cm, 3.5*cm, 9*cm])
        agreed_color = colors.HexColor("#27ae60") if wd["agreed"] else colors.HexColor("#d35400")
        final_bg = (colors.HexColor("#f0fff4") if sev=="GOOD" else
                    colors.HexColor("#fff0f0"))
        w_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,0),  colors.HexColor("#1565c0")),
            ("TEXTCOLOR",     (0,0),(-1,0),  colors.white),
            ("FONTNAME",      (0,0),(-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8.5),
            ("ALIGN",         (1,0),(1,-1),  "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-2), [colors.HexColor("#eef4ff"),colors.white]),
            ("BACKGROUND",    (0,4),(-1,4),  final_bg),
            ("FONTNAME",      (0,4),(-1,4),  "Helvetica-Bold"),
            ("TEXTCOLOR",     (1,4),(1,4),   scolor),
            ("TEXTCOLOR",     (2,4),(2,4),   agreed_color),
            ("GRID",          (0,0),(-1,-1), 0.5, colors.HexColor("#cccccc")),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ]))
        story.append(w_tbl)
        story.append(Spacer(1, 6))

        # Verdict box
        verdict_bg = (colors.HexColor("#fff0f0") if sev=="BAD" else
                      colors.HexColor("#f0fff4"))
        v_tbl = Table([[Paragraph(
            f"<font color='#{sev_hex(sev)}'><b>{icon_text(sev)} {sev}</b></font>"
            f"  -  Final water: <b>{w_display}</b>  |  Limit: 45 ppm",
            normal_s,
        )]], colWidths=[17*cm])
        v_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), verdict_bg),
            ("LINEABOVE",     (0,0),(-1,0),  2, scolor),
            ("LEFTPADDING",   (0,0),(-1,-1), 10),
            ("TOPPADDING",    (0,0),(-1,-1), 7),
            ("BOTTOMPADDING", (0,0),(-1,-1), 7),
        ]))
        story.append(v_tbl)
        story.append(Spacer(1, 10))

        # Vendor recommendations
        story.append(Paragraph("Vendor Recommendations", h2_s))
        ost_rec = data.get("ost_recommendation") or "—"
        dga_rec = data.get("dga_recommendation") or "—"
        overall = data.get("recommendation")     or "—"
        rec_data = [
            ["OST",     Paragraph(ost_rec, normal_s)],
            ["DGA",     Paragraph(dga_rec, normal_s)],
            ["Overall", Paragraph(overall, normal_s)],
        ]
        r_tbl = Table(rec_data, colWidths=[2.5*cm, 14.5*cm])
        r_tbl.setStyle(TableStyle([
            ("FONTNAME",      (0,0),(0,-1),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8.5),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("ROWBACKGROUNDS",(0,0),(-1,-1),
             [colors.HexColor("#e3f2fd"),colors.HexColor("#e8f5e9"),colors.HexColor("#f3e5f5")]),
            ("GRID",          (0,0),(-1,-1), 0.4, colors.HexColor("#dddddd")),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ]))
        story.append(r_tbl)
        story.append(Spacer(1, 10))

        # OST results
        story.append(Paragraph("Oil Screening Tests (OST)", h2_s))
        ost_rows_pdf = [
            ["Parameter", "Result", "Limit", "Status"],
            ["BDV",                  v(data,"bdv","kV"),        "30 kV min",      _ost_status(data,"bdv",30,None,"min")],
            ["Water Content",        v(data,"water","ppm"),     "45 ppm max",     _ost_status(data,"water",None,45,"max")],
            ["IFT @ 27C",            v(data,"ift","N/m"),       "0.020 N/m min",  _ost_status(data,"ift",0.020,None,"min")],
            ["Neutralization Value", v(data,"neutralization"),  "0.3 max",        _ost_status(data,"neutralization",None,0.3,"max")],
            ["Density @ 29.5C",      v(data,"density"),         "0.890 max",      _ost_status(data,"density",None,0.890,"max")],
            ["Flash Point",          v(data,"flash","C"),       "125 C min",      _ost_status(data,"flash",125,None,"min")],
            ["OQI",                  v(data,"oqi"),             "45 min",         _ost_status(data,"oqi",45,None,"min")],
            ["tan-d @ 90C",          v(data,"tdf_90"),          "0.5 max",        _ost_status(data,"tdf_90",None,0.5,"max")],
            ["Sediment & Sludge",    v(data,"sludge","%"),      "< 0.1%",         _ost_status(data,"sludge",None,0.1,"max")],
        ]
        ost_t = Table(ost_rows_pdf, colWidths=[5*cm, 3.5*cm, 4.5*cm, 4*cm])
        ost_style = [
            ("BACKGROUND",    (0,0),(-1,0),  colors.HexColor("#1565c0")),
            ("TEXTCOLOR",     (0,0),(-1,0),  colors.white),
            ("FONTNAME",      (0,0),(-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8),
            ("ALIGN",         (2,0),(-1,-1), "CENTER"),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.HexColor("#f8faff"),colors.white]),
            ("BACKGROUND",    (0,2),(-1,2),  colors.HexColor("#e3f2fd")),
            ("FONTNAME",      (0,2),(-1,2),  "Helvetica-Bold"),
            ("GRID",          (0,0),(-1,-1), 0.4, colors.HexColor("#cccccc")),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ]
        for ri, row in enumerate(ost_rows_pdf[1:], start=1):
            status = row[3]
            if status == "PASS":
                ost_style += [
                    ("TEXTCOLOR",(3,ri),(3,ri),colors.HexColor("#27ae60")),
                    ("FONTNAME", (3,ri),(3,ri),"Helvetica-Bold"),
                ]
            elif status == "FAIL":
                ost_style += [
                    ("TEXTCOLOR",(3,ri),(3,ri),colors.HexColor("#c0392b")),
                    ("FONTNAME", (3,ri),(3,ri),"Helvetica-Bold"),
                ]
        ost_t.setStyle(TableStyle(ost_style))
        story.append(ost_t)
        story.append(Spacer(1, 10))

        # Equipment info
        story.append(Paragraph("Equipment Details", h2_s))
        eq_data = [
            ["Equipment",      equip,                          "Manufacturer",  data.get("manufacturer","—")],
            ["Sampling Point", sp,                             "Voltage Class", data.get("voltage_class","—")],
            ["Report No.",     data.get("report_no","—"),      "Report Date",   data.get("report_date","—")],
            ["Voltage Ratio",  data.get("voltage_ratio","—"),  "Sample Date",   data.get("sampling_date","—")],
            ["Reason",         data.get("reason_for_sampling","—"), "Weather",  data.get("weather_condition","—")],
        ]
        eq_t = Table(eq_data, colWidths=[3.5*cm, 5*cm, 3.5*cm, 5*cm])
        eq_t.setStyle(TableStyle([
            ("FONTNAME",      (0,0),(0,-1),  "Helvetica-Bold"),
            ("FONTNAME",      (2,0),(2,-1),  "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 8),
            ("VALIGN",        (0,0),(-1,-1), "TOP"),
            ("ROWBACKGROUNDS",(0,0),(-1,-1), [colors.HexColor("#f0f4ff"),colors.white]),
            ("GRID",          (0,0),(-1,-1), 0.4, colors.HexColor("#dddddd")),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 6),
        ]))
        story.append(eq_t)
        story.append(Spacer(1, 4))

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# OST table for Streamlit UI
# ─────────────────────────────────────────────────────────────────────────────

OST_ROWS_UI = [
    ("BDV",                  "bdv",            "kV",         30,    None,  "min"),
    ("Water Content",        "water",          "ppm",        None,  45,    "max"),
    ("IFT @ 27 C",           "ift",            "N/m",        0.020, None,  "min"),
    ("Neutralization Value", "neutralization", "mgKOH/g",    None,  0.3,   "max"),
    ("Density @ 29.5 C",     "density",        "g/cm3",      None,  0.890, "max"),
    ("Color (ASTM)",         "color",          "",           None,  7.0,   "max"),
    ("Flash Point",          "flash",          "C",          125,   None,  "min"),
    ("OQI",                  "oqi",            "",           45,    None,  "min"),
    ("tan-d @ 90 C",         "tdf_90",         "",           None,  0.5,   "max"),
    ("Sp. Res @27 C",        "sp_res_27",      "x10^12 Ohm", 0.4,   None,  "min"),
    ("Sp. Res @90 C",        "sp_res_90",      "x10^12 Ohm", 0.02,  None,  "min"),
    ("Sediment & Sludge",    "sludge",         "%",          None,  0.1,   "max"),
]

def render_ost_table_html(data: dict) -> str:
    rows = ""
    for label, key, unit, lim_min, lim_max, direction in OST_ROWS_UI:
        raw  = data.get(key, "")
        disp = "—" if not raw or str(raw).strip().upper() in ("ND","NOT FOUND","","NS","NA") \
               else f"{raw} {unit}".strip()
        limit_val = lim_min if direction == "min" else lim_max
        status, css, icon = "—", "color:#aaa", "·"
        if limit_val is not None and raw and str(raw).strip().upper() not in ("ND","NS","NA",""):
            try:
                fv = float(str(raw).strip())
                ok = (fv >= limit_val) if direction == "min" else (fv <= limit_val)
                status = "PASS" if ok else "FAIL"
                css    = "color:#27ae60;font-weight:700" if ok else "color:#c0392b;font-weight:700"
                icon   = "✔" if ok else "✘"
            except (ValueError, TypeError):
                pass
        highlight = "background:#e3f2fd;" if key == "water" else ""
        bold_lbl  = "font-weight:700" if key == "water" else "font-weight:400"
        rows += (
            f"<tr style='{highlight}'>"
            f"<td style='padding:5px 8px;color:#444;{bold_lbl}'>{label}</td>"
            f"<td style='padding:5px 8px;font-weight:700;text-align:right'>{disp}</td>"
            f"<td style='padding:5px 8px;text-align:center;{css}'>{icon} {status}</td>"
            f"</tr>"
        )
    return (
        "<table style='width:100%;border-collapse:collapse;font-size:0.87rem;'>"
        "<thead><tr style='background:#1565c0;color:#fff;'>"
        "<th style='padding:6px 8px;text-align:left;font-size:0.72rem;letter-spacing:.07em;text-transform:uppercase;'>Parameter</th>"
        "<th style='padding:6px 8px;text-align:right;font-size:0.72rem;letter-spacing:.07em;text-transform:uppercase;'>Value</th>"
        "<th style='padding:6px 8px;text-align:center;font-size:0.72rem;letter-spacing:.07em;text-transform:uppercase;'>Status</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Page config + CSS
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Water Content Monitor · Good / Bad",
    page_icon="💧",
    layout="wide",
)

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
.mc-total    { background:#e8eaf6; color:#1a237e; }
.mc-bad      { background:#ffebee; color:#b71c1c; }
.mc-good     { background:#e8f5e9; color:#1b5e20; }
.mc-err      { background:#f5f5f5; color:#555;    }

.sev-header { border-radius:10px; padding:.75rem 1.4rem;
              margin:1.2rem 0 .6rem; font-size:1.1rem; font-weight:700; }
.sev-bad      { background:linear-gradient(90deg,#c0392b,#e74c3c); color:#fff; }
.sev-good     { background:linear-gradient(90deg,#27ae60,#2ecc71); color:#fff; }

.verdict-banner { border-radius:8px; padding:.7rem 1.1rem; margin-bottom:.8rem;
                  font-size:1rem; font-weight:700; display:flex; align-items:center; gap:.5rem; }
.verdict-bad      { background:#fff0f0; border:2px solid #c0392b; color:#c0392b; }
.verdict-good     { background:#f0fff4; border:2px solid #27ae60; color:#27ae60; }

.triple-card { border-radius:10px; padding:1rem 1.2rem; margin-bottom:.6rem;
               box-shadow:0 1px 6px rgba(0,0,0,.08); }
.triple-method { font-size:.7rem; font-weight:700; text-transform:uppercase;
                 letter-spacing:.1em; opacity:.65; margin-bottom:4px; }
.triple-val  { font-size:1.6rem; font-weight:800; line-height:1.1; }
.triple-unit { font-size:.85rem; opacity:.7; margin-left:3px; }
.triple-note { font-size:.75rem; opacity:.55; margin-top:3px; }

.agree-badge { display:inline-block; padding:3px 10px; border-radius:20px;
               font-size:.75rem; font-weight:700; margin-top:8px; }
.agree-yes { background:#e8f5e9; color:#2e7d32; border:1px solid #a5d6a7; }
.agree-no  { background:#fff8e1; color:#f57f17; border:1px solid #ffe082; }

.section-label { font-size:.72rem; font-weight:700; text-transform:uppercase;
                 letter-spacing:.1em; color:#888; margin:1.1rem 0 .4rem;
                 padding-bottom:.3rem; border-bottom:1px solid #e8e8e8; }

.rec-box { border-radius:8px; padding:.8rem 1.1rem; margin-bottom:.5rem;
           font-size:.9rem; line-height:1.55; }
.rec-box strong { display:block; margin-bottom:.3rem; font-size:.7rem;
                  text-transform:uppercase; letter-spacing:.07em; opacity:.7; }
.rec-ost     { background:#e3f2fd; border-left:4px solid #1565c0; color:#0d2a5e; }
.rec-dga     { background:#e8f5e9; border-left:4px solid #27ae60; color:#1a4a2a; }
.rec-overall { background:#f3e5f5; border-left:4px solid #8e24aa; color:#3e0056; }
.rec-warn    { background:#fff8e6; border-left:4px solid #f39c12; color:#5c3a00; }

[data-testid="stMetricValue"] { font-size:1rem !important; }
[data-testid="stMetricLabel"] { font-size:.74rem !important; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Hero
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<div class="hero-banner">
  <h1>💧 Transformer Oil — Water Content Monitor</h1>
  <p>
    Triple-check extraction &nbsp;·&nbsp;
    M1: extractor key &nbsp;|&nbsp; M2: regex text scan &nbsp;|&nbsp; M3: table cell scan
    &nbsp;·&nbsp; Final = median of valid methods
    &nbsp;·&nbsp; BIS 1866-2017 limit: <strong>45 ppm</strong>
  </p>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Upload
# ─────────────────────────────────────────────────────────────────────────────

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
      <div style="font-size:1.1rem;font-weight:600;">Drop transformer oil report PDFs here</div>
      <div style="font-size:.88rem;margin-top:.4rem;opacity:.7;">
        Water content extracted by 3 independent methods and cross-checked automatically
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# Parse + classify
# ─────────────────────────────────────────────────────────────────────────────

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
            data       = parse_pdf(tmp_path)
            water_data = triple_extract_water(data, tmp_path)
            severity   = classify(water_data["final"])
            reports[key] = dict(
                name=pdf_file.name, data=data,
                severity=severity, water_data=water_data,
                error=None,
            )
        except Exception as exc:
            reports[key] = dict(
                name=pdf_file.name, data={},
                severity="ERR",
                water_data=dict(m1=None,m2=None,m3=None,final=None,agreed=False,valid_count=0),
                error=str(exc),
            )
        finally:
            os.unlink(tmp_path)
    progress.progress((i + 1) / len(pdfs), text=f"Parsed {i+1}/{len(pdfs)} …")

progress.empty()

all_reports = sorted(
    reports.values(),
    key=lambda r: (SEV_ORDER.get(r["severity"], 99), -(r["water_data"]["final"] or 0)),
)

# ─────────────────────────────────────────────────────────────────────────────
# Summary cards
# ─────────────────────────────────────────────────────────────────────────────

n_total = len(all_reports)
n_b  = sum(1 for r in all_reports if r["severity"]=="BAD")
n_g  = sum(1 for r in all_reports if r["severity"]=="GOOD")
n_e  = sum(1 for r in all_reports if r["severity"]=="ERR")

err_card = (f"<div class='metric-card mc-err'><div class='mc-num'>{n_e}</div>"
            f"<div class='mc-lbl'>Errors</div></div>") if n_e else ""

st.markdown(f"""
<div class="metric-row">
  <div class="metric-card mc-total">
    <div class="mc-num">{n_total}</div><div class="mc-lbl">Total</div></div>
  <div class="metric-card mc-bad">
    <div class="mc-num">{n_b}</div><div class="mc-lbl">Bad</div></div>
  <div class="metric-card mc-good">
    <div class="mc-num">{n_g}</div><div class="mc-lbl">Good</div></div>
  {err_card}
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Card renderer
# ─────────────────────────────────────────────────────────────────────────────

def render_card(rep: dict):
    data  = rep["data"]
    sev   = rep["severity"]
    wd    = rep["water_data"]
    final = wd["final"]

    if rep.get("error"):
        st.error(f"Parse error — **{rep['name']}**: {rep['error']}")
        return

    equip = data.get("equipment_designation") or data.get("css_name") or rep["name"]
    sp    = data.get("sampling_point") or "—"
    label = f"{SEV_ICON[sev]}  {equip}  ·  {sp}  ·  {rep['name']}"

    with st.expander(label, expanded=(sev == "BAD")):

        # Verdict
        vc    = sev.lower()
        w_d   = f"{final:.1f} ppm" if final else "N/D"
        pct_v = f"{final/WATER_LIMIT*100:.1f}%" if final else "—"
        st.markdown(
            f"<div class='verdict-banner verdict-{vc}'>"
            f"{SEV_ICON[sev]} <strong>{sev}</strong>"
            f"&nbsp;—&nbsp; Water Content: <strong>{w_d}</strong>"
            f"&nbsp;|&nbsp; {pct_v} of 45 ppm limit"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Triple-check cards
        st.markdown("<div class='section-label'>💧 Water Content — Triple Extraction</div>",
                    unsafe_allow_html=True)

        methods = [
            ("M1", "Extractor Key",   "transformer_oil_extractor_v7", wd["m1"]),
            ("M2", "Regex Text Scan", "pdfplumber full-text regex",    wd["m2"]),
            ("M3", "Table Cell Scan", "pdfplumber table row lookup",   wd["m3"]),
        ]
        c1, c2, c3 = st.columns(3)
        for col, (badge, name, note, val) in zip([c1,c2,c3], methods):
            vstr  = f"{val:.1f}" if val is not None else "N/D"
            vcol  = SEV_COLOR[classify(val)] if val is not None else "#aaa"
            with col:
                st.markdown(
                    f"<div class='triple-card' style='background:#f8faff;border:1px solid #dde;'>"
                    f"<div class='triple-method'>{badge} — {name}</div>"
                    f"<div class='triple-val' style='color:{vcol};'>{vstr}"
                    f"<span class='triple-unit'>ppm</span></div>"
                    f"<div class='triple-note'>{note}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # Consensus
        agree_cls  = "agree-yes" if wd["agreed"] else "agree-no"
        agree_text = (
            f"All methods agree (within 2 ppm)  —  Final: {w_d}"
            if wd["agreed"] else
            f"Methods diverge — median used  —  Final: {w_d}"
        )
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:12px;margin:.4rem 0 .8rem;'>"
            f"  <span class='agree-badge {agree_cls}'>{agree_text}</span>"
            f"  <span style='font-size:.78rem;color:#888;'>Valid: {wd['valid_count']}/3 methods</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if final:
            st.markdown(pct_bar_html(final, WATER_LIMIT, SEV_COLOR[sev]), unsafe_allow_html=True)

        st.divider()

        # Vendor recommendations
        st.markdown("<div class='section-label'>📋 Vendor Recommendations</div>",
                    unsafe_allow_html=True)
        ost_rec = data.get("ost_recommendation") or "—"
        dga_rec = data.get("dga_recommendation") or "—"
        overall = data.get("recommendation")     or "—"
        is_oltc = "OLTC" in str(data.get("sampling_point","")).upper()

        ra, rb, rc = st.columns(3)
        with ra:
            st.markdown(
                f"<div class='rec-box rec-ost'><strong>OST Recommendation</strong>{ost_rec}</div>",
                unsafe_allow_html=True,
            )
        with rb:
            is_ns = is_oltc and "not specified" in dga_rec.lower()
            d_cls = "rec-warn" if is_ns else "rec-dga"
            d_txt = dga_rec + (" <em>(Limits NS — OLTC)</em>" if is_ns else "")
            st.markdown(
                f"<div class='rec-box {d_cls}'><strong>DGA Recommendation</strong>{d_txt}</div>",
                unsafe_allow_html=True,
            )
        with rc:
            st.markdown(
                f"<div class='rec-box rec-overall'><strong>Overall Recommendation</strong>{overall}</div>",
                unsafe_allow_html=True,
            )

        # Full details collapsed
        with st.expander("🔍 Full Report Details", expanded=False):
            st.markdown("<div class='section-label'>🏭 Equipment Identity</div>",
                        unsafe_allow_html=True)
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Equipment",       v(data,"equipment_designation") or equip)
            c2.metric("Owner / CSS",     v(data,"css_name") if v(data,"css_name")!="—" else v(data,"owner"))
            c3.metric("Manufacturer",    v(data,"manufacturer"))
            c4.metric("Mfr. Serial No.", data.get("mfr_serial_no","—") or "—")
            c1b,c2b,c3b,c4b = st.columns(4)
            c1b.metric("Rating",         v(data,"rating"))
            c2b.metric("Voltage Class",  v(data,"voltage_class"))
            c3b.metric("Voltage Ratio",  v(data,"voltage_ratio"))
            c4b.metric("Sampling Point", v(data,"sampling_point"))

            st.markdown("<div class='section-label'>📋 Report Metadata</div>",
                        unsafe_allow_html=True)
            m1,m2,m3,m4 = st.columns(4)
            m1.metric("Report No.",  v(data,"report_no"))
            m2.metric("Report Date", v(data,"report_date"))
            m3.metric("Sample Date", v(data,"sampling_date"))
            m4.metric("Reason",      v(data,"reason_for_sampling"))
            m1b,m2b,m3b,m4b = st.columns(4)
            m1b.metric("Weather",      v(data,"weather_condition"))
            m2b.metric("Oil Temp",     v(data,"oil_temperature","C"))
            m3b.metric("Winding Temp", v(data,"winding_temperature","C"))
            m4b.metric("Condition",    v(data,"condition_on_receipt"))

            st.markdown("<div class='section-label'>🧪 Full OST Results</div>",
                        unsafe_allow_html=True)
            st.markdown(render_ost_table_html(data), unsafe_allow_html=True)

            st.markdown("<div class='section-label'>🔬 DGA Gas Values</div>",
                        unsafe_allow_html=True)
            dga_keys = [
                ("H2","h2"),("O2","o2"),("N2","n2"),
                ("CO","co"),("CH4","ch4"),("CO2","co2"),
                ("C2H2","c2h2"),("C2H4","c2h4"),("C2H6","c2h6"),
                ("C3H6","c3h6"),("C3H8","c3h8"),
                ("TDCG","tdcg"),("TGC","tgc"),
            ]
            dc1,dc2,dc3,dc4 = st.columns(4)
            dcs = [dc1,dc2,dc3,dc4]
            for idx,(lbl,key) in enumerate(dga_keys):
                dcs[idx%4].metric(lbl, v(data,key,"ppm"))

            with st.expander("🗂 Raw JSON", expanded=False):
                st.json(data)


# ─────────────────────────────────────────────────────────────────────────────
# Grouped sections
# ─────────────────────────────────────────────────────────────────────────────

TIERS = [
    ("BAD",  "BAD  — Exceeds 45 ppm limit",       "bad"),
    ("GOOD", "GOOD — Within safe range (≤45 ppm)", "good"),
]

for tier, label_str, css in TIERS:
    group = [r for r in all_reports if r["severity"]==tier]
    if not group:
        continue
    st.markdown(
        f"<div class='sev-header sev-{css}'>"
        f"{SEV_ICON[tier]}  {label_str}  ({len(group)})"
        f"</div>",
        unsafe_allow_html=True,
    )
    for rep in group:
        render_card(rep)

err_group = [r for r in all_reports if r["severity"]=="ERR"]
if err_group:
    st.markdown("### Parse Errors")
    for rep in err_group:
        st.error(f"**{rep['name']}** — {rep.get('error','unknown error')}")

# ─────────────────────────────────────────────────────────────────────────────
# PDF export
# ─────────────────────────────────────────────────────────────────────────────

st.divider()
st.markdown("### 📄 Export Summary Report")

valid_reports = [r for r in all_reports if not r.get("error")]

if not valid_reports:
    st.info("No valid reports to export.")
else:
    if st.button("Generate PDF Report", type="primary"):
        with st.spinner("Building PDF …"):
            pdf_bytes = build_pdf_report(valid_reports)
        st.download_button(
            label="Download PDF",
            data=pdf_bytes,
            file_name="transformer_water_content_report.pdf",
            mime="application/pdf",
        )
