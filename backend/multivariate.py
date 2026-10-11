"""
multivariate.py

Multivariat analys av muskeltillväxt baserat på tre livsstilskomponenter:
1. Träningsvolym (kg/vecka) från schemalagda/genomförda pass.
2. Sömn (timmar/natt) från Apple Health.
3. Vardagssteg (steg/dag) från Apple Health.

Målet är att isolera den relativa och partiella effekten av varje livsstilsvariabel
på förändringen i muskelmassa (Delta Muskelmassa) per 8-veckors mesocykelblock:
Delta Muskelmassa = beta_0 + beta_1 * Volym + beta_2 * Sömn + beta_3 * Steg + epsilon

Innehåller:
- Blockaggregering (8 veckor) med start/slut-median för Withings BIA-vågdata.
- Hantering av saknade mätvärden (NULLs/glapp) i Apple Health via snitt på mätta dagar.
- Datatäckningskontroll (flaggning och exkludering av block med <50% täckning).
- Multipel linjär regression (OLS) med standardiserade beta-vikter (Z-score).
- Beräkning av R^2, justerat R^2, F-statistik, modellens p-värde.
- Koefficienter med p-värden, konfidensintervall (95% CI) och multikollinearitet (VIF).
"""

from datetime import datetime, date, timedelta
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

try:
    from scipy import stats as scipy_stats
except ImportError:
    scipy_stats = None


def parse_date_only(val: Any) -> Optional[str]:
    """Extraherar YYYY-MM-DD från sträng eller datetime-objekt."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    # Om ISO-tidsstämpel med 'T' eller mellanslag
    if "T" in s:
        s = s.split("T")[0]
    elif " " in s:
        s = s.split(" ")[0]
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").date()
        return dt.isoformat()
    except Exception:
        return None


def clean_health_records(health_data: List[Dict[str, Any]]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Normaliserar Apple Health-rader till en uppslagsbok per datum { 'YYYY-MM-DD': { 'steps': float, 'sleep': float } }.
    Hanterar både platta vyer (apple_health_summary_view) och råa exportstrukturer (apple_health_daily).
    """
    clean: Dict[str, Dict[str, Optional[float]]] = {}

    for row in health_data:
        # 1. Kontrollera platt vy/struktur
        raw_date = row.get("date")
        step_val = row.get("step_count")
        # Stödjer både sleep_total_hours och total_sleep_hours
        sleep_val = row.get("sleep_total_hours")
        if sleep_val is None:
            sleep_val = row.get("total_sleep_hours")

        d_str = parse_date_only(raw_date)

        # 2. Om rå 'data'-kolumn finns med metrics-lista
        if not d_str and "data" in row and isinstance(row["data"], dict):
            metrics = row["data"].get("metrics", [])
            for m in metrics:
                m_name = m.get("name")
                for item in m.get("data", []):
                    item_d = parse_date_only(item.get("date"))
                    if not item_d:
                        continue
                    if item_d not in clean:
                        clean[item_d] = {"steps": None, "sleep": None}
                    if m_name == "step_count" and item.get("qty") is not None:
                        clean[item_d]["steps"] = float(item["qty"])
                    elif m_name == "sleep_analysis":
                        s_hrs = item.get("totalSleep")
                        if s_hrs is not None:
                            clean[item_d]["sleep"] = float(s_hrs)

        if d_str:
            if d_str not in clean:
                clean[d_str] = {"steps": None, "sleep": None}
            if step_val is not None:
                try:
                    clean[d_str]["steps"] = float(step_val)
                except (ValueError, TypeError):
                    pass
            if sleep_val is not None:
                try:
                    clean[d_str]["sleep"] = float(sleep_val)
                except (ValueError, TypeError):
                    pass

    return clean


