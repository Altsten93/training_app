"""
correlation.py

Beräknar veckovis aggregering av träningsvolym och muskelmassa från Withings-vågdata,
utför tidsserieimputering (linjär interpolation och regressionsestimering),
samt utför avancerad sambandsanalys:
1. Blockaggregering (8-veckors mesocykler) med start/slut-median för att rensa BIA-vätskebrus.
2. Korrelation mellan genomsnittlig veckovolym och delta muskelmassa (muskeltillväxt).
3. Exkluderar imputerad data från regressionsunderlaget (include_imputed=False som standard).
"""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

try:
    from scipy import stats as scipy_stats
except ImportError:
    scipy_stats = None


def calculate_pearson_r_and_p(x: List[float], y: List[float]) -> Tuple[float, float]:
    """Beräkna Pearsons r och p-värde med scipy eller analytisk fallback."""
    n = len(x)
    if n < 2:
        return 0.0, 1.0

    arr_x = np.array(x, dtype=float)
    arr_y = np.array(y, dtype=float)

    std_x = np.std(arr_x)
    std_y = np.std(arr_y)

    if std_x == 0 or std_y == 0 or np.isnan(std_x) or np.isnan(std_y):
        return 0.0, 1.0

    if scipy_stats is not None:
        try:
            res = scipy_stats.pearsonr(arr_x, arr_y)
            r_val = float(res[0])
            p_val = float(res[1])
            return (0.0 if np.isnan(r_val) else r_val, 1.0 if np.isnan(p_val) else p_val)
        except Exception:
            pass

    # Fallback-beräkning av r
    corr = np.corrcoef(arr_x, arr_y)[0, 1]
    r_val = 0.0 if np.isnan(corr) else float(corr)

    # Approximativt p-värde via t-fördelning om n >= 3
    if n > 2 and abs(r_val) < 1.0:
        t_stat = r_val * math.sqrt((n - 2) / max(1e-9, 1.0 - r_val**2))
        p_val = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t_stat) / math.sqrt(2.0))))
        p_val = max(0.0, min(1.0, float(p_val)))
    else:
        p_val = 1.0

    return r_val, p_val


def interpret_correlation(r: float, p_value: Optional[float] = None) -> str:
    """Returnera en beskrivande svensk text för Pearsons korrelationskoefficient r och p-värde."""
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
        strength = "försumbart/inget tydligt"

    if strength.startswith("försumbart"):
        text = f"{strength} samband"
    else:
        text = f"{strength} {direction} samband"

    if p_value is not None and not np.isnan(p_value):
        if p_value < 0.01:
            text += f", statistiskt signifikant (p = {p_value:.3f})"
        elif p_value < 0.05:
            text += f", statistiskt signifikant (p = {p_value:.3f})"
        else:
            text += f" (p = {p_value:.3f})"

    return text


