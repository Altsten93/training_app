from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import numpy as np
import httpx
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import io
import asyncio

app = FastAPI(title="Workout Brain API", version="1.0.0")

@app.get("/")
async def root():
    return {"status": "ok", "message": "Workout Brain API is running"}

# ==========================================
# 1. CORS-INSTÄLLNINGAR (Tillåt din Netlify-app)
# ==========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # I produktion kan du sätta t.ex. ["https://din-app.netlify.app"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 2. KONFIGURATION & LÄNKAR
# ==========================================
CONFIG = {
    "chest": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTZEwzPvfWQhcYGCjEShCyYoSSelbrQkTI7Mu6hVRgw190wDS0o84OQjTOSWdxje62AJ62bCMOVpSI7/pub?gid=0&single=true&output=csv",
    "back": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTZEwzPvfWQhcYGCjEShCyYoSSelbrQkTI7Mu6hVRgw190wDS0o84OQjTOSWdxje62AJ62bCMOVpSI7/pub?gid=1317122870&single=true&output=csv",
    "legs": "https://docs.google.com/spreadsheets/d/e/2PACX-1vTZEwzPvfWQhcYGCjEShCyYoSSelbrQkTI7Mu6hVRgw190wDS0o84OQjTOSWdxje62AJ62bCMOVpSI7/pub?gid=1972507766&single=true&output=csv",
    "scriptUrl": "https://script.google.com/macros/s/AKfycbygMk5VLSori47VCZf2LvW9HIJgzN93Rg4XArJ6Rc52-xY7vPUn0WYBWQhYyuFAbWS9/exec",
    "mlModelUrl": "https://workout-brain-155687864714.europe-west1.run.app",
}

WEEKLY_GOAL_KG = 12000.0
WORKOUT_ORDER = ["Chest", "Back", "Legs"]

# ==========================================
# 3. DATA-MODELLER (Pydantic)
# ==========================================
class CompleteWorkoutRequest(BaseModel):
    workoutType: str
    originalRowIndex: int
    difficulty: float
    date: Optional[str] = None  # Format: YYYY-MM-DD (lämnas tom för dagens datum)


