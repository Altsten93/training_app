"""
correlation.py

Beräknar veckovis aggregering av träningsvolym och muskelmassa från Withings-vågdata,
utför tidsserieimputering (linjär interpolation och regressionsestimering)
och utför korrelationsanalys (Pearsons r samt linjär regression).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


def interpret_correlation(r: float) -> str:
    """Returnera en beskrivande svensk text för Pearsons korrelationskoefficient r."""
    if np.isnan(r):
        return "inget statistiskt samband kunde beräknas"
    abs_r = abs(r)
    direction = "positivt" if r >= 0 else "negativt"

    if abs_r >= 0.7:
        strength = "starkt"
    elif abs_r >= 0.4:
        strength = "måttligt"
    elif abs_r >= 0.2:
        strength = "svagt"
    else:
        return "försumbart/inget tydligt samband"

    return f"{strength} {direction} samband"


def compute_volume_muscle_correlation(
    workouts: List[Dict[str, Any]],
    scale_data: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Koppla och imputera data:
    1. Träningsvolym: summera veckans totala volym (sum(reps * vikt)).
       För veckor utan registrerade pass sätts volymen till 0.
    2. Muskelmassa (muscle_mass_kg): beräkna veckans genomsnittliga muskelmassa för veckor med vägningar.
    3. Imputering för veckor utan vägning:
       - Primär metod: linjär interpolation mellan kända mätpunkter.
       - Sekundär metod: trendbaserad regressionsimputering för ändveckor (före första / efter sista mätning).
       - Märkning med boolesk flagga is_imputed.
    4. Korrelationsanalys:
       - Pearsons r
       - Regressionslinje (y = kx + m)
       - Sammanfattande textrad
    """
    empty_result = {
        "timeline": {
            "weeks": [],
            "volume": [],
            "muscleMass": [],
            "isImputed": [],
        },
        "scatter": {
            "points": [],
            "regressionLine": [],
            "r": 0.0,
            "k": 0.0,
            "m": 0.0,
            "totalWeeks": 0,
            "imputedWeeks": 0,
            "measuredWeeks": 0,
            "summaryText": "Ingen mätdata tillgänglig för korrelationsanalys.",
        },
    }

    # 1. Aggregera träningsvolym per ISO-vecka (YYYY-Www)
    weekly_vol: Dict[str, float] = {}
    for w in workouts:
        if not w.get("completed"):
            continue
        d_str = w.get("workout_date")
        if not d_str:
            continue
        try:
            dt = datetime.strptime(str(d_str).strip(), "%Y-%m-%d")
            yw = dt.strftime("%G-W%V")
            vol = float(w.get("volume") or 0.0)
            weekly_vol[yw] = weekly_vol.get(yw, 0.0) + vol
        except Exception:
            continue

    # 2. Aggregera Withings muskelmassa per ISO-vecka
    weekly_muscle_vals: Dict[str, List[float]] = {}
    for s in scale_data:
        m_str = s.get("measured_at")
        val = s.get("muscle_mass_kg")
        if not m_str or val is None:
            continue
        try:
            # ISO-datum t.ex. 2023-06-03T13:46:43+00:00 eller 2023-06-03T13:46:43Z
            iso_str = str(m_str).replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_str)
            yw = dt.strftime("%G-W%V")
            weekly_muscle_vals.setdefault(yw, []).append(float(val))
        except Exception:
            continue

    if not weekly_muscle_vals:
        return empty_result

    # Beräkna genomsnittlig muskelmassa för veckor med mätningar
    weekly_muscle_measured = {
        yw: round(sum(vals) / len(vals), 2) for yw, vals in weekly_muscle_vals.items()
    }

    # 3. Kontinuerlig kalendertidslinje
    # Börja från första veckan med registrerad muskelmassa
    first_muscle_week = min(weekly_muscle_measured.keys())
    latest_vol_week = max(weekly_vol.keys(), default=first_muscle_week)
    latest_muscle_week = max(weekly_muscle_measured.keys(), default=first_muscle_week)
    last_week = max(latest_vol_week, latest_muscle_week)

    # Inkludera nuvarande kalendervecka om den är senare
    now_utc = datetime.now(timezone.utc)
    current_yw = f"{now_utc.isocalendar().year}-W{now_utc.isocalendar().week:02d}"
    if current_yw > last_week:
        last_week = current_yw

    y_start, w_start = map(int, first_muscle_week.split("-W"))
    y_end, w_end = map(int, last_week.split("-W"))

    start_date = datetime.fromisocalendar(y_start, w_start, 1)
    end_date = datetime.fromisocalendar(y_end, w_end, 1)
    week_range = pd.date_range(start=start_date, end=end_date, freq="W-MON")
    weeks = [d.strftime("%G-W%V") for d in week_range]

    if not weeks:
        return empty_result

    # Skapa tidslinjeserier
    vols = [round(float(weekly_vol.get(w, 0.0)), 1) for w in weeks]
    raw_muscle = [weekly_muscle_measured.get(w) for w in weeks]
    is_imputed = [v is None for v in raw_muscle]

    # Imputering:
    series = pd.Series(raw_muscle, dtype=float)
    # Primär metod: Linjär interpolation
    interpolated = series.interpolate(method="linear")

    # Sekundär metod: Trendbaserad regressionsimputering för ev. ändveckor (framåt/bakåt)
    known_idx = series.dropna().index.values
    known_vals = series.dropna().values
    if len(known_idx) >= 2:
        beta, alpha = np.polyfit(known_idx, known_vals, 1)
        # Sätt rimliga fysiologiska gränser baserat på kända mätningar
        min_bound = max(20.0, float(np.min(known_vals)) - 10.0)
        max_bound = min(150.0, float(np.max(known_vals)) + 10.0)

        for i in range(len(interpolated)):
            if pd.isna(interpolated.iloc[i]):
                pred_val = alpha + beta * i
                clamped_val = max(min_bound, min(max_bound, pred_val))
                interpolated.iloc[i] = clamped_val
    else:
        interpolated = interpolated.bfill().ffill()

    final_muscle = [round(float(v), 2) for v in interpolated.values]

    # 4. Beräkna korrelation och regression
    x = np.array(vols, dtype=float)
    y = np.array(final_muscle, dtype=float)

    # Skydd vid noll varians
    x_std = np.std(x)
    y_std = np.std(y)

    if x_std > 0 and y_std > 0 and len(x) > 1:
        corr_matrix = np.corrcoef(x, y)
        r = float(corr_matrix[0, 1])
        k, m = np.polyfit(x, y, 1)
        r_val = round(r, 2)
        k_val = round(float(k), 6)
        m_val = round(float(m), 2)
    else:
        r_val = 0.0
        k_val = 0.0
        m_val = round(float(np.mean(y)), 2) if len(y) > 0 else 0.0

    min_vol = float(np.min(x))
    max_vol = float(np.max(x))
    regression_line = [
        {"x": round(min_vol, 1), "y": round(k_val * min_vol + m_val, 2)},
        {"x": round(max_vol, 1), "y": round(k_val * max_vol + m_val, 2)},
    ]

    scatter_points = [
        {
            "x": vols[i],
            "y": final_muscle[i],
            "week": weeks[i],
            "is_imputed": is_imputed[i],
        }
        for i in range(len(weeks))
    ]

    total_weeks = len(weeks)
    imputed_count = sum(is_imputed)
    measured_count = total_weeks - imputed_count

    desc = interpret_correlation(r_val)
    summary_text = (
        f"Korrelation r = {r_val:.2f} ({desc}). "
        f"Baserat på {total_weeks} veckor (varav {imputed_count} veckor med imputerad kroppsdata)."
    )

    return {
        "timeline": {
            "weeks": weeks,
            "volume": vols,
            "muscleMass": final_muscle,
            "isImputed": is_imputed,
        },
        "scatter": {
            "points": scatter_points,
            "regressionLine": regression_line,
            "r": r_val,
            "k": k_val,
            "m": m_val,
            "totalWeeks": total_weeks,
            "imputedWeeks": imputed_count,
            "measuredWeeks": measured_count,
            "summaryText": summary_text,
        },
    }
