"""
transformer_oil_extractor_v7.py
================================
Parses Transformer Oil DGA PDF reports (TRU-FIL and SGS/CPRI formats).

Public API
----------
    from transformer_oil_extractor_v7 import parse_pdf
    data = parse_pdf("report.pdf")   # returns a flat dict of all fields
"""

import os
import re

# ─────────────────────────────────────────────────────────────────────────────
# All keys guaranteed in returned dict
# ─────────────────────────────────────────────────────────────────────────────

ALL_KEYS = [
    "fmt", "owner", "css_name", "installation_location",
    "equipment_designation", "equipment_type", "transformer_no",
    "manufacturer", "manufacturer_slno", "rating", "voltage_class",
    "voltage_ratio", "cooling", "manufacturing_year", "oil_type",
    "report_no", "sample_id", "report_date", "sampling_date",
    "weather_condition", "sampling_point",
    "bdv", "water", "color", "density",
    "sp_res_27", "sp_res_90", "ddf_27", "ddf_90",
    "ift", "neutralization", "sediment", "flash", "oqi",
    "h2", "o2", "n2", "co", "ch4", "co2",
    "c2h4", "c2h6", "c2h2", "c3h6", "c3h8",
    "tdcg", "tgc", "tdcg_ratio",
    "ost_recommendation", "dga_recommendation", "recommendation",
]


def _ev(text, pattern, default="ND", flags=re.IGNORECASE | re.DOTALL):
    try:
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else default
    except Exception:
        return default


def _detect_format(text: str) -> str:
    if re.search(r"IS\s+6792|IS\s+9434|IS\s+6103", text):
        return "TRUFIL"
    if re.search(r"Owner\s+\S|H₂|µl/l|Installation Location", text):
        return "SGS"
    return "TRUFIL"


# ─────────────────────────────────────────────────────────────────────────────
# TRU-FIL parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_trufil(full: str) -> dict:
    d = {"fmt": "TRU-FIL"}

    m = re.search(r"Equipment Designation\s+([^\n]+)", full, re.IGNORECASE)
    eq = m.group(1).strip() if m else ""
    m2 = re.search(r"(.*?)\s*/\s*(TR[#\-\d\w]+)", eq, re.IGNORECASE)
    if not m2:
        m2 = re.search(r"(.*?)\s*/\s*([A-Z]{2}[#\-]?\d+)", eq)
    if m2:
        d["css_name"]       = re.sub(r"S/[Ss]", "", m2.group(1)).strip()
        d["transformer_no"] = m2.group(2).strip()
    else:
        d["css_name"]       = eq
        d["transformer_no"] = ""
    d["equipment_designation"] = eq

    d["owner"]             = _ev(full, r"Owner\s+([^\n]+)")
    d["installation_location"] = _ev(full, r"Installation Location\s+([^\n]+)")
    d["equipment_type"]    = _ev(full, r"Equipment Type\s+([^\n]+)")
    d["sampling_point"]    = _ev(full, r"Sampling Point\s+([^\n]+)")
    d["report_date"]       = _ev(full, r"Report Date\s+([\d][\d\-/]+\d)")
    d["sampling_date"]     = _ev(full, r"Sampling Date\s+([\d][\d\-/]+\d)")
    d["manufacturer"]      = _ev(full, r"Manufacturer\s+([^\n]+)", default="")
    d["manufacturer_slno"] = _ev(full, r"Manufacturer'?s?\s+Sl\.?\s*No\.?\s+(\S+)", default="")
    d["rating"]            = _ev(full, r"Rating\s+([\d,]+\s*KVA)", default="")
    d["voltage_class"]     = _ev(full, r"Voltage Class\s+([\d]+\s*KV)", default="")
    d["voltage_ratio"]     = _ev(full, r"Voltage Ratio[^\n]*?\s+([\d/ ]+VOLT)", default="")
    d["cooling"]           = _ev(full, r"Cooling\s+([^\n]+)")
    d["manufacturing_year"]= _ev(full, r"Manufacturing Year\s+([^\n]+)")
    d["oil_type"]          = _ev(full, r"Insulating Fluid\s+([^\n]+)", default="")
    d["report_no"]         = _ev(full, r"Oil Test Report\s*[-–]\s*([A-Z0-9/\-]+)")
    d["sample_id"]         = _ev(full, r"Our Sample ID\s+([A-Z0-9\-]+)")
    d["weather_condition"] = _ev(full, r"Weather condition\s+([^\n]+)")

    # OST
    d["bdv"]            = _ev(full, r"Electric Strength[^K]+KV[^I]+IS\s*6792\s+([\d.]+)")
    d["water"]          = _ev(full, r"Water Content[^m]+mg.KG[^\n]+IS\s*13567\s+([\d.]+)")
    d["color"]          = _ev(full, r"Visual Appearance\s*-\s*Color[^\n]+(L\s*[\d.]+)")
    d["density"]        = _ev(full, r"Density[^g]+g.cm[^\n]+IS\s*1448[^\n]+([\d.]+)\s+[\d.]")
    d["sp_res_27"]      = _ev(full, r"Sp\.\s*Resistance at 27[^I]+IS\s*6103\s+([\d.]+)")
    d["sp_res_90"]      = _ev(full, r"Specific Resistance\s*.90[^I]+IS\s*6103\s+([\d.]+)")
    d["ddf_27"]         = _ev(full, r"Dissipation Factor[^\n]+27\s*C[^I]+IS\s*6262\s+([\d.]+)")
    d["ddf_90"]         = _ev(full, r"Dissipation Factor[^\n]+90\s*C[^I]+IS\s*6262\s+([\d.]+)")
    d["ift"]            = _ev(full, r"Interfacial Tension[^N]+N.m[^I]+IS\s*6104\s+([\d.]+)")
    d["neutralization"] = _ev(full, r"Neutralization Value[^I]+IEC\s*62021[^\n]+([\d.]+)")
    d["sediment"]       = _ev(full, r"Sediment and Sludge[^A]+Annex[^\n]+([\d.]+)")
    d["flash"]          = _ev(full, r"Flash Point[^I]+IS\s*1448[^\n]+([\d]+)")
    d["oqi"]            = _ev(full, r"Oil Quality Index[^\-]+\-[^\-]+\-\s+([\d]+)")

    # DGA — IS 9434 pattern
    d["h2"]   = _ev(full, r"Hydrogen[^\n]+IS 9434\s+([\d.]+)")
    d["o2"]   = _ev(full, r"Oxygen[^\n]+IS 9434\s+([\d.]+)")
    d["n2"]   = _ev(full, r"Nitrogen[^\n]+IS 9434\s+([\d.]+)")
    d["co"]   = _ev(full, r"Carbon Monoxide[^\n]+IS 9434\s+([\d.]+)")
    d["ch4"]  = _ev(full, r"Methane[^\n]+IS 9434\s+([\d.]+)")
    d["co2"]  = _ev(full, r"Carbon Dioxide[^\n]+IS 9434\s+([\d.]+)")
    d["c2h4"] = _ev(full, r"Ethylene[^\n]+IS 9434\s+([\d.]+)")
    d["c2h6"] = _ev(full, r"Ethane[^\n]+IS 9434\s+([\d.]+)")
    d["c2h2"] = _ev(full, r"Acetylene[^\n]+IS 9434\s+([\d.]+|ND)")
    d["c3h6"] = _ev(full, r"Propylene[^\n]+IS 9434\s+([\d.]+|ND)")
    d["c3h8"] = _ev(full, r"Propane[^\n]+IS 9434\s+([\d.]+|ND)")
    d["tdcg"] = _ev(full, r"Total Dissolved Combustible[^\n]+IS 9434\s+([\d.]+)")
    d["tgc"]  = _ev(full, r"Total Gas Content[^%\n]+IS 9434\s+([\d.]+)")
    d["tdcg_ratio"] = _ev(full, r"TDCG/TGC[^\n]+IS 9434\s+([\d.]+)")

    # Recommendations — grab the full line for each
    m_ost = re.search(r"OST Test Recommendation\s+(.+)", full, re.IGNORECASE)
    d["ost_recommendation"] = m_ost.group(1).strip() if m_ost else ""

    m_dga = re.search(r"DGA Test Recommendation\s+(.+)", full, re.IGNORECASE)
    d["dga_recommendation"] = m_dga.group(1).strip() if m_dga else ""

    # Overall — can span onto next line; stop at blank line or "Oil Test Report"
    m_ovr = re.search(
        r"Overall Recommendations?:\s*(.+?)(?:\n\n|\nOil Test Report|\Z)",
        full, re.IGNORECASE | re.DOTALL
    )
    d["recommendation"] = " ".join(m_ovr.group(1).split()) if m_ovr else ""

    for k in ("owner", "installation_location", "equipment_type",
              "manufacturer_slno", "voltage_ratio", "cooling",
              "manufacturing_year", "weather_condition",
              "sample_id", "report_no", "tdcg_ratio", "sampling_point"):
        d.setdefault(k, "")

    return d