def compute_blocks_aggregation(
    weeks: List[str],
    vols: List[float],
    muscles: List[float],
    is_imputed: List[bool],
    block_size: int = 8,
    step_size: int = 4,
    lag_weeks: int = 0,
    include_imputed: bool = False,
) -> Dict[str, Any]:
    """
    Blockaggregering (Mesocykel / 8-veckors träningsblock):
    - Beräknar genomsnittlig veckovolym (kg) under blocket.
    - Beräknar median för muskelmassa vid start och slut av blocket för att rensa vätskebrus.
    - Beräknar Delta Muskelmassa = Massa_slut - Massa_start.
    - Exkluderar block som saknar faktiska mätningar vid start/slut om include_imputed=False.
    """
    total_weeks = len(weeks)
    blocks: List[Dict[str, Any]] = []

    max_start = total_weeks - block_size - lag_weeks + 1
    if max_start <= 0:
        return {
            "points": [],
            "regressionLine": [],
            "r": 0.0,
            "pValue": 1.0,
            "k": 0.0,
            "m": 0.0,
            "n": 0,
            "summaryText": "För få veckor för att skapa träningsblock.",
        }

    for start_i in range(0, max_start, step_size):
        end_i = start_i + block_size - 1

        # Genomsnittlig veckovolym under träningsblocket
        block_vols = vols[start_i : end_i + 1]
        avg_vol = float(np.mean(block_vols))

        # Utfallsmätning (ev. med tidslagg)
        out_start = start_i + lag_weeks
        out_end = end_i + lag_weeks

        # Startfönster: de 2 första veckorna i utfallsfönstret
        start_slice_indices = range(out_start, min(out_start + 2, out_end))
        # Slutfönster: de 2 sista veckorna i utfallsfönstret (eller upp till +1 vecka efter)
        end_slice_indices = range(max(out_start, out_end - 1), min(total_weeks, out_end + 2))

        if include_imputed:
            start_vals = [muscles[i] for i in start_slice_indices]
            end_vals = [muscles[i] for i in end_slice_indices]
        else:
            # Endast faktiska mätningar
            start_vals = [muscles[i] for i in start_slice_indices if not is_imputed[i]]
            end_vals = [muscles[i] for i in end_slice_indices if not is_imputed[i]]

        # Block är giltigt om vi har godkända mätningar både vid start och slut
        if start_vals and end_vals:
            m_start = float(np.median(start_vals))
            m_end = float(np.median(end_vals))
            delta_m = round(m_end - m_start, 2)
            has_imputed = any(is_imputed[i] for i in start_slice_indices) or any(is_imputed[i] for i in end_slice_indices)

            blocks.append({
                "x": round(avg_vol, 1),
                "y": delta_m,
                "label": f"{weeks[start_i]} – {weeks[end_i]}",
                "startWeek": weeks[start_i],
                "endWeek": weeks[end_i],
                "startMuscle": round(m_start, 2),
                "endMuscle": round(m_end, 2),
                "type": "block",
                "is_imputed": has_imputed,
            })

    n = len(blocks)
    if n < 2:
        return {
            "points": blocks,
            "regressionLine": [],
            "r": 0.0,
            "pValue": 1.0,
            "k": 0.0,
            "m": 0.0,
            "n": n,
            "summaryText": f"Endast {n} giltiga träningsblock hittades. Minst 2 krävs för korrelation.",
        }

    x_vals = [b["x"] for b in blocks]
    y_vals = [b["y"] for b in blocks]

    r_val, p_val = calculate_pearson_r_and_p(x_vals, y_vals)
    r_val = round(r_val, 2)
    p_val = round(p_val, 3)

    # Linjär regression Delta = k * Volym + m
    std_x = np.std(x_vals)
    if std_x > 0 and n > 1:
        k_val, m_val = np.polyfit(x_vals, y_vals, 1)
        k_val = round(float(k_val), 6)
        m_val = round(float(m_val), 2)
    else:
        k_val = 0.0
        m_val = round(float(np.mean(y_vals)), 2)

    min_x = float(np.min(x_vals))
    max_x = float(np.max(x_vals))
    regression_line = [
        {"x": round(min_x, 1), "y": round(k_val * min_x + m_val, 2)},
        {"x": round(max_x, 1), "y": round(k_val * max_x + m_val, 2)},
    ]

    desc = interpret_correlation(r_val, p_val)
    imputed_note = "inklusive imputerade block" if include_imputed else "exkluderat imputerad data"
    summary_text = (
        f"Korrelation r = {r_val:.2f} ({desc}). "
        f"Baserat på {n} träningsblock om {block_size} veckor ({imputed_note})."
    )

    return {
        "points": blocks,
        "regressionLine": regression_line,
        "r": r_val,
        "pValue": p_val,
        "k": k_val,
        "m": m_val,
        "n": n,
        "summaryText": summary_text,
    }