def calculate_vif_scores(X_raw: np.ndarray) -> List[float]:
    """
    Beräknar Variance Inflation Factor (VIF) för varje prediktorkolumn i X_raw.
    VIF = 1 / (1 - R_j^2) när kolumn j regresseras mot övriga prediktorer.
    """
    n, p = X_raw.shape
    vifs: List[float] = []

    if p <= 1 or n <= p:
        return [1.0] * p

    for j in range(p):
        other_indices = [idx for idx in range(p) if idx != j]
        X_other = np.column_stack([np.ones(n), X_raw[:, other_indices]])
        y_target = X_raw[:, j]

        std_target = np.std(y_target)
        if std_target == 0 or np.isnan(std_target):
            vifs.append(1.0)
            continue

        try:
            b_other, _, _, _ = np.linalg.lstsq(X_other, y_target, rcond=None)
            pred = X_other @ b_other
            res = y_target - pred
            ss_res = np.sum(res**2)
            ss_tot = np.sum((y_target - np.mean(y_target))**2)
            if ss_tot == 0:
                vifs.append(1.0)
                continue
            r2_j = max(0.0, min(0.999999, 1.0 - (ss_res / ss_tot)))
            vif_val = 1.0 / (1.0 - r2_j)
            vifs.append(round(float(vif_val), 2))
        except Exception:
            vifs.append(1.0)

    return vifs


def run_multiple_linear_regression(
    X_raw: np.ndarray,
    y: np.ndarray,
    feature_names: List[str],
) -> Dict[str, Any]:
    """
    Kör OLS multipel regression:
    y = beta_0 + sum(beta_j * X_j) + e

    Beräknar:
    - R^2 och justerat R^2
    - F-statistik och modellens p-värde
    - Råa koefficienter, standardfel (SE), t-värden, p-värden, 95% konfidensintervall
    - Standardiserade regressionskoefficienter (beta-weights) och standardiserat 95% CI
    - Variance Inflation Factor (VIF)
    """
    n, p = X_raw.shape
    df_res = n - p - 1

    if n <= p + 1 or df_res <= 0:
        return {
            "r2": 0.0,
            "r2_adjusted": 0.0,
            "model_p_value": 1.0,
            "f_statistic": 0.0,
            "df_model": p,
            "df_res": max(0, df_res),
            "intercept": {"raw": 0.0, "se": 0.0, "p_value": 1.0},
            "coefficients": {
                name: {
                    "raw": 0.0,
                    "standardized_beta": 0.0,
                    "p_value": 1.0,
                    "se": 0.0,
                    "t_statistic": 0.0,
                    "ci_lower": 0.0,
                    "ci_upper": 0.0,
                    "std_ci_lower": 0.0,
                    "std_ci_upper": 0.0,
                    "vif": 1.0,
                    "significant": False,
                }
                for name in feature_names
            },
        }

    # Lägg till konstantkolumn för intercept
    X = np.column_stack([np.ones(n), X_raw])

    # OLS via minstakvadratmetoden
    try:
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    except Exception:
        beta = np.zeros(p + 1)

    y_pred = X @ beta
    residuals = y - y_pred
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y - np.mean(y))**2))

    if ss_tot > 0:
        r2 = max(0.0, min(1.0, 1.0 - (ss_res / ss_tot)))
    else:
        r2 = 0.0

    r2_adj = 1.0 - (1.0 - r2) * ((n - 1) / df_res)
    r2_adj = max(0.0, min(1.0, r2_adj))

    # Varians och kovariansmatris
    s2 = ss_res / df_res
    try:
        xtx_inv = np.linalg.pinv(X.T @ X)
        cov_beta = s2 * xtx_inv
        se_all = np.sqrt(np.maximum(0.0, np.diag(cov_beta)))
    except Exception:
        se_all = np.zeros(p + 1)

    # t-värden, p-värden och konfidensintervall (95%)
    t_stats = np.zeros(p + 1)
    p_vals = np.ones(p + 1)
    ci_lower = np.zeros(p + 1)
    ci_upper = np.zeros(p + 1)

    if scipy_stats is not None:
        t_crit = float(scipy_stats.t.ppf(0.975, df_res))
    else:
        t_crit = 1.96

    for i in range(p + 1):
        if se_all[i] > 1e-12:
            t_val = beta[i] / se_all[i]
            t_stats[i] = t_val
            if scipy_stats is not None:
                p_vals[i] = float(scipy_stats.t.sf(abs(t_val), df_res) * 2)
            else:
                # Approximativ normalfördelning
                p_vals[i] = float(2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t_val) / math.sqrt(2.0)))))
        else:
            t_stats[i] = 0.0
            p_vals[i] = 1.0

        ci_lower[i] = beta[i] - t_crit * se_all[i]
        ci_upper[i] = beta[i] + t_crit * se_all[i]

    # F-statistik och modell-p-värde
    if ss_tot > 0 and r2 > 0:
        denom = max(1e-12, (1.0 - r2) / df_res)
        f_stat = (r2 / p) / denom
    else:
        f_stat = 0.0

    if scipy_stats is not None and f_stat > 0:
        model_p = float(scipy_stats.f.sf(f_stat, p, df_res))
    else:
        model_p = 1.0 if f_stat == 0 else 0.0001

    # Standardiserade beta-koefficienter: beta_std = beta * (s_x / s_y)
    std_y = float(np.std(y, ddof=1))
    std_x = np.std(X_raw, axis=0, ddof=1)

    std_betas = np.zeros(p)
    std_ci_low = np.zeros(p)
    std_ci_up = np.zeros(p)

    for j in range(p):
        if std_y > 1e-12 and std_x[j] > 1e-12:
            ratio = std_x[j] / std_y
            std_betas[j] = beta[j + 1] * ratio
            std_ci_low[j] = ci_lower[j + 1] * ratio
            std_ci_up[j] = ci_upper[j + 1] * ratio
        else:
            std_betas[j] = 0.0
            std_ci_low[j] = 0.0
            std_ci_up[j] = 0.0

    # VIF-beräkning
    vifs = calculate_vif_scores(X_raw)

    coeff_dict: Dict[str, Any] = {}
    for j, name in enumerate(feature_names):
        p_val_rounded = round(float(p_vals[j + 1]), 4)
        coeff_dict[name] = {
            "raw": round(float(beta[j + 1]), 6),
            "standardized_beta": round(float(std_betas[j]), 3),
            "se": round(float(se_all[j + 1]), 6),
            "t_statistic": round(float(t_stats[j + 1]), 3),
            "p_value": p_val_rounded,
            "ci_lower": round(float(ci_lower[j + 1]), 6),
            "ci_upper": round(float(ci_upper[j + 1]), 6),
            "std_ci_lower": round(float(std_ci_low[j]), 3),
            "std_ci_upper": round(float(std_ci_up[j]), 3),
            "vif": vifs[j],
            "significant": p_val_rounded < 0.05,
        }

    return {
        "r2": round(float(r2), 3),
        "r2_adjusted": round(float(r2_adj), 3),
        "model_p_value": round(float(model_p), 4),
        "f_statistic": round(float(f_stat), 2),
        "df_model": p,
        "df_res": df_res,
        "intercept": {
            "raw": round(float(beta[0]), 4),
            "se": round(float(se_all[0]), 4),
            "p_value": round(float(p_vals[0]), 4),
        },
        "coefficients": coeff_dict,
    }


