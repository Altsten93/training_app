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


def test_apply_overrides_marks_workout_completed():
    import sys
    from pathlib import Path
    backend_dir = str(Path(__file__).resolve().parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from main import apply_overrides, COMPLETED_OVERRIDES
    
    raw_data = {
        'workoutType': ['Chest', 'Chest', 'Back'],
        'originalRowIndex': [277, 278, 280],
        'Completed_workout': ['Ja', 'Nej', 'Nej'],
        'is_completed': [True, False, False],
        'Datum': ['2026-09-12', '', ''],
        'parsed_date': [pd.to_datetime('2026-09-12'), pd.NaT, pd.NaT],
        'Chest_difficulty': [7.0, None, None]
    }
    df = pd.DataFrame(raw_data)
    
    # Innan override
    assert not df.loc[df['originalRowIndex'] == 278, 'is_completed'].iloc[0]
    
    # Simulera att pass 278 har genomförts
    COMPLETED_OVERRIDES[('chest', 278)] = {'date': '2026-09-14', 'difficulty': 8.5}
    
    updated_df = apply_overrides(df)
    
    row_278 = updated_df[updated_df['originalRowIndex'] == 278].iloc[0]
    assert row_278['is_completed'] == True
    assert row_278['Completed_workout'] == 'Ja'
    assert row_278['Datum'] == '2026-09-14'
    assert row_278['parsed_date'] == pd.to_datetime('2026-09-14')
    assert row_278['Chest_difficulty'] == 8.5
    
    # Rensa
    COMPLETED_OVERRIDES.clear()


def test_get_upcoming_sessions_returns_first_three_uncompleted_per_category():
    import sys
    from pathlib import Path
    backend_dir = str(Path(__file__).resolve().parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from main import get_upcoming_sessions

    workouts = [
        {'category': 'Chest', 'row_index': 4, 'exercise': 'Bench 4', 'completed': False},
        {'category': 'Chest', 'row_index': 2, 'exercise': 'Bench 2', 'completed': False},
        {'category': 'Chest', 'row_index': 1, 'exercise': 'Bench 1', 'completed': True},
        {'category': 'Chest', 'row_index': 3, 'exercise': 'Bench 3', 'completed': False},
        {'category': 'Chest', 'row_index': 5, 'exercise': 'Bench 5', 'completed': False},
        {'category': 'Back', 'row_index': 1, 'exercise': 'Deadlift', 'completed': False},
    ]

    result = get_upcoming_sessions(workouts)

    chest_sessions = next(group['sessions'] for group in result if group['category'] == 'Chest')
    back_sessions = next(group['sessions'] for group in result if group['category'] == 'Back')
    legs_sessions = next(group['sessions'] for group in result if group['category'] == 'Legs')
    assert [session['name'] for session in chest_sessions] == ['Bench 2', 'Bench 3', 'Bench 4']
    assert len(back_sessions) == 1
    assert legs_sessions == []