def compute_weekly_correlation(
    weeks: List[str],
    vols: List[float],
    muscles: List[float],
    is_imputed: List[bool],
    include_imputed: bool = False,
) -> Dict[str, Any]:
    """Veckovis analys: filtrerar bort imputerad data vid include_imputed=False."""
    points: List[Dict[str, Any]] = []

    for i in range(len(weeks)):
        imp = is_imputed[i]
        if not include_imputed and imp:
            continue
        points.append({
            "x": vols[i],
            "y": muscles[i],
            "week": weeks[i],
            "is_imputed": imp,
            "type": "weekly",
        })

    n = len(points)
    if n < 2:
        return {
            "points": points,
            "regressionLine": [],
            "r": 0.0,
            "pValue": 1.0,
            "k": 0.0,
            "m": 0.0,
            "n": n,
            "summaryText": f"Endast {n} mätpunkter tillgängliga.",
        }

    x_vals = [p["x"] for p in points]
    y_vals = [p["y"] for p in points]

    r_val, p_val = calculate_pearson_r_and_p(x_vals, y_vals)
    r_val = round(r_val, 2)
    p_val = round(p_val, 3)

    std_x = np.std(x_vals)
    if std_x > 0 and n > 1:
        k_val, m_val = np.polyfit(x_vals, y_vals, 1)
        k_val = round(float(k_val), 6)
        m_val = round(float(m_val), 2)
    else:
        k_val = 0.0
        m_val = round(float(np.mean(y_vals)), 2)

    min_x = float(np.min(x_vals))
    max_x = float(np.max(x_vals))
    regression_line = [
        {"x": round(min_x, 1), "y": round(k_val * min_x + m_val, 2)},
        {"x": round(max_x, 1), "y": round(k_val * max_x + m_val, 2)},
    ]

    desc = interpret_correlation(r_val, p_val)
    imputed_note = "inklusive imputerade veckor" if include_imputed else "exkluderat imputerad data"
    summary_text = (
        f"Veckovis korrelation r = {r_val:.2f} ({desc}). "
        f"Baserat på {n} veckor ({imputed_note})."
    )

    return {
        "points": points,
        "regressionLine": regression_line,
        "r": r_val,
        "pValue": p_val,
        "k": k_val,
        "m": m_val,
        "n": n,
        "summaryText": summary_text,
    }


def compute_volume_muscle_correlation(
    workouts: List[Dict[str, Any]],
    scale_data: List[Dict[str, Any]],
    include_imputed: bool = False,
    block_size: int = 8,
    step_size: int = 4,
    lag_weeks: int = 0,
) -> Dict[str, Any]:
    """
    Huvudfunktion för korrelationsanalys:
    1. Bygger kontinuerlig veckotidslinje och utför biologisk linjär interpolation för tidslinjen.
    2. Utför blockaggregering (mesocykel på block_size veckor med start/slut-median)
       och filtrerar bort imputerad data som standard (include_imputed=False).
    3. Returnerar strukturerade stats och scatter för både block- och veckovisa vyer.
    """
    empty_result = {
        "timeline": {
            "weeks": [],
            "volume": [],
            "muscleMass": [],
            "isImputed": [],
        },
        "stats": {
            "r": 0.0,
            "pValue": 1.0,
            "k": 0.0,
            "m": 0.0,
            "n": 0,
            "totalWeeks": 0,
            "imputedWeeks": 0,
            "measuredWeeks": 0,
            "method": "block_aggregation",
        },
        "interpretation": "Ingen mätdata tillgänglig för korrelationsanalys.",
        "scatter": {
            "points": [],
            "regressionLine": [],
            "r": 0.0,
            "pValue": 1.0,
            "k": 0.0,
            "m": 0.0,
            "n": 0,
            "totalWeeks": 0,
            "imputedWeeks": 0,
            "measuredWeeks": 0,
            "summaryText": "Ingen mätdata tillgänglig för korrelationsanalys.",
        },
        "weeklyAnalysis": {
            "points": [],
            "regressionLine": [],
            "r": 0.0,
            "pValue": 1.0,
            "k": 0.0,
            "m": 0.0,
            "n": 0,
            "summaryText": "Ingen mätdata tillgänglig.",
        },
    }

    # 1. Aggregera träningsvolym per ISO-vecka (YYYY-Www)
    weekly_vol: Dict[str, float] = {}
    for w in workouts:
        if w.get("completed") is False:
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
            iso_str = str(m_str).replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso_str)
            yw = dt.strftime("%G-W%V")
            weekly_muscle_vals.setdefault(yw, []).append(float(val))
        except Exception:
            continue

    if not weekly_muscle_vals:
        return empty_result

    # Genomsnittlig muskelmassa för veckor med faktiska vägningar
    weekly_muscle_measured = {
        yw: round(sum(vals) / len(vals), 2) for yw, vals in weekly_muscle_vals.items()
    }

    # 3. Kontinuerlig kalendertidslinje från första vägningen till senaste aktivitet
    first_muscle_week = min(weekly_muscle_measured.keys())
    latest_vol_week = max(weekly_vol.keys(), default=first_muscle_week)
    latest_muscle_week = max(weekly_muscle_measured.keys(), default=first_muscle_week)
    last_week = max(latest_vol_week, latest_muscle_week)

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

    # Imputering för tidslinjen:
    series = pd.Series(raw_muscle, dtype=float)
    interpolated = series.interpolate(method="linear")

    known_idx = series.dropna().index.values
    known_vals = series.dropna().values
    if len(known_idx) >= 2:
        beta, alpha = np.polyfit(known_idx, known_vals, 1)
        min_bound = max(20.0, float(np.min(known_vals)) - 10.0)
        max_bound = min(150.0, float(np.max(known_vals)) + 10.0)

        for i in range(len(interpolated)):
            if pd.isna(interpolated.iloc[i]):
                pred_val = alpha + beta * i
                interpolated.iloc[i] = max(min_bound, min(max_bound, pred_val))
    else:
        interpolated = interpolated.bfill().ffill()

    final_muscle = [round(float(v), 2) for v in interpolated.values]

    # 4. Beräkna Blockanalys (Mesocykler)
    block_res = compute_blocks_aggregation(
        weeks=weeks,
        vols=vols,
        muscles=final_muscle,
        is_imputed=is_imputed,
        block_size=block_size,
        step_size=step_size,
        lag_weeks=lag_weeks,
        include_imputed=include_imputed,
    )

    # 5. Beräkna Veckovis analys
    weekly_res = compute_weekly_correlation(
        weeks=weeks,
        vols=vols,
        muscles=final_muscle,
        is_imputed=is_imputed,
        include_imputed=include_imputed,
    )

    # Välj primära stats från block-analysen om den har minst 2 block, annars från veckovisa analysen
    primary = block_res if block_res["n"] >= 2 else weekly_res

    stats_obj = {
        "r": primary["r"],
        "pValue": primary["pValue"],
        "k": primary["k"],
        "m": primary["m"],
        "n": primary["n"],
        "method": f"{block_size}-veckors mesocykelblock" if block_res["n"] >= 2 else "veckovis mätpunkt",
    }

    return {
        "timeline": {
            "weeks": weeks,
            "volume": vols,
            "muscleMass": final_muscle,
            "isImputed": is_imputed,
        },
        "stats": stats_obj,
        "interpretation": primary["summaryText"],
        "scatter": {
            "points": primary["points"],
            "regressionLine": primary["regressionLine"],
            "r": primary["r"],
            "pValue": primary["pValue"],
            "k": primary["k"],
            "m": primary["m"],
            "n": primary["n"],
            "summaryText": primary["summaryText"],
        },
        "weeklyAnalysis": weekly_res,
        "blockAnalysis": block_res,
    }

