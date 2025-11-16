import functions_framework
import gspread
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import r2_score, mean_absolute_error

# === NEW: Import for secure, file-less authentication ===
import google.auth

# Config: List the exact names of the tabs you want to process
TARGET_TABS = ['Chest', 'Back', 'Legs']

def process_tab(worksheet):
    """
    Trains a model for a single worksheet dynamically finding columns.
    """
    # 1. Get Data
    data = worksheet.get_all_records()
    df = pd.DataFrame(data)
    
    if df.empty:
        return f"Skipped {worksheet.title}: Empty."

    # 2. Dynamic Column Finder
    headers = df.columns.tolist()
    
    # Add date parsing
    df['parsed_date'] = pd.to_datetime(df['Datum'], format='%d/%m/%Y', errors='coerce')
    
    try:
        kg_col = next(h for h in headers if '_KG' in h)
        reps_col = next(h for h in headers if '_reps' in h)
        sets_col = next(h for h in headers if '_set' in h)
        target_col = next(h for h in headers if '_difficulty' in h)
        
    except StopIteration:
        return f"Skipped {worksheet.title}: Could not find required columns (KG, reps, set, difficulty)."

    # 3. Clean Training Data
    train_df = df[df[target_col] != ''].copy()
    
    cols_to_numeric = [kg_col, reps_col, sets_col, target_col]
    for col in cols_to_numeric:
        train_df[col] = pd.to_numeric(train_df[col], errors='coerce')
    
    train_df.dropna(subset=cols_to_numeric + ['parsed_date'], inplace=True)
    
    training_rows = len(train_df)
    if training_rows < 5:
        return f"Skipped {worksheet.title}: Not enough data to train ({training_rows} rows)."

    # 4. Train Model
    X_train = train_df[[kg_col, reps_col, sets_col]]
    y_train = train_df[target_col]
    
    # Calculate Time-Decay Weights
    today = pd.to_datetime('today')
    days_old = (today - train_df['parsed_date']).dt.days
    
    max_age_for_weighting = 730.0 
    sample_weights = 1.0 - (days_old / max_age_for_weighting)
    sample_weights = sample_weights.clip(lower=0.01) 
    
    model = RandomForestRegressor(n_estimators=100, random_state=42)
    model.fit(X_train, y_train, sample_weight=sample_weights)

    # 5. Predict for ALL rows
    all_X = df[[kg_col, reps_col, sets_col]].copy()
    all_X = all_X.apply(pd.to_numeric, errors='coerce').fillna(0)
    
    predictions = model.predict(all_X)
    
    # 6. Update Column J
    update_values = [[round(float(x), 1)] for x in predictions]
    worksheet.update(range_name=f'J2:J{len(update_values)+1}', values=update_values)
    worksheet.update_cell(1, 10, "ML_Predicted_Difficulty")
    
    # 7. CALCULATE METRICS
    train_predictions = model.predict(X_train)
    r2 = r2_score(y_train, train_predictions)
    mae = mean_absolute_error(y_train, train_predictions)
    
    importances = model.feature_importances_
    features = {
        'KG': importances[0],
        'Reps': importances[1],
        'Sets': importances[2]
    }
    
    stats_report = (
        f"Success: {worksheet.title} (Trained on {training_rows} rows, time-weighted)\n"
        f"  - R-squared: {r2:.2f} (1.0 is perfect)\n"
        f"  - MAE: {mae:.2f} (Avg error in difficulty points)\n"
        f"  - Feature Importance:\n"
        f"    - KG:   {features['KG']:.1%}\n"
        f"    - Reps: {features['Reps']:.1%}\n"
        f"    - Sets: {features['Sets']:.1%}\n"
    )
    
    return stats_report


@functions_framework.http
def retrain_model(request):
    # --- CORS HEADERS ---
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '3600'
        }
        return ('', 204, headers)

    headers = {'Access-Control-Allow-Origin': '*'}
    headers['Content-Type'] = 'text/plain'

    try:
        # === NEW AUTHENTICATION METHOD ===
        # This automatically finds the service account credentials in the Cloud Run environment.
        # No more credentials.json file!
        scopes = ['https://www.googleapis.com/auth/spreadsheets',
                  'https://www.googleapis.com/auth/drive']
        credentials, _ = google.auth.default(scopes=scopes)
        gc = gspread.authorize(credentials)
        # === END OF NEW AUTH METHOD ===

        sh = gc.open("Träningsschema 2025/2026") 

        results = []
        for tab_name in TARGET_TABS:
            try:
                worksheet = sh.worksheet(tab_name)
                status = process_tab(worksheet)
                results.append(status)
            except gspread.WorksheetNotFound:
                results.append(f"Error: Tab '{tab_name}' not found.")
            except Exception as e:
                results.append(f"Error processing {tab_name}: {str(e)}")

        return ('\n'.join(results), 200, headers)

    except Exception as e:
        return (f'Critical Error: {str(e)}', 500, headers)