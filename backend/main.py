import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import Client, create_client

load_dotenv()  # Loads variables from .env locally

app = FastAPI(title="Workout Brain API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared token to protect this endpoint from public calls
SYNC_SECRET = os.getenv("SHEET_SYNC_SECRET", "MY_SUPER_SECRET_SYNC_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

WEEKLY_GOAL_KG = 12000.0
WORKOUT_ORDER = ["Chest", "Back", "Legs"]

CACHE_TTL_SECONDS = 60
_cache: dict[str, Any] = {
    "df": None,
    "timestamp": 0.0
}
_cache_lock = asyncio.Lock()

# Exercise name mapping based on your tab names
TAB_EXERCISE_MAPPING = {
    "Chest": "Bänkpress",
    "Back": "Deadlift",
    "Legs": "Squat"
}

class TabData(BaseModel):
    headers: list[str]
    rows: list[list[Any]]

class SyncPayload(BaseModel):
    user_id: str
    tabs: dict[str, TabData]

def parse_swedish_date(date_str: str) -> str | None:
    """Parses DD/MM/YYYY or D/M/YYYY to ISO format YYYY-MM-DD."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(str(val).replace(",", ".").strip())
    except (ValueError, TypeError):
        return default

def safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return default

@app.post("/api/sync-sheet")
async def sync_sheet_data(payload: SyncPayload, authorization: str | None = Header(None)) -> dict[str, Any]:
    # 1. Verify authorization secret
    expected_header = f"Bearer {SYNC_SECRET}"
    if authorization != expected_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid sync token")

    records_to_save: list[dict[str, Any]] = []

    # 2. Iterate through tabs (Chest, Back, Legs)
    for tab_name, tab_data in payload.tabs.items():
        exercise_name = TAB_EXERCISE_MAPPING.get(tab_name, tab_name)

        for row in tab_data.rows:
            if not row or not row[0]:  # Skip empty lines
                continue
            
            workout_date = parse_swedish_date(row[0])
            if not workout_date:
                continue

            # Map the 10 columns
            completed_str = str(row[1]).strip().lower() if len(row) > 1 else ""
            is_completed = completed_str in ("ja", "yes", "true", "1")
            
            weight = safe_float(row[2]) if len(row) > 2 else 0.0
            reps = safe_int(row[3]) if len(row) > 3 else 0
            sets = safe_int(row[4]) if len(row) > 4 else 0
            volume = safe_float(row[5]) if len(row) > 5 else (weight * reps * sets)
            intensity = safe_float(row[6]) if len(row) > 6 else 0.0
            difficulty = safe_int(row[7]) if len(row) > 7 else None
            one_rm = safe_float(row[8]) if len(row) > 8 else None
            ml_difficulty = safe_int(row[9]) if len(row) > 9 else None

            record: dict[str, Any] = {
                "user_id": payload.user_id,
                "category": tab_name,
                "exercise": exercise_name,
                "workout_date": workout_date,
                "completed": is_completed,
                "weight_kg": weight,
                "reps": reps,
                "sets": sets,
                "volume": volume,
                "intensity": intensity,
                "difficulty": difficulty,
                "one_rm": one_rm,
                "ml_predicted_difficulty": ml_difficulty
            }
            records_to_save.append(record)

 # 3. Deduplicera så att samma datum/övning inte skickas två gånger i samma batch
    if records_to_save:
        # Sparar senaste raden om samma datum förekommer flera gånger
        unique_records = list({
            (r["user_id"], r["workout_date"], r["exercise"]): r
            for r in records_to_save
        }.values())

        # Skicka i batcher om 200 rader
        batch_size = 200
        for i in range(0, len(unique_records), batch_size):
            chunk = unique_records[i : i + batch_size]
            supabase.table("workouts").upsert(
                chunk,
                on_conflict="user_id,workout_date,exercise"
            ).execute()

        _cache["df"] = None
        _cache["timestamp"] = 0.0

    return {
        "status": "success",
        "total_records": len(records_to_save)
    }


def get_funny_message(days_since: int | None) -> str:
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


async def fetch_workouts_df(force_refresh: bool = False, user_id: str = "Altsten93") -> pd.DataFrame:
    """Hämtar pass från Supabase-tabellen workouts med caching."""
    async with _cache_lock:
        now = datetime.now().timestamp()
        if not force_refresh and _cache["df"] is not None and (now - _cache["timestamp"]) < CACHE_TTL_SECONDS:
            return _cache["df"].copy()

        resp = supabase.table("workouts").select("*").eq("user_id", user_id).execute()
        data = resp.data or []
        df = pd.DataFrame(data)

        if not df.empty:
            df["parsed_date"] = pd.to_datetime(df["workout_date"], errors="coerce")
            if "volume" not in df.columns:
                df["volume"] = 0.0
            else:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
            if "completed" not in df.columns:
                df["completed"] = False
            else:
                df["completed"] = df["completed"].astype(bool)
        else:
            df = pd.DataFrame(columns=[
                "id", "user_id", "category", "exercise", "workout_date",
                "completed", "weight_kg", "reps", "sets", "volume",
                "intensity", "difficulty", "one_rm", "ml_predicted_difficulty", "parsed_date"
            ])

        _cache["df"] = df.copy()
        _cache["timestamp"] = now
        return df.copy()


@app.get("/api/workout/next")
async def get_next_workout(
    group_index: int | None = Query(None, ge=0, le=2),
    force_refresh: bool = Query(False)
) -> dict[str, Any]:
    """
    Hämtar nästa schemalagda pass och räknar ut dagar sedan förra passet i samma kategori.
    Om group_index utelämnas väljs den muskelgrupp som tränades längst sedan automatiskt.
    """
    df = await fetch_workouts_df(force_refresh=force_refresh, user_id="Altsten93")

    # 1. Identifiera senaste genomförda datum för respektive grupp
    completed = df[df["completed"] & df["parsed_date"].notna()]
    last_dates: dict[str, datetime | None] = {}
    for group in WORKOUT_ORDER:
        group_completed = completed[completed["category"] == group]
        if not group_completed.empty:
            last_dates[group] = group_completed["parsed_date"].max()
        else:
            last_dates[group] = None

    # 2. Välj grupp
    if group_index is None:
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
    uncompleted = df[(df["category"] == selected_group) & (~df["completed"])].sort_values("parsed_date")

    last_date = last_dates.get(selected_group)
    days_since = (datetime.now().date() - last_date.date()).days if last_date and pd.notna(last_date) else None

    if uncompleted.empty:
        return {
            "allCompleted": True,
            "groupIndex": active_group_index,
            "workoutType": selected_group,
            "nextWorkout": None,
            "message": "Alla pass i denna kategori är slutförda!"
        }

    next_row = uncompleted.iloc[0]

    exercises = [
        {
            "name": str(next_row["exercise"]) if pd.notna(next_row.get("exercise")) else TAB_EXERCISE_MAPPING.get(selected_group, selected_group),
            "kg": str(next_row["weight_kg"]) if pd.notna(next_row.get("weight_kg")) else "0",
            "reps": str(next_row["reps"]) if pd.notna(next_row.get("reps")) else "0",
            "sets": str(next_row["sets"]) if pd.notna(next_row.get("sets")) else "0",
        }
    ]

    return {
        "allCompleted": False,
        "groupIndex": active_group_index,
        "workoutType": selected_group,
        "originalRowIndex": 0,
        "daysSinceLastWorkout": days_since,
        "message": get_funny_message(days_since),
        "exercises": exercises
    }


@app.get("/api/dashboard")
async def get_dashboard(force_refresh: bool = Query(False)) -> dict[str, Any]:
    """
    Räknar ut all data för dashboarden:
    - Veckomål och nuvarande veckovolym (Pie/Doughnut)
    - 6-veckors rullande volym per muskelgrupp (Line chart)
    - Totalt antal genomförda pass (Bar chart)
    - Normaliserad intensitet vs svårighetsgrad (Adaption chart)
    """
    df = await fetch_workouts_df(force_refresh=force_refresh, user_id="Altsten93")
    completed = df[df["completed"] & df["parsed_date"].notna()].copy()

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
        vol = current_week_df[current_week_df["category"] == w_type]["volume"].sum()
        weekly_by_type[w_type] = round(float(vol), 1)

    current_week_total = sum(weekly_by_type.values())
    percentage = min(round((current_week_total / WEEKLY_GOAL_KG) * 100, 1), 100.0)
    remaining_vol = max(0.0, round(WEEKLY_GOAL_KG - current_week_total, 1))

    # --- 2. Rullande 6-veckors volym ---
    completed["year_week"] = (
        completed["iso_year"].astype(str) + "-W" +
        completed["iso_week"].astype(str).str.zfill(2)
    )

    pivot_vol = completed.pivot_table(
        index="year_week", columns="category", values="volume", aggfunc="sum"
    ).fillna(0)

    rolling_vol = pivot_vol.rolling(window=6, min_periods=1).mean().round(1)

    all_weeks = [str(w) for w in rolling_vol.index]
    if len(all_weeks) > 12:
        all_weeks = all_weeks[-12:]
    rolling_vol = rolling_vol.loc[all_weeks]

    volume_datasets = []
    colors = {"Chest": "#48BB78", "Back": "#F56565", "Legs": "#4299E1"}

    for w_type in WORKOUT_ORDER:
        data = [float(x) for x in rolling_vol[w_type].values] if w_type in rolling_vol.columns else [0.0] * len(all_weeks)
        volume_datasets.append({
            "label": f"{w_type} Volume (6-Week Avg)",
            "data": data,
            "borderColor": colors[w_type],
            "borderWidth": 2,
            "fill": False,
            "pointRadius": 2
        })

    # --- 3. Pass per kategori (Total Sessions) ---
    session_counts = completed["category"].value_counts().to_dict()
    sessions_data = {
        "labels": WORKOUT_ORDER,
        "data": [int(session_counts.get(w_type, 0)) for w_type in WORKOUT_ORDER]
    }

    # --- 4. Adaptionsgraf (Senaste 12 månaderna) ---
    adaption_window_start = now - timedelta(days=365)
    recent_df = completed[completed["parsed_date"] >= adaption_window_start].copy()

    adaption_mapping = {
        "Chest": {"color": "#FFD700", "dash": [5, 5]},
        "Back": {"color": "#9370DB", "dash": [2, 3]},
        "Legs": {"color": "#00BFFF", "dash": [10, 3]},
    }

    adaption_points = []
    for _, row in recent_df.iterrows():
        w_type = str(row["category"])
        if w_type not in adaption_mapping:
            continue

        try:
            val_int = float(row["intensity"]) if "intensity" in row and pd.notna(row["intensity"]) else np.nan
            val_diff = float(row["difficulty"]) if "difficulty" in row and pd.notna(row["difficulty"]) else np.nan

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

        min_int, max_int = float(ad_df["intensity"].min()), float(ad_df["intensity"].max())
        min_diff, max_diff = float(ad_df["difficulty"].min()), float(ad_df["difficulty"].max())

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


# Mount static files at the end so routes precede static file handling
app.mount("/", StaticFiles(directory=".", html=True), name="static")