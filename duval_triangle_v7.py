"""
duval_triangle_v7.py
=====================
Generates a Duval Triangle plot (IEC 60599) from DGA data.

Public API
----------
    from duval_triangle_v7 import generate_duval_image, classify_duval

    image_path = generate_duval_image(data)   # data dict from parse_pdf()
    zone       = classify_duval(pCH4, pC2H4, pC2H2)
"""

import io
import numpy as np
import matplotlib
matplotlib.use("Agg")                       # non-interactive backend for Streamlit
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon

# ─────────────────────────────────────────────────────────────────────────────
# Zone definitions (IEC 60599)
# ─────────────────────────────────────────────────────────────────────────────

ZONES = {
    "PD": {
        "color": "#B3D9FF",
        "label": "PD\nPartial Discharge",
        "points": [(98, 0, 2), (100, 0, 0), (98, 2, 0)],
    },
    "T1": {
        "color": "#FFFF99",
        "label": "T1\n< 300°C",
        "points": [(98, 0, 2), (98, 2, 0), (76, 24, 0), (77, 0, 23)],
    },
    "T2": {
        "color": "#FFD966",
        "label": "T2\n300–700°C",
        "points": [(77, 0, 23), (76, 24, 0), (40, 60, 0), (46, 0, 54)],
    },
    "T3": {
        "color": "#FF9900",
        "label": "T3\n> 700°C",
        "points": [(46, 0, 54), (40, 60, 0), (0, 100, 0), (0, 93, 7), (0, 0, 100)],
    },
    "D1": {
        "color": "#FF7F7F",
        "label": "D1\nLow Energy\nDischarge",
        "points": [(100, 0, 0), (98, 2, 0), (76, 24, 0), (87, 0, 13)],
    },
    "D2": {
        "color": "#FF3333",
        "label": "D2\nHigh Energy\nDischarge",
        "points": [(87, 0, 13), (76, 24, 0), (40, 60, 0), (23, 0, 77)],
    },
    "DT": {
        "color": "#CC66FF",
        "label": "DT\nMixed Discharge\n+ Thermal",
        "points": [(23, 0, 77), (40, 60, 0), (0, 93, 7), (0, 0, 100)],
    },
}

FAULT_MEANINGS = {
    "PD": "Partial Discharge",
    "T1": "Thermal Fault < 300°C",
    "T2": "Thermal Fault 300–700°C",
    "T3": "Thermal Fault > 700°C",
    "D1": "Low Energy Electrical Discharge",
    "D2": "High Energy Electrical Discharge (Arc)",
    "DT": "Mixed Discharge + Thermal Fault",
}


# ─────────────────────────────────────────────────────────────────────────────
# Ternary → Cartesian
# ─────────────────────────────────────────────────────────────────────────────

def _ternary_to_cart(pCH4, pC2H4, pC2H2):
    """Convert ternary percentages to (x, y) Cartesian in equilateral triangle."""
    a = pCH4  / 100
    b = pC2H4 / 100
    c = pC2H2 / 100
    x = 0.5 * (2 * b + c) / (a + b + c)
    y = (np.sqrt(3) / 2) * c / (a + b + c)
    return x, y


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_duval(pCH4: float, pC2H4: float, pC2H2: float) -> str:
    """
    Standard Duval Triangle fault classification (IEC 60599).

    Parameters
    ----------
    pCH4, pC2H4, pC2H2 : float
        Percentage contributions (must sum to 100).

    Returns
    -------
    str : one of 'PD', 'T1', 'T2', 'T3', 'D1', 'D2', 'DT'
    """
    if pC2H2 >= 29:
        return "DT" if pCH4 <= 23 else "D2"
    if pC2H2 >= 13:
        return "DT" if pC2H4 <= 60 and pCH4 <= 23 else "D2"
    if pC2H2 >= 2:
        if pC2H4 < 24:
            return "T1"
        return "D1" if pCH4 >= 87 else "D2"
    # pC2H2 < 2
    if pC2H4 >= 60:
        return "T3"
    if pC2H4 >= 24:
        return "T2"
    if pCH4 >= 98:
        return "PD"
    return "T1"


def _safe_float(val) -> float:
    """Convert a value to float; treat 'ND' or empty as 0."""
    try:
        if str(val).strip().upper() in ("ND", "", "NOT FOUND"):
            return 0.0
        return float(val)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_duval_image(data: dict, output_path: str = "duval_triangle.png") -> str:
    """
    Build and save a Duval Triangle PNG from a DGA data dict.

    Parameters
    ----------
    data        : dict returned by parse_pdf()
    output_path : where to save the PNG (default: 'duval_triangle.png')

    Returns
    -------
    str : absolute path to the saved PNG
    """
    CH4  = _safe_float(data.get("ch4",  0))
    C2H4 = _safe_float(data.get("c2h4", 0))
    C2H2 = _safe_float(data.get("c2h2", 0))

    total = CH4 + C2H4 + C2H2
    if total == 0:
        pCH4 = pC2H4 = pC2H2 = 0.0
        fault_zone = "T1"          # default when all gases are zero / ND
    else:
        pCH4  = (CH4  / total) * 100
        pC2H4 = (C2H4 / total) * 100
        pC2H2 = (C2H2 / total) * 100
        fault_zone = classify_duval(pCH4, pC2H4, pC2H2)

    # ── Figure ───────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(17, 10), facecolor="white")
    ax.set_aspect("equal")
    ax.axis("off")

    # Filled zone polygons
    for zone_name, zone_data in ZONES.items():
        pts = [_ternary_to_cart(*p) for p in zone_data["points"]]
        poly = Polygon(pts, closed=True,
                       facecolor=zone_data["color"], edgecolor="white",
                       linewidth=1.0, alpha=0.85, zorder=1)
        ax.add_patch(poly)
        cx = np.mean([p[0] for p in pts])
        cy = np.mean([p[1] for p in pts])
        ax.text(cx, cy, zone_data["label"], ha="center", va="center",
                fontsize=7.5, fontweight="bold", color="#222222",
                zorder=4, linespacing=1.3)

    # Triangle outline
    triangle = Polygon([(0, 0), (1, 0), (0.5, np.sqrt(3) / 2)],
                       closed=True, fill=False,
                       edgecolor="black", linewidth=2, zorder=5)
    ax.add_patch(triangle)

    # Grid lines at 20% intervals
    tick_vals = [20, 40, 60, 80]
    for tv in tick_vals:
        for pairs in [
            (_ternary_to_cart(tv, 0, 100 - tv),   _ternary_to_cart(tv, 100 - tv, 0)),
            (_ternary_to_cart(0, tv, 100 - tv),   _ternary_to_cart(100 - tv, tv, 0)),
            (_ternary_to_cart(0, 100 - tv, tv),   _ternary_to_cart(100 - tv, 0, tv)),
        ]:
            ax.plot([pairs[0][0], pairs[1][0]], [pairs[0][1], pairs[1][1]],
                    color="gray", lw=0.4, ls="--", zorder=2)

    # Corner labels
    off = 0.04
    ax.text(-off,    -off,    "100%\nCH₄",  ha="center", va="top",    fontsize=9, fontweight="bold")
    ax.text(1 + off, -off,    "100%\nC₂H₄", ha="center", va="top",    fontsize=9, fontweight="bold")
    ax.text(0.5, np.sqrt(3) / 2 + 0.02, "100%\nC₂H₂",
            ha="center", va="bottom", fontsize=9, fontweight="bold")

    # Axis percentage ticks
    for tv in tick_vals:
        x,  y  = _ternary_to_cart(100 - tv, tv, 0)
        x2, y2 = _ternary_to_cart(tv, 0, 100 - tv)
        x3, y3 = _ternary_to_cart(0, tv, 100 - tv)
        ax.text(x,  y  - 0.04, f"{tv}%", ha="center", va="top",    fontsize=7, color="#555555")
        ax.text(x2 - 0.03, y2, f"{tv}%", ha="right",  va="center", fontsize=7, color="#555555")
        ax.text(x3 + 0.03, y3, f"{tv}%", ha="left",   va="center", fontsize=7, color="#555555")

    # Sample point
    if total > 0:
        sx, sy = _ternary_to_cart(pCH4, pC2H4, pC2H2)
        ax.scatter([sx], [sy], s=220, color="red", edgecolors="black",
                   linewidths=1.5, zorder=10, marker="*")
        ax.annotate(
            f"  Sample Point\n  CH₄={pCH4:.1f}%\n  C₂H₄={pC2H4:.1f}%\n  C₂H₂={pC2H2:.1f}%",
            (sx, sy),
            xytext=(sx + 0.12, sy + 0.06),
            fontsize=8, color="darkred", fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="darkred", lw=1.2),
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                      edgecolor="darkred", alpha=0.85),
            zorder=11,
        )

    # Title
    equip = data.get("equipment_designation") or data.get("css_name") or ""
    subtitle = f" — {equip}" if equip else ""
    ax.set_title(
        f"Duval's Triangle (IEC 60599){subtitle}\n"
        f"Fault Zone: {fault_zone} — {FAULT_MEANINGS.get(fault_zone, '')}",
        fontsize=13, fontweight="bold", pad=14,
    )

    # Legend
    legend_patches = [
        mpatches.Patch(color=v["color"], label=v["label"].replace("\n", " "))
        for v in ZONES.values()
    ]
    ax.legend(handles=legend_patches, loc="lower right",
              bbox_to_anchor=(1.35, 0.0), fontsize=7.5,
              title="Fault Zones", framealpha=0.9, title_fontsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)

    return os.path.abspath(output_path)


import os   # needed for abspath – import here to avoid circular at top