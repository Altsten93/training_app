import pandas as pd


def test_workout_columns_have_expected_datatypes():
    data = {
        'Datum': ['01/01/2026', '02/01/2026'],
        'Completed_workout': ['Ja', 'Ja'],
        'Bänkpress_KG': [80, 100],
        'Bänkpress_reps': [5, 10],
        'Bänkpress_set': [4, 10],
        'Bänkpress_volym': [1600, 10000],
        'Bänkpress_intensity': [0.7, 2.2],
        'Chest_difficulty': [6, 6],
        'Chest_1RM': [110.0, 180.0],
        'ML_Predicted_Difficulty': [6, 6],
    }
    df = pd.DataFrame(data)

    assert df['Bänkpress_KG'].dtype.kind in 'if'
    assert df['Completed_workout'].astype(str).str.lower().str.contains('ja|nej').any()
    assert df['Chest_difficulty'].between(1, 10).all()


def test_dashboard_contract_has_required_keys():
    payload = {
        'weeklyProgress': {
            'currentWeekVolume': 1000,
            'weeklyGoal': 12000,
            'percentage': 8.3,
            'remaining': 11000,
            'volumeByType': {'Chest': 500, 'Back': 300, 'Legs': 200},
        },
        'volumeChart': {'labels': ['2026-W01'], 'datasets': [{'label': 'Chest Volume (6-Week Avg)', 'data': [500]}]},
        'sessionsChart': {'labels': ['Chest', 'Back', 'Legs'], 'data': [1, 2, 3]},
        'adaptionChart': {'datasets': [], 'minDate': '2026-01-01', 'maxDate': '2026-08-17'},
    }

    assert set(payload.keys()) == {'weeklyProgress', 'volumeChart', 'sessionsChart', 'adaptionChart'}
    assert set(payload['weeklyProgress'].keys()) == {'currentWeekVolume', 'weeklyGoal', 'percentage', 'remaining', 'volumeByType'}
