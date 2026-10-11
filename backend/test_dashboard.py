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


def test_sync_sheet_updates_do_not_include_one_rm(monkeypatch):
    import asyncio
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY', 'test-key')
    backend_dir = str(Path(__file__).resolve().parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    import main

    row = {
        'user_id': 'Altsten93',
        'category': 'Chest',
        'row_index': 2,
        'exercise': 'Bänkpress',
        'completed': True,
        'workout_date': '2026-01-01',
        'weight_kg': 80,
        'reps': 5,
        'sets': 4,
        'volume': 1600,
        'intensity': 0.7,
        'difficulty': 7,
        'one_rm': 100,
        'ml_predicted_difficulty': 6,
    }

    class FakeQuery:
        def __init__(self, database):
            self.database = database
            self.upsert_rows = None

        def select(self, _columns):
            return self

        def eq(self, _column, _value):
            return self

        def upsert(self, rows, on_conflict):
            self.upsert_rows = rows
            return self

        def execute(self):
            if self.upsert_rows:
                self.database.row.update(self.upsert_rows[0])
            return SimpleNamespace(data=[self.database.row.copy()])

    class FakeDatabase:
        def __init__(self):
            self.row = row.copy()

        def table(self, _name):
            return FakeQuery(self)

    monkeypatch.setattr(main, 'supabase', FakeDatabase())
    payload = main.SyncPayload(
        user_id='Altsten93',
        tabs={'Chest': main.TabData(
            headers=[],
            rows=[['', 'Nej', 80, 5, 4, 1600, 0.7, 7, 105, 6]],
        )},
    )

    result = asyncio.run(main.sync_sheet_data(payload, authorization='******'))

    assert result['updates']['Chest'][0]['completed'] == 'Ja'
    assert 'one_rm' not in result['updates']['Chest'][0]