def generate_multivariate_summary(
    r2: float,
    r2_adj: float,
    model_p: float,
    n: int,
    coeffs: Dict[str, Any],
) -> str:
    """
    Genererar en tolkande, vetenskapligt grundad svensk sammanfattningstext för den multivariata modellen.
    Exempel:
    'Träningsvolym är den starkaste drivkraften (β = +0.58, p = 0.025), medan sömn visar en positiv
    faciliterande trend (β = +0.28, p = 0.081). Modellen förklarar sammanlagt 54 % av variationen i muskeltillväxt.'
    """
    if n < 4:
        return (
            f"Endast {n} träningsblock med tillräcklig datatäckning finns tillgängliga. "
            "Minst 4 block krävs för multivariat regressionsanalys."
        )

    labels = {
        "volume": "Träningsvolym",
        "sleep_hours": "Sömn",
        "daily_steps": "Vardagssteg",
    }

    # Rangordna variabler efter absolut storlek på standardiserad beta
    ranked = sorted(
        coeffs.items(),
        key=lambda item: abs(item[1].get("standardized_beta", 0.0)),
        reverse=True,
    )

    if not ranked:
        return "Inga giltiga regressionskoefficienter kunde beräknas."

    top_var, top_info = ranked[0]
    top_label = labels.get(top_var, top_var)
    top_beta = top_info.get("standardized_beta", 0.0)
    top_p = top_info.get("p_value", 1.0)
    top_sign = "+" if top_beta >= 0 else ""

    second_var, second_info = (ranked[1] if len(ranked) > 1 else (None, None))

    clauses = []
    if top_p < 0.05:
        clauses.append(f"{top_label} är den starkaste och statistiskt signifikanta drivkraften (β = {top_sign}{top_beta:.2f}, p = {top_p:.3f})")
    else:
        clauses.append(f"{top_label} visar den största relativa effekten (β = {top_sign}{top_beta:.2f}, p = {top_p:.3f})")

    if second_var and second_info:
        sec_label = labels.get(second_var, second_var).lower()
        sec_beta = second_info.get("standardized_beta", 0.0)
        sec_p = second_info.get("p_value", 1.0)
        sec_sign = "+" if sec_beta >= 0 else ""
        if sec_beta > 0:
            trend_word = "en positiv faciliterande trend" if sec_p >= 0.05 else "en signifikant positiv effekt"
        else:
            trend_word = "en dämpande trend" if sec_p >= 0.05 else "en signifikant negativ effekt"
        clauses.append(f"medan {sec_label} visar {trend_word} (β = {sec_sign}{sec_beta:.2f}, p = {sec_p:.3f})")

    r2_pct = int(round(r2 * 100))
    model_significance_note = " (statistiskt signifikant helhetsmodell)" if model_p < 0.05 else ""

    body = ", ".join(clauses)
    conclusion = (
        f"{body}. "
        f"Modellen förklarar sammanlagt {r2_pct} % av variationen i muskeltillväxt "
        f"(justerat R² = {r2_adj:.2f}, p = {model_p:.3f}{model_significance_note}) baserat på {n} mesocykelblock."
    )
    return conclusion