# ─────────────────────────────────────────────────────────────────────────────
# SGS / CPRI parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_sgs(full: str) -> dict:
    d = {"fmt": "SGS/CPRI"}

    d["owner"]                 = _ev(full, r"Owner\s+([^\n]+)")
    d["installation_location"] = _ev(full, r"Installation Location\s+([^\n]+)")
    d["equipment_designation"] = _ev(full, r"Equipment Designation\s+([^\n]+)")
    d["equipment_type"]        = _ev(full, r"Equipment Type\s+([^\n]+)")
    d["sampling_point"]        = _ev(full, r"Sampling Point\s+([^\n]+)")
    d["transformer_no"]        = _ev(full, r"(?:Equipment Serial No|Manufacturer'?s?\s+Sl\.?\s*No\.?)\s*[:\s]+([^\n]+)", default="")
    d["manufacturer_slno"]     = _ev(full, r"Manufacturer'?s?\s+Sl\.?\s*No\.\s+([^\n]+)")
    d["manufacturer"]          = _ev(full, r"Equipment Make\s*:\s*([^\n]+)", default="")
    d["rating"]                = _ev(full, r"Power Rating\s*:\s*([^\n]+)", default="")
    d["voltage_class"]         = _ev(full, r"Voltage Rating\s*:\s*([^\n]+)", default="")
    d["voltage_ratio"]         = _ev(full, r"Voltage Ratio[^\n]*?\s+([0-9 /]+VOLT)", default="")
    d["cooling"]               = _ev(full, r"Cooling\s+([^\n]+)")
    d["manufacturing_year"]    = _ev(full, r"Manufacturing Year\s+([^\n]+)")
    d["oil_type"]              = _ev(full, r"Product Name\s*:\s*([^\n]+)", default="")
    d["weather_condition"]     = _ev(full, r"Weather [Cc]ondition\s+([^\n]+)")
    d["sample_id"]             = _ev(full, r"Our Sample ID\s+([A-Z0-9\-]+)")
    d["report_no"]             = _ev(full, r"Oil Test Report\s*[-–]\s*([A-Z0-9/\-]+)")
    d["report_date"]           = _ev(full, r"Analysis Date\s*[:\s]+([0-9/\-]+)")
    d["sampling_date"]         = _ev(full, r"Sampling Date\s*[:\s]+([0-9/\-]+)")
    d["css_name"] = d["equipment_designation"]

    d["bdv"] = (
        _ev(full, r"Electric Strength \(BDV\)[^\n]*?([0-9.]+)\s+30") or
        _ev(full, r"Electric Strength \(Breakdown Voltage\)[^\n]*?([0-9.]+)\s+(?:Good|30)", default="ND")
    )
    d["water"] = (
        _ev(full, r"Water Content By Karl Fischer Method[^\n]*?([0-9.]+)\s+40") or
        _ev(full, r"Water Content[^\n]*?mg/kg[^\n]*?([0-9.]+)\s+(?:Good|40)", default="ND")
    )
    d["neutralization"] = _ev(full, r"Neutralization Value[^\n]*?([0-9.]+)\s+0\.3")
    if d["neutralization"] == "ND":
        d["neutralization"] = _ev(full, r"Neutralization Value[^\n]*?([0-9.]+)\s+Good")
    d["ift"] = _ev(full, r"Interfacial Tension[^\n]*?([0-9.]+)\s+0\.020")
    if d["ift"] == "ND":
        d["ift"] = _ev(full, r"Interfacial Tension[^\n]*?([0-9.]+)\s+Good")
    d["ddf_90"]    = _ev(full, r"Dielectric Dissipation Factor[^\n]*?90[°o]?C[^\n]*?([0-9.]+)\s+(?:Good|0\.[0-9]+)")
    d["ddf_27"]    = _ev(full, r"Dielectric Dissipation Factor[^\n]*?(?:27[°o]?C|RT)[^\n]*?([0-9.]+)")
    d["sp_res_90"] = _ev(full, r"Specific Resistance[^\n]*?90[°o]?C[^\n]*?([0-9.]+)\s+(?:Good|[0-9.]+)")
    d["sp_res_27"] = _ev(full, r"Specific Resistance[^\n]*?(?:27[°o]?C|RT)[^\n]*?([0-9.]+)")
    d["sediment"]  = _ev(full, r"Sediment[^\n]*?(Not Detected|[0-9.]+)")
    d["flash"]     = _ev(full, r"Flash Point[^\n]*?([0-9]+)")
    d["density"]   = (
        _ev(full, r"Density\s*@[^\n]*?([0-9.]+)\s+0\.890") or
        _ev(full, r"Density[^\n]*?g/ml\s*([0-9.]+)", default="ND")
    )

    def dga(p): return _ev(full, p, default="ND")
    d["h2"]        = dga(r"Hydrogen\s*\(H[₂2]\)[^\n]*?([0-9.]+)\s+50")
    d["o2"]        = dga(r"Oxygen\s*\(O[₂2]\)[^\n]*?([0-9.]+)\s+NS")
    d["n2"]        = dga(r"Nitrogen\s*\(N[₂2]\)[^\n]*?([0-9.]+)\s+NS")
    d["co"]        = dga(r"Carbon Monoxide\s*\(CO\)[^\n]*?([0-9.]+)\s+400")
    d["ch4"]       = dga(r"Methane\s*\(CH[₄4]\)[^\n]*?([0-9.]+)\s+30")
    d["co2"]       = dga(r"Carbon Dioxide\s*\(CO[₂2]\)[^\n]*?([0-9.]+)\s+3800")
    d["c2h4"]      = dga(r"Ethylene\s*\(C[₂2]H[₄4]\)[^\n]*?([0-9.]+)\s+60")
    d["c2h6"]      = dga(r"Ethane\s*\(C[₂2]H[₆6]\)[^\n]*?([0-9.]+)\s+20")
    d["c2h2"]      = dga(r"Acetylene\s*\(C[₂2]H[₂2]\)[^\n]*?(ND|[0-9.]+)\s+2")
    d["c3h6"]      = dga(r"Propylene\s*\(C[₃3]H[₆6]\)[^\n]*?(ND|[0-9.]+)\s+NS")
    d["c3h8"]      = dga(r"Propane\s*\(C[₃3]H[₈8]\)[^\n]*?(ND|[0-9.]+)\s+NS")
    d["tdcg"]      = dga(r"Total Dissolved Combustible[^\n]*?([0-9.]+)\s+NS")
    d["tgc"]       = dga(r"Total Gas Content\s*\(TGC\)[^\n]*?([0-9.]+)\s+NS")
    d["tdcg_ratio"]= dga(r"TDCG/TGC[^\n]*?([0-9.]+)\s+8%")

    m_ost = re.search(r"OST Test Recommendation\s+(.+)", full, re.IGNORECASE)
    d["ost_recommendation"] = m_ost.group(1).strip() if m_ost else ""

    m_dga = re.search(r"DGA Test Recommendation\s+(.+)", full, re.IGNORECASE)
    d["dga_recommendation"] = m_dga.group(1).strip() if m_dga else ""

    m_ovr = re.search(
        r"Overall Recommendations?:\s*(.+?)(?:\n\n|\nOil Test Report|\Z)",
        full, re.IGNORECASE | re.DOTALL
    )
    d["recommendation"] = " ".join(m_ovr.group(1).split()) if m_ovr else ""

    for k in ("color", "oqi"):
        d.setdefault(k, "")

    return d


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def parse_pdf(path: str) -> dict:
    """Parse a transformer oil DGA PDF and return a flat dict."""
    import pdfplumber

    full = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                full += t + "\n"

    fmt = _detect_format(full)
    d = _parse_trufil(full) if fmt == "TRUFIL" else _parse_sgs(full)

    for k in ALL_KEYS:
        d.setdefault(k, "")

    return d