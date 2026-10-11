"""
test_multivariate.py

Enhetstester för multivariat regressionsanalys av träningsvolym, sömn och vardagssteg
mot muskeltillväxt.
"""

from datetime import datetime, timedelta
import numpy as np
import pytest

from multivariate import (
    clean_health_records,
    calculate_vif_scores,
    run_multiple_linear_regression,
    generate_multivariate_summary,
    compute_multivariate_lifestyle_model,
)


def test_clean_health_records_flat_and_nested():
    flat_data = [
        {"date": "2026-10-01", "step_count": 8500, "sleep_total_hours": 7.5},
        {"date": "2026-10-02T08:00:00Z", "step_count": None, "sleep_total_hours": 6.8},
        {"date": "2026-10-03 00:00:00 +0200", "step_count": 10200, "total_sleep_hours": None},
    ]
    res_flat = clean_health_records(flat_data)
    assert "2026-10-01" in res_flat
    assert res_flat["2026-10-01"]["steps"] == 8500.0
    assert res_flat["2026-10-01"]["sleep"] == 7.5

    assert res_flat["2026-10-02"]["steps"] is None
    assert res_flat["2026-10-02"]["sleep"] == 6.8

    assert res_flat["2026-10-03"]["steps"] == 10200.0
    assert res_flat["2026-10-03"]["sleep"] is None

    # Testa även rå nested struktur från Auto Export / Apple Watch
    nested_data = [
        {
            "id": 100,
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "data": [{"date": "2026-10-05 00:00:00 +0200", "qty": 9400}],
                    },
                    {
                        "name": "sleep_analysis",
                        "data": [{"date": "2026-10-05 00:00:00 +0200", "totalSleep": 8.1}],
                    },
                ]
            },
        }
    ]
    res_nested = clean_health_records(nested_data)
    assert "2026-10-05" in res_nested
    assert res_nested["2026-10-05"]["steps"] == 9400.0
    assert res_nested["2026-10-05"]["sleep"] == 8.1


def test_calculate_vif_orthogonal_and_collinear():
    np.random.seed(42)
    n = 40
    # 3 ortogonala variabler
    x1 = np.random.randn(n)
    x2 = np.random.randn(n)
    x3 = np.random.randn(n)
    X_ortho = np.column_stack([x1, x2, x3])
    vifs_ortho = calculate_vif_scores(X_ortho)
    assert len(vifs_ortho) == 3
    for v in vifs_ortho:
        assert 0.8 <= v <= 2.0  # Låg VIF indikerar ingen multikollinearitet

    # Starkt kollineära variabler
    x_collinear = 2.0 * x1 + np.random.randn(n) * 0.01
    X_coll = np.column_stack([x1, x_collinear, x3])
    vifs_coll = calculate_vif_scores(X_coll)
    assert vifs_coll[0] > 10.0 or vifs_coll[1] > 10.0


def test_run_multiple_linear_regression_perfect_fit():
    np.random.seed(123)
    n = 25
    # X1 = volym (skala 5000-15000), X2 = sömn (5-9h), X3 = steg (5000-12000)
    x1 = np.random.uniform(6000, 14000, n)
    x2 = np.random.uniform(6.0, 8.5, n)
    x3 = np.random.uniform(6000, 11000, n)
    X = np.column_stack([x1, x2, x3])

    # Känd modell: y = 1.0 + 0.0002 * x1 + 0.3 * x2 - 0.0001 * x3
    y = 1.0 + 0.0002 * x1 + 0.3 * x2 - 0.0001 * x3

    reg = run_multiple_linear_regression(X, y, ["volume", "sleep_hours", "daily_steps"])

    assert reg["r2"] > 0.99
    assert reg["r2_adjusted"] > 0.99
    assert reg["f_statistic"] > 50.0
    assert reg["model_p_value"] < 0.001
    assert reg["df_model"] == 3
    assert reg["df_res"] == n - 3 - 1

    coeffs = reg["coefficients"]
    assert "volume" in coeffs
    assert "sleep_hours" in coeffs
    assert "daily_steps" in coeffs

    # Kontrollera råa koefficienter och konfidensintervall
    assert abs(coeffs["volume"]["raw"] - 0.0002) < 0.00001
    assert abs(coeffs["sleep_hours"]["raw"] - 0.3) < 0.01
    assert abs(coeffs["daily_steps"]["raw"] - (-0.0001)) < 0.00001

    # Standardiserade beta ska finnas och vara normaliserade
    assert coeffs["volume"]["standardized_beta"] != 0.0
    assert coeffs["volume"]["ci_lower"] <= coeffs["volume"]["raw"] <= coeffs["volume"]["ci_upper"]


def test_run_multiple_linear_regression_too_few_samples():
    # n = 3 och p = 3 ger df_res = 3 - 3 - 1 = -1 <= 0 (underdeterminerat)
    X = np.array([[1000, 7.0, 8000], [2000, 8.0, 9000], [3000, 7.5, 8500]])
    y = np.array([0.5, 1.2, 0.8])

    reg = run_multiple_linear_regression(X, y, ["volume", "sleep_hours", "daily_steps"])
    assert reg["r2"] == 0.0
    assert reg["model_p_value"] == 1.0
    assert reg["coefficients"]["volume"]["p_value"] == 1.0


def test_generate_multivariate_summary():
    coeffs = {
        "volume": {"standardized_beta": 0.58, "p_value": 0.025},
        "sleep_hours": {"standardized_beta": 0.28, "p_value": 0.081},
        "daily_steps": {"standardized_beta": -0.15, "p_value": 0.240},
    }
    summary = generate_multivariate_summary(
        r2=0.54, r2_adj=0.42, model_p=0.012, n=12, coeffs=coeffs
    )
    assert "Träningsvolym" in summary
    assert "starkaste" in summary
    assert "0.58" in summary
    assert "sömn" in summary.lower()
    assert "54 %" in summary
    assert "12 mesocykelblock" in summary


def test_multivariate_lifestyle_model_coverage_handling():
    """
    Verifierar att block med <50% datatäckning flaggas och exkluderas från OLS-modellen
    när exclude_low_coverage=True.
    """
    # 24 veckor av träningsdata och invägningar
    workouts = []
    scale_data = []
    health_data = []

    start_date = datetime(2024, 1, 1)

    for i in range(24 * 7):
        curr_d = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")

        # Pass 3 ggr i veckan
        if i % 7 in (1, 3, 5):
            workouts.append({
                "workout_date": curr_d,
                "completed": True,
                "volume": 3500.0,
            })

        # Daglig Apple Health: fullständig täckning för första 12 veckorna,
        # men därefter endast 1 dag per vecka (< 15% täckning) för de sista 12 veckorna
        if i < 12 * 7:
            health_data.append({
                "date": curr_d,
                "step_count": 8000 + (i % 5) * 400,
                "sleep_total_hours": 7.0 + (i % 3) * 0.4,
            })
        else:
            if i % 7 == 0:
                health_data.append({
                    "date": curr_d,
                    "step_count": 6000,
                    "sleep_total_hours": 6.5,
                })

    # Skala: invägning varje vecka
    for wk in range(24):
        wk_d = (start_date + timedelta(days=wk * 7 + 2)).strftime("%Y-%m-%d")
        scale_data.append({
            "measured_at": f"{wk_d}T07:30:00Z",
            "muscle_mass_kg": round(72.0 + wk * 0.08, 2),
        })

    # Med min_coverage = 0.5 och exclude_low_coverage = True
    res_filtered = compute_multivariate_lifestyle_model(
        workouts=workouts,
        scale_data=scale_data,
        health_data=health_data,
        block_size=8,
        step_size=4,
        min_coverage=0.5,
        exclude_low_coverage=True,
    )

    blocks = res_filtered["blocks_data"]
    assert len(blocks) > 0

    # Kontrollera att täckningsprocent räknats ut per block
    has_high_cov = False
    has_low_cov = False
    for b in blocks:
        assert "step_coverage" in b
        assert "sleep_coverage" in b
        assert "coverage_ok" in b
        assert "included_in_model" in b
        if b["coverage_ok"]:
            has_high_cov = True
            assert b["included_in_model"] is True
            assert b["step_coverage"] >= 0.5
            assert b["sleep_coverage"] >= 0.5
        else:
            has_low_cov = True
            assert b["included_in_model"] is False

    assert has_high_cov
    assert has_low_cov

    # Modellen ska enbart räkna med block där included_in_model=True
    assert res_filtered["n_blocks"] < len(blocks)
    assert res_filtered["n_blocks"] >= 2


def test_multivariate_lifestyle_model_null_averaging():
    """
    Verifierar att snitt beräknas strikt över de dagar som har faktiska mätningar (WHERE step_count IS NOT NULL
    respektive sleep_total_hours IS NOT NULL).
    """
    start_date = datetime(2024, 1, 1)
    workouts = []
    scale_data = []
    health_data = []

    # 8 veckor = 56 dagar
    step_vals = []
    sleep_vals = []
    for i in range(56):
        d_str = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
        if i % 7 in (1, 4):
            workouts.append({"workout_date": d_str, "completed": True, "volume": 5000.0})

        # Steg finns varannan dag
        st = None
        if i % 2 == 0:
            st = 8000.0 + i * 10
            step_vals.append(st)

        # Sömn finns var 3:e dag
        sl = None
        if i % 3 == 0:
            sl = 7.0 + (i % 5) * 0.2
            sleep_vals.append(sl)

        health_data.append({"date": d_str, "step_count": st, "sleep_total_hours": sl})

    # Start och slut vägning
    scale_data.append({"measured_at": "2024-01-02T08:00:00Z", "muscle_mass_kg": 70.0})
    scale_data.append({"measured_at": "2024-02-24T08:00:00Z", "muscle_mass_kg": 71.5})

    res = compute_multivariate_lifestyle_model(
        workouts=workouts,
        scale_data=scale_data,
        health_data=health_data,
        block_size=8,
        min_coverage=0.3,
        exclude_low_coverage=False,
    )

    assert len(res["blocks_data"]) == 1
    block = res["blocks_data"][0]

    # Förväntat snitt beräknat enbart på dagar med data
    expected_avg_steps = round(float(np.mean(step_vals)), 1)
    expected_avg_sleep = round(float(np.mean(sleep_vals)), 2)

    assert block["daily_steps"] == expected_avg_steps
    assert block["sleep_hours"] == expected_avg_sleep
    assert block["step_coverage"] == round(len(step_vals) / 56, 3)
    assert block["sleep_coverage"] == round(len(sleep_vals) / 56, 3)


def test_multivariate_lifestyle_model_empty_and_insufficient_data():
    empty_res = compute_multivariate_lifestyle_model([], [], [])
    assert empty_res["r2"] == 0.0
    assert empty_res["n_blocks"] == 0
    assert empty_res["blocks_data"] == []
    assert "Otillräcklig data" in empty_res["summary_text"]


def test_multivariate_api_endpoint_structure():
    """Verifierar att API-endpointen /api/stats/multivariate-analysis svarar enligt kravspecifikationen."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    response = client.get("/api/stats/multivariate-analysis")
    assert response.status_code == 200
    data = response.json()

    required_keys = {"r2", "r2_adjusted", "model_p_value", "n_blocks", "coefficients", "blocks_data"}
    assert required_keys.issubset(data.keys())

    coeffs = data["coefficients"]
    for pred in ("volume", "sleep_hours", "daily_steps"):
        assert pred in coeffs
        assert "raw" in coeffs[pred]
        assert "standardized_beta" in coeffs[pred]
        assert "p_value" in coeffs[pred]
        assert "vif" in coeffs[pred]