def compute_multivariate_lifestyle_model(
    workouts: List[Dict[str, Any]],
    scale_data: List[Dict[str, Any]],
    health_data: List[Dict[str, Any]],
    block_size: int = 8,
    step_size: int = 4,
    lag_weeks: int = 0,
    min_coverage: float = 0.5,
    exclude_low_coverage: bool = True,
    include_imputed: bool = False,
) -> Dict[str, Any]:
    """
    Bygger mesocykelblock (8 veckor som standard) och kör multivariat OLS-regression:
    Delta Muskelmassa = beta_0 + beta_1 * Volym + beta_2 * Sömn + beta_3 * Vardagssteg

    Parametrar:
    - workouts: Lista med slutförda pass (workout_date, volume, completed).
    - scale_data: Withings-invägningar (measured_at, muscle_mass_kg).
    - health_data: Apple Health dagsdata (date, step_count, sleep_total_hours).
    - block_size: Antal veckor per mesocykel (standard 8).
    - step_size: Förskjutning mellan block (standard 4 veckor).
    - lag_weeks: Tidslagg för muskeltillväxtutfallet (standard 0).
    - min_coverage: Minsta godkända datatäckning per block (standard 0.5 = 50% av dagarna).
    - exclude_low_coverage: Om block med <min_coverage ska exkluderas från regressionsmodellen.
    - include_imputed: Om imputerade vägningsveckor ska användas för start/slut-median.
    """
    empty_result = {
        "r2": 0.0,
        "r2_adjusted": 0.0,
        "model_p_value": 1.0,
        "f_statistic": 0.0,
        "n_blocks": 0,
        "total_blocks": 0,
        "degrees_of_freedom": 0,
        "coefficients": {
            "volume": {
                "name": "Träningsvolym",
                "unit": "kg/vecka",
                "raw": 0.0,
                "standardized_beta": 0.0,
                "p_value": 1.0,
                "se": 0.0,
                "t_statistic": 0.0,
                "ci_lower": 0.0,
                "ci_upper": 0.0,
                "std_ci_lower": 0.0,
                "std_ci_upper": 0.0,
                "vif": 1.0,
                "significant": False,
            },
            "sleep_hours": {
                "name": "Sömn",
                "unit": "h/dag",
                "raw": 0.0,
                "standardized_beta": 0.0,
                "p_value": 1.0,
                "se": 0.0,
                "t_statistic": 0.0,
                "ci_lower": 0.0,
                "ci_upper": 0.0,
                "std_ci_lower": 0.0,
                "std_ci_upper": 0.0,
                "vif": 1.0,
                "significant": False,
            },
            "daily_steps": {
                "name": "Vardagssteg",
                "unit": "steg/dag",
                "raw": 0.0,
                "standardized_beta": 0.0,
                "p_value": 1.0,
                "se": 0.0,
                "t_statistic": 0.0,
                "ci_lower": 0.0,
                "ci_upper": 0.0,
                "std_ci_lower": 0.0,
                "std_ci_upper": 0.0,
                "vif": 1.0,
                "significant": False,
            },
        },
        "intercept": {"raw": 0.0, "se": 0.0, "p_value": 1.0},
        "summary_text": "Otillräcklig data för multivariat analys.",
        "blocks_data": [],
    }

    # 1. Bygg ISO-veckotabell för träningsvolym
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

    weekly_muscle_measured = {
        yw: round(sum(vals) / len(vals), 2) for yw, vals in weekly_muscle_vals.items()
    }

    # 3. Skapa kontinuerlig kalendertidslinje
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

    if not weeks or len(weeks) < block_size:
        return empty_result

    vols = [round(float(weekly_vol.get(w, 0.0)), 1) for w in weeks]
    raw_muscle = [weekly_muscle_measured.get(w) for w in weeks]
    is_imputed = [v is None for v in raw_muscle]

    # Imputering för tidslinjen vid start/slut
    series = pd.Series(raw_muscle, dtype=float)
    interpolated = series.interpolate(method="linear")
    known_idx = series.dropna().index.values
    known_vals = series.dropna().values
    if len(known_idx) >= 2:
        beta_m, alpha_m = np.polyfit(known_idx, known_vals, 1)
        min_bound = max(20.0, float(np.min(known_vals)) - 10.0)
        max_bound = min(150.0, float(np.max(known_vals)) + 10.0)
        for i in range(len(interpolated)):
            if pd.isna(interpolated.iloc[i]):
                pred_val = alpha_m + beta_m * i
                interpolated.iloc[i] = max(min_bound, min(max_bound, pred_val))
    else:
        interpolated = interpolated.bfill().ffill()

    final_muscle = [round(float(v), 2) for v in interpolated.values]

    # 4. Rensa Apple Health-data
    clean_health = clean_health_records(health_data)

    # 5. Bygg mesocykelblock
    total_weeks = len(weeks)
    max_start = total_weeks - block_size - lag_weeks + 1
    if max_start <= 0:
        return empty_result

    blocks: List[Dict[str, Any]] = []

    for start_i in range(0, max_start, step_size):
        end_i = start_i + block_size - 1

        # Träningsvolym
        block_vols = vols[start_i : end_i + 1]
        avg_vol = float(np.mean(block_vols))

        # Utfallsmätning muskelmassa (start- och slutfönster)
        out_start = start_i + lag_weeks
        out_end = end_i + lag_weeks

        start_slice_indices = range(out_start, min(out_start + 2, out_end))
        end_slice_indices = range(max(out_start, out_end - 1), min(total_weeks, out_end + 2))

        if include_imputed:
            start_vals = [final_muscle[i] for i in start_slice_indices]
            end_vals = [final_muscle[i] for i in end_slice_indices]
        else:
            start_vals = [final_muscle[i] for i in start_slice_indices if not is_imputed[i]]
            end_vals = [final_muscle[i] for i in end_slice_indices if not is_imputed[i]]

        # Kräver giltiga muskelmätningar vid start och slut
        if not (start_vals and end_vals):
            continue

        m_start = float(np.median(start_vals))
        m_end = float(np.median(end_vals))
        delta_m = round(m_end - m_start, 2)

        # Kalenderdatum för blocket (Måndag startvecka till Söndag slutvecka)
        y_b_start, w_b_start = map(int, weeks[start_i].split("-W"))
        y_b_end, w_b_end = map(int, weeks[end_i].split("-W"))
        cal_start = datetime.fromisocalendar(y_b_start, w_b_start, 1).date()
        cal_end = datetime.fromisocalendar(y_b_end, w_b_end, 7).date()

        day_count = (cal_end - cal_start).days + 1
        block_days = [cal_start + timedelta(days=d) for d in range(day_count)]

        # Samla in steg och sömn för mätta dagar i blocket
        valid_steps: List[float] = []
        valid_sleeps: List[float] = []

        for d_obj in block_days:
            d_key = d_obj.isoformat()
            if d_key in clean_health:
                h_item = clean_health[d_key]
                st = h_item.get("steps")
                sl = h_item.get("sleep")
                if st is not None and not np.isnan(st):
                    valid_steps.append(st)
                if sl is not None and not np.isnan(sl):
                    valid_sleeps.append(sl)

        step_coverage = len(valid_steps) / day_count if day_count > 0 else 0.0
        sleep_coverage = len(valid_sleeps) / day_count if day_count > 0 else 0.0

        avg_steps = float(np.mean(valid_steps)) if valid_steps else None
        avg_sleep = float(np.mean(valid_sleeps)) if valid_sleeps else None

        coverage_ok = (step_coverage >= min_coverage) and (sleep_coverage >= min_coverage)
        included_in_model = (
            coverage_ok if exclude_low_coverage else (avg_steps is not None and avg_sleep is not None)
        )

        blocks.append({
            "label": f"{weeks[start_i]} – {weeks[end_i]}",
            "start_week": weeks[start_i],
            "end_week": weeks[end_i],
            "start_date": cal_start.isoformat(),
            "end_date": cal_end.isoformat(),
            "volume": round(avg_vol, 1),
            "delta_muscle": delta_m,
            "start_muscle": round(m_start, 2),
            "end_muscle": round(m_end, 2),
            "daily_steps": round(avg_steps, 1) if avg_steps is not None else None,
            "sleep_hours": round(avg_sleep, 2) if avg_sleep is not None else None,
            "step_coverage": round(step_coverage, 3),
            "sleep_coverage": round(sleep_coverage, 3),
            "coverage_ok": coverage_ok,
            "included_in_model": included_in_model,
        })

    # Filtrera de block som har kompletta data för modellen
    model_blocks = [
        b for b in blocks
        if b["included_in_model"]
        and b["volume"] is not None
        and b["sleep_hours"] is not None
        and b["daily_steps"] is not None
        and b["delta_muscle"] is not None
    ]

    n_model = len(model_blocks)
    feature_names = ["volume", "sleep_hours", "daily_steps"]

    if n_model < 4:
        summary_msg = generate_multivariate_summary(
            r2=0.0, r2_adj=0.0, model_p=1.0, n=n_model, coeffs={}
        )
        empty_result["n_blocks"] = n_model
        empty_result["total_blocks"] = len(blocks)
        empty_result["summary_text"] = summary_msg
        empty_result["blocks_data"] = blocks
        return empty_result

    # Konstruera X och y
    y_arr = np.array([b["delta_muscle"] for b in model_blocks], dtype=float)
    X_raw = np.array([
        [b["volume"], b["sleep_hours"], b["daily_steps"]]
        for b in model_blocks
    ], dtype=float)

    reg_result = run_multiple_linear_regression(
        X_raw=X_raw,
        y=y_arr,
        feature_names=feature_names,
    )

    summary_text = generate_multivariate_summary(
        r2=reg_result["r2"],
        r2_adj=reg_result["r2_adjusted"],
        model_p=reg_result["model_p_value"],
        n=n_model,
        coeffs=reg_result["coefficients"],
    )

    # Berika med namn och enheter för UI
    feature_meta = {
        "volume": {"name": "Träningsvolym", "unit": "kg/vecka"},
        "sleep_hours": {"name": "Sömn", "unit": "h/dag"},
        "daily_steps": {"name": "Vardagssteg", "unit": "steg/dag"},
    }
    for k, meta in feature_meta.items():
        if k in reg_result["coefficients"]:
            reg_result["coefficients"][k].update(meta)

    return {
        "r2": reg_result["r2"],
        "r2_adjusted": reg_result["r2_adjusted"],
        "model_p_value": reg_result["model_p_value"],
        "f_statistic": reg_result["f_statistic"],
        "n_blocks": n_model,
        "total_blocks": len(blocks),
        "degrees_of_freedom": reg_result["df_res"],
        "coefficients": reg_result["coefficients"],
        "intercept": reg_result["intercept"],
        "summary_text": summary_text,
        "blocks_data": blocks,
    }
