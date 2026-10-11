import pytest
import numpy as np
from correlation import compute_volume_muscle_correlation, interpret_correlation


def test_interpret_correlation():
    assert "starkt positivt" in interpret_correlation(0.85)
    assert "måttligt positivt" in interpret_correlation(0.55)
    assert "svagt positivt" in interpret_correlation(0.25)
    assert "försumbart" in interpret_correlation(0.05)
    assert "måttligt negativt" in interpret_correlation(-0.45)


def test_compute_volume_muscle_correlation_empty():
    res = compute_volume_muscle_correlation([], [])
    assert res["timeline"]["weeks"] == []
    assert res["scatter"]["totalWeeks"] == 0
    assert "Ingen mätdata" in res["scatter"]["summaryText"]


def test_compute_volume_muscle_correlation_interpolation_and_imputation():
    # 3 weeks of workouts, but only week 1 and week 3 have scale measurements
    # Week 2 should be linearly interpolated
    workouts = [
        {"workout_date": "2024-01-01", "completed": True, "volume": 1000.0}, # 2024-W01
        {"workout_date": "2024-01-08", "completed": True, "volume": 2000.0}, # 2024-W02
        {"workout_date": "2024-01-15", "completed": True, "volume": 3000.0}, # 2024-W03
    ]
    scale_data = [
        {"measured_at": "2024-01-03T10:00:00Z", "muscle_mass_kg": 70.0}, # 2024-W01
        {"measured_at": "2024-01-17T10:00:00Z", "muscle_mass_kg": 72.0}, # 2024-W03
    ]

    res = compute_volume_muscle_correlation(workouts, scale_data)
    timeline = res["timeline"]
    weeks = timeline["weeks"]

    idx_w01 = weeks.index("2024-W01")
    idx_w02 = weeks.index("2024-W02")
    idx_w03 = weeks.index("2024-W03")

    # Volumes
    assert timeline["volume"][idx_w01] == 1000.0
    assert timeline["volume"][idx_w02] == 2000.0
    assert timeline["volume"][idx_w03] == 3000.0

    # Muscle mass: 70.0 in W01, 72.0 in W03, interpolated 71.0 in W02
    assert timeline["muscleMass"][idx_w01] == 70.0
    assert timeline["isImputed"][idx_w01] is False

    assert timeline["muscleMass"][idx_w02] == 71.0
    assert timeline["isImputed"][idx_w02] is True

    assert timeline["muscleMass"][idx_w03] == 72.0
    assert timeline["isImputed"][idx_w03] is False

    # Scatter & correlation
    scatter = res["scatter"]
    assert "korrelation r =" in scatter["summaryText"].lower()
    assert "stats" in res
    assert "interpretation" in res
    assert "r" in res["stats"]
    assert "pValue" in res["stats"]


def test_compute_volume_muscle_correlation_excludes_imputed_by_default():
    workouts = [
        {"workout_date": "2024-01-01", "completed": True, "volume": 1000.0}, # W01
        {"workout_date": "2024-01-08", "completed": True, "volume": 2000.0}, # W02 (missing scale)
        {"workout_date": "2024-01-15", "completed": True, "volume": 3000.0}, # W03
    ]
    scale_data = [
        {"measured_at": "2024-01-03T10:00:00Z", "muscle_mass_kg": 70.0}, # W01
        {"measured_at": "2024-01-17T10:00:00Z", "muscle_mass_kg": 72.0}, # W03
    ]

    # Default include_imputed=False
    res_default = compute_volume_muscle_correlation(workouts, scale_data, include_imputed=False)
    weekly_default = res_default["weeklyAnalysis"]
    # Should only have 2 points (W01 and W03), excluding W02
    assert weekly_default["n"] == 2
    assert all(not p["is_imputed"] for p in weekly_default["points"])

    # include_imputed=True
    res_imputed = compute_volume_muscle_correlation(workouts, scale_data, include_imputed=True)
    weekly_imputed = res_imputed["weeklyAnalysis"]
    # Should include all 3 points
    assert weekly_imputed["n"] == 3
    assert any(p["is_imputed"] for p in weekly_imputed["points"])


def test_block_aggregation_mesocycles():
    # 16 weeks of workouts and weigh-ins
    workouts = []
    scale_data = []

    for wk in range(1, 17):
        # Even weeks higher volume, increasing over time
        date_str = f"2024-{(wk-1)//4 + 1:02d}-{(wk-1)%4 * 7 + 1:02d}"
        vol = 5000.0 + wk * 500.0
        workouts.append({"workout_date": date_str, "completed": True, "volume": vol})
        
        # Weigh-in: muscle mass increases slightly
        scale_data.append({
            "measured_at": f"{date_str}T08:00:00Z",
            "muscle_mass_kg": round(70.0 + wk * 0.1, 2)
        })

    res = compute_volume_muscle_correlation(
        workouts, scale_data, include_imputed=False, block_size=8, step_size=4
    )

    block_analysis = res["blockAnalysis"]
    assert block_analysis["n"] > 0
    # Every block has avg_vol and delta_m
    for p in block_analysis["points"]:
        assert "x" in p and p["x"] > 0
        assert "y" in p
        assert p["type"] == "block"
        assert "startMuscle" in p
        assert "endMuscle" in p

    assert "r" in block_analysis
    assert "pValue" in block_analysis
    assert "summaryText" in block_analysis


def test_block_aggregation_insufficient_data():
    # Only 3 weeks: not enough for an 8-week block
    workouts = [
        {"workout_date": "2024-01-01", "completed": True, "volume": 1000.0},
        {"workout_date": "2024-01-08", "completed": True, "volume": 2000.0},
    ]
    scale_data = [
        {"measured_at": "2024-01-03T10:00:00Z", "muscle_mass_kg": 70.0},
        {"measured_at": "2024-01-10T10:00:00Z", "muscle_mass_kg": 70.5},
    ]

    res = compute_volume_muscle_correlation(workouts, scale_data, block_size=8)
    assert res["blockAnalysis"]["n"] == 0
    # Fallback to weekly analysis
    assert res["stats"]["method"] == "veckovis mätpunkt"