# ==========================================
# 4. HJÄLPFUNKTIONER FÖR DATABEHANDLING
# ==========================================
async def fetch_all_workouts() -> pd.DataFrame:
    """Hämtar alla 3 Google Sheets asynkront och slår ihop till en DataFrame."""
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
        timestamp = int(datetime.now().timestamp() * 1000)
        responses = await asyncio.gather(
            client.get(f"{CONFIG['chest']}&t={timestamp}", headers=headers),
            client.get(f"{CONFIG['back']}&t={timestamp}", headers=headers),
            client.get(f"{CONFIG['legs']}&t={timestamp}", headers=headers)
        )
    
    dfs = []
    types = ["Chest", "Back", "Legs"]
    
    for resp, workout_type in zip(responses, types):
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Kunde inte hämta data för {workout_type}")
        
        # Läs CSV från text
        df = pd.read_csv(io.StringIO(resp.text))
        df["workoutType"] = workout_type
        # Radnummer i kalkylarket (1-indexerat med hänsyn till rubrikrad)
        df["originalRowIndex"] = df.index + 2
        dfs.append(df)
        
    combined_df = pd.concat(dfs, ignore_index=True)
    
    # Standardisera status och datum
    if "Completed_workout" in combined_df.columns:
        combined_df["is_completed"] = combined_df["Completed_workout"].astype(str).str.strip().str.lower().isin(["ja", "yes", "true", "1"])
    else:
        combined_df["is_completed"] = False

    if "Datum" in combined_df.columns:
        combined_df["parsed_date"] = pd.to_datetime(
            combined_df["Datum"], format="mixed", dayfirst=True, errors="coerce"
        )
    else:
        combined_df["parsed_date"] = pd.NaT

    # Räkna ut total volym per rad genom att summera alla kolumner som slutar på '_volym'
    vol_cols = [c for c in combined_df.columns if c.endswith("_volym")]
    if vol_cols:
        combined_df["total_volym"] = combined_df[vol_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    else:
        combined_df["total_volym"] = 0.0

    return combined_df


def extract_exercises(row: pd.Series) -> List[Dict[str, Any]]:
    """Extraherar alla övningar med vikt, reps och set från en rad."""
    exercises = []
    for col in row.index:
        if col.endswith("_KG") and pd.notna(row[col]) and str(row[col]).strip() != "":
            base_name = col[:-3]
            clean_name = base_name.replace("_", " ").title()
            
            reps_col = f"{base_name}_reps"
            set_col = f"{base_name}_set"
            
            exercises.append({
                "name": clean_name,
                "kg": str(row[col]),
                "reps": str(row.get(reps_col, "N/A")),
                "sets": str(row.get(set_col, "N/A"))
            })
    return exercises


def get_funny_message(days_since: Optional[int]) -> str:
    """Genererar ett meddelande baserat på hur länge sedan det var man tränade."""
    if days_since is None:
        return "Inget tidigare pass registrerat i denna kategori."
    if days_since == 0:
        return "You trained this today."
    elif days_since == 1:
        return "You trained this yesterday."
    elif days_since == 7:
        return "You trained this a week ago."
    elif days_since > 9:
        return f"A wooo, get your fat ass to the gym, it has been {days_since} days ago !!!"
    elif days_since > 0:
        return f"It has been {days_since} days since your last workout."
    return ""


# ==========================================
# 5. API-ENDPOINTS
# ==========================================

import asyncio

@app.get("/api/workout/next")
async def get_next_workout(group_index: Optional[int] = Query(None, ge=0, le=2)):
    """
    Hämtar nästa schemalagda pass och räknar ut dagar sedan förra passet i samma kategori.
    Om group_index utelämnas väljs den muskelgrupp som tränades längst sedan automatiskt.
    """
    df = await fetch_all_workouts()
    
    # 1. Identifiera senaste genomförda datum för respektive grupp
    completed = df[df["is_completed"] & df["parsed_date"].notna()]
    last_dates = {}
    for group in WORKOUT_ORDER:
        group_completed = completed[completed["workoutType"] == group]
        if not group_completed.empty:
            last_dates[group] = group_completed["parsed_date"].max()
        else:
            last_dates[group] = None

    # 2. Välj grupp
    if group_index is None:
        # Sortera grupper efter äldst datum först (None = aldrig kört = högst prioritet)
        sorted_groups = sorted(
            WORKOUT_ORDER,
            key=lambda g: (last_dates[g] is not None, last_dates[g] or datetime.min)
        )
        selected_group = sorted_groups[0]
        active_group_index = WORKOUT_ORDER.index(selected_group)
    else:
        active_group_index = group_index
        selected_group = WORKOUT_ORDER[active_group_index]

    # 3. Hämta första oavslutade passet i den valda gruppen
    uncompleted = df[(df["workoutType"] == selected_group) & (~df["is_completed"])].sort_values("originalRowIndex")
    
    if uncompleted.empty:
        return {
            "allCompleted": True,
            "groupIndex": active_group_index,
            "workoutType": selected_group,
            "nextWorkout": None,
            "message": "Alla pass i denna kategori är slutförda!"
        }

    next_row = uncompleted.iloc[0]
    
    # 4. Räkna ut dagar sedan förra passet
    last_date = last_dates.get(selected_group)
    days_since = (datetime.now().date() - last_date.date()).days if last_date else None
    
    return {
        "allCompleted": False,
        "groupIndex": active_group_index,
        "workoutType": selected_group,
        "originalRowIndex": int(next_row["originalRowIndex"]),
        "daysSinceLastWorkout": days_since,
        "message": get_funny_message(days_since),
        "exercises": extract_exercises(next_row)
    }


@app.post("/api/workout/complete")
async def complete_workout(req: CompleteWorkoutRequest):
    """Uppdaterar Google Sheets via ditt Google Apps Script med datum och svårighetsgrad."""
    today_str = req.date or datetime.now().strftime("%Y-%m-%d")
    
    payload = {
        "sheetName": req.workoutType,
        "rowIndex": req.originalRowIndex,
        "date": today_str,
        "difficulty": float(req.difficulty)
    }
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(CONFIG["scriptUrl"], json=payload)
            result = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"status": "ok"}
            return {"status": "success", "message": "Pass markerat som klart!", "result": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Kunde inte uppdatera Google Sheet: {str(e)}")


@app.post("/api/model/retrain")
async def retrain_model():
    """Triggar omträning av ML-modellen på Cloud Run."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(CONFIG["mlModelUrl"])
            return {
                "status": "success" if resp.status_code == 200 else "error",
                "statusCode": resp.status_code,
                "serverMessage": resp.text
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Fel vid anrop till ML-tjänsten: {str(e)}")


@app.get("/api/dashboard")
async def get_dashboard():
    """
    Räknar ut all data för dashboarden:
    - Veckomål och nuvarande veckovolym (Pie/Doughnut)
    - 6-veckors rullande volym per muskelgrupp (Line chart)
    - Totalt antal genomförda pass (Bar chart)
    - Normaliserad intensitet vs svårighetsgrad (Adaption chart)
    """
    df = await fetch_all_workouts()
    completed = df[df["is_completed"] & df["parsed_date"].notna()].copy()
    
    if completed.empty:
        return {"empty": True}

    now = datetime.now()
    current_year, current_week, _ = now.isocalendar()

    # --- 1. Nuvarande veckovolym ---
    completed["iso_year"] = completed["parsed_date"].dt.isocalendar().year
    completed["iso_week"] = completed["parsed_date"].dt.isocalendar().week
    
    current_week_df = completed[
        (completed["iso_year"] == current_year) & 
        (completed["iso_week"] == current_week)
    ]
    
    weekly_by_type = {"Chest": 0.0, "Back": 0.0, "Legs": 0.0}
    for w_type in WORKOUT_ORDER:
        vol = current_week_df[current_week_df["workoutType"] == w_type]["total_volym"].sum()
        weekly_by_type[w_type] = round(float(vol), 1)
        
    current_week_total = sum(weekly_by_type.values())
    percentage = min(round((current_week_total / WEEKLY_GOAL_KG) * 100, 1), 100.0)
    remaining_vol = max(0.0, round(WEEKLY_GOAL_KG - current_week_total, 1))

    # --- 2. Rullande 6-veckors volym ---
    completed["year_week"] = (
        completed["iso_year"].astype(str) + "-W" + 
        completed["iso_week"].astype(str).str.zfill(2)
    )
    
    # Skapa pivot-tabell: veckor som rader, muskelgrupp som kolumner
    pivot_vol = completed.pivot_table(
        index="year_week", columns="workoutType", values="total_volym", aggfunc="sum"
    ).fillna(0)
    
    # Beräkna rullande medelvärde med fönster på 6
    rolling_vol = pivot_vol.rolling(window=6, min_periods=1).mean().round(1)
    
    all_weeks = list(rolling_vol.index)
    if len(all_weeks) > 12:
        all_weeks = all_weeks[-12:]
    rolling_vol = rolling_vol.loc[all_weeks]

    volume_datasets = []
    colors = {"Chest": "#48BB78", "Back": "#F56565", "Legs": "#4299E1"}

    for w_type in WORKOUT_ORDER:
        data = list(rolling_vol[w_type].values) if w_type in rolling_vol.columns else [0] * len(all_weeks)
        volume_datasets.append({
            "label": f"{w_type} Volume (6-Week Avg)",
            "data": data,
            "borderColor": colors[w_type],
            "borderWidth": 2,
            "fill": False,
            "pointRadius": 2
        })

    # --- 3. Pass per kategori (Total Sessions) ---
    session_counts = completed["workoutType"].value_counts().to_dict()
    sessions_data = {
        "labels": WORKOUT_ORDER,
        "data": [int(session_counts.get(w_type, 0)) for w_type in WORKOUT_ORDER]
    }

    # --- 4. Adaptionsgraf (Senaste 12 månaderna, för att få med all data som är relevant) ---
    adaption_window_start = now - timedelta(days=365)
    recent_df = completed[completed["parsed_date"] >= adaption_window_start].copy()
    
    adaption_mapping = {
        "Chest": {"intensity": "Bänkpress_intensity", "difficulty": "Chest_difficulty", "color": "#FFD700", "dash": [5, 5]},
        "Back": {"intensity": "Deadlift_intensity", "difficulty": "Back_difficulty", "color": "#9370DB", "dash": [2, 3]},
        "Legs": {"intensity": "Squat_intensity", "difficulty": "Legs_difficulty", "color": "#00BFFF", "dash": [10, 3]},
    }
    
    adaption_points = []
    for _, row in recent_df.iterrows():
        w_type = row["workoutType"]
        if w_type not in adaption_mapping:
            continue
        
        int_col = adaption_mapping[w_type]["intensity"]
        diff_col = adaption_mapping[w_type]["difficulty"]
        
        try:
            val_int = float(row[int_col]) if int_col in row and pd.notna(row[int_col]) else np.nan
            val_diff = float(row[diff_col]) if diff_col in row and pd.notna(row[diff_col]) else np.nan
            
            if not np.isnan(val_int) and not np.isnan(val_diff):
                adaption_points.append({
                    "date": row["parsed_date"],
                    "workoutType": w_type,
                    "intensity": val_int,
                    "difficulty": val_diff
                })
        except (ValueError, TypeError):
            continue

    adaption_datasets = []
    if adaption_points:
        ad_df = pd.DataFrame(adaption_points)
        
        # Min-Max Normalisering (0 till 1)
        min_int, max_int = ad_df["intensity"].min(), ad_df["intensity"].max()
        min_diff, max_diff = ad_df["difficulty"].min(), ad_df["difficulty"].max()
        
        ad_df["norm_intensity"] = 0.5 if min_int == max_int else (ad_df["intensity"] - min_int) / (max_int - min_int)
        ad_df["norm_difficulty"] = 0.5 if min_diff == max_diff else (ad_df["difficulty"] - min_diff) / (max_diff - min_diff)
        ad_df["adaption"] = (ad_df["norm_intensity"] - ad_df["norm_difficulty"]).round(3)
        
        for w_type in WORKOUT_ORDER:
            type_df = ad_df[ad_df["workoutType"] == w_type].sort_values("date")
            if not type_df.empty:
                chart_data = [{"x": d.strftime("%Y-%m-%d"), "y": float(y)} for d, y in zip(type_df["date"], type_df["adaption"])]
                adaption_datasets.append({
                    "label": f"{w_type}_adaption",
                    "data": chart_data,
                    "borderColor": adaption_mapping[w_type]["color"],
                    "backgroundColor": f"{adaption_mapping[w_type]['color']}80",
                    "borderDash": adaption_mapping[w_type]["dash"],
                    "tension": 0.4,
                    "borderWidth": 2
                })

    return {
        "weeklyProgress": {
            "currentWeekVolume": current_week_total,
            "weeklyGoal": WEEKLY_GOAL_KG,
            "percentage": percentage,
            "remaining": remaining_vol,
            "volumeByType": weekly_by_type
        },
        "volumeChart": {
            "labels": all_weeks,
            "datasets": volume_datasets
        },
        "sessionsChart": sessions_data,
        "adaptionChart": {
            "datasets": adaption_datasets,
            "minDate": adaption_window_start.strftime("%Y-%m-%d"),
            "maxDate": now.strftime("%Y-%m-%d")
        }
    }