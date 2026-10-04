import sys
from pathlib import Path

import pandas as pd

backend_dir = str(Path(__file__).resolve().parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from ml.engine import WorkoutDifficultyPredictor


def test_workout_difficulty_predictor():
    train_data = {
        "category": ["Chest", "Chest", "Back", "Back", "Legs", "Legs"],
        "exercise": ["Bänkpress", "Bänkpress", "Deadlift", "Deadlift", "Squat", "Squat"],
        "weight_kg": [60.0, 70.0, 100.0, 110.0, 80.0, 90.0],
        "reps": [10, 8, 5, 5, 10, 8],
        "sets": [4, 4, 3, 3, 4, 4],
        "volume": [2400.0, 2240.0, 1500.0, 1650.0, 3200.0, 2880.0],
        "intensity": [0.7, 0.8, 1.2, 1.3, 0.9, 1.0],
        "completed": [True, True, True, True, True, True],
        "difficulty": [6.0, 7.5, 7.0, 8.5, 6.5, 8.0],
    }
    df = pd.DataFrame(train_data)

    predictor = WorkoutDifficultyPredictor()
    metrics = predictor.train(df)

    assert metrics["status"] == "success"
    assert metrics["trained_samples"] == 6
    assert metrics["mae"] >= 0.0

    test_data = {
        "category": ["Chest"],
        "exercise": ["Bänkpress"],
        "weight_kg": [65.0],
        "reps": [10],
        "sets": [4],
        "volume": [2600.0],
        "intensity": [0.75],
    }
    test_df = pd.DataFrame(test_data)
    preds = predictor.predict(test_df)

    assert len(preds) == 1
    assert 1.0 <= preds[0] <= 10.0
