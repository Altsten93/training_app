import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import Client, create_client

try:
    from backend.ml.engine import WorkoutDifficultyPredictor
except ImportError:
    from ml.engine import WorkoutDifficultyPredictor

predictor = WorkoutDifficultyPredictor()

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
    "data": None,
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
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc).date().isoformat()
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

        for idx, row in enumerate(tab_data.rows, start=2):
            if not row:
                continue

            # Hoppa enbart över helt tomma rader
            has_content = any(
                str(cell).strip() != "" and str(cell).strip().lower() != "nan"
                for cell in row
                if cell is not None
            )
            if not has_content:
                continue

            # Läs av kolumn A (Datum): Om row[0] finns och har ett datum, parsa det. Om tomt sätts workout_date = None.
            raw_date = str(row[0]).strip() if len(row) > 0 and row[0] is not None else ""
            workout_date = parse_swedish_date(raw_date) if raw_date and raw_date.lower() != "nan" else None

            # Läs av kolumn B (Completed_workout): True om ja/yes/true/1, annars False.
            completed_str = str(row[1]).strip().lower() if len(row) > 1 and row[1] is not None else ""
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
                "row_index": idx,
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

    # 3. Deduplicera så att samma rad inte skickas två gånger i samma batch
    if records_to_save:
        unique_records = list({
            (r["user_id"], r["category"], r["row_index"]): r
            for r in records_to_save
        }.values())

        # Skicka i batcher om 200 rader
        batch_size = 200
        for i in range(0, len(unique_records), batch_size):
            chunk = unique_records[i : i + batch_size]
            supabase.table("workouts").upsert(
                chunk,
                on_conflict="user_id,category,row_index"
            ).execute()

        _cache["data"] = None
        _cache["timestamp"] = 0.0

    # 4. Hämta uppdaterade rader från Supabase för tvåvägssynk
    updates: dict[str, list[dict[str, Any]]] = {
        "Chest": [],
        "Back": [],
        "Legs": [],
    }

    all_workouts_resp = (
        supabase.table("workouts")
        .select("*")
        .eq("user_id", payload.user_id)
        .execute()
    )
    all_rows: list[dict[str, Any]] = cast(
        list[dict[str, Any]], all_workouts_resp.data or []
    )

    for r in all_rows:
        is_done = bool(r.get("completed"))
        has_ml = r.get("ml_predicted_difficulty") is not None
        if not (is_done or has_ml):
            continue

        cat = str(r.get("category", ""))
        if cat not in updates:
            updates[cat] = []

        date_str = None
        raw_wdate = r.get("workout_date")
        if raw_wdate:
            try:
                date_str = (
                    datetime.strptime(str(raw_wdate).strip(), "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc)
                    .strftime("%d/%m/%Y")
                )
            except ValueError:
                date_str = str(raw_wdate)

        update_item: dict[str, Any] = {
            "row_index": r.get("row_index"),
            "date": date_str,
            "completed": "Ja" if is_done else "Nej",
            "difficulty": r.get("difficulty"),
            "ml_difficulty": r.get("ml_predicted_difficulty"),
        }
        updates[cat].append(update_item)

    for items in updates.values():
        items.sort(key=lambda x: int(x.get("row_index") or 0))

    return {
        "status": "success",
        "total_records": len(records_to_save),
        "updates": updates,
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


async def fetch_workouts(force_refresh: bool = False, user_id: str = "Altsten93") -> list[dict[str, Any]]:
    """Hämtar pass från Supabase-tabellen workouts med caching."""
    async with _cache_lock:
        now = datetime.now(timezone.utc).timestamp()
        cached_data = _cache.get("data")
        if (
            not force_refresh
            and isinstance(cached_data, list)
            and (now - float(_cache.get("timestamp", 0.0))) < CACHE_TTL_SECONDS
        ):
            return cast(list[dict[str, Any]], cached_data)

        resp = supabase.table("workouts").select("*").eq("user_id", user_id).execute()
        rows: list[dict[str, Any]] = cast(list[dict[str, Any]], resp.data or [])

        _cache["data"] = rows
        _cache["timestamp"] = now
        return rows


@app.get("/api/workout/next")
async def get_next_workout(
    group_index: int | None = Query(None, ge=0, le=2),
    force_refresh: bool = Query(False)
) -> dict[str, Any]:
    """
    Hämtar nästa schemalagda pass och räknar ut dagar sedan förra passet i samma kategori.
    Om group_index utelämnas väljs den muskelgrupp som tränades längst sedan automatiskt.
    """
    workouts = await fetch_workouts(force_refresh=force_refresh, user_id="Altsten93")

    # 1. Identifiera senaste genomförda datum för respektive grupp
    completed = [w for w in workouts if w.get("completed") and w.get("workout_date")]
    last_dates: dict[str, datetime | None] = {}
    for group in WORKOUT_ORDER:
        group_dates: list[datetime] = []
        for w in completed:
            if w.get("category") == group:
                try:
                    group_dates.append(datetime.strptime(str(w["workout_date"]).strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc))
                except ValueError:
                    pass
        last_dates[group] = max(group_dates) if group_dates else None

    # 2. Välj grupp
    if group_index is None:
        sorted_groups = sorted(
            WORKOUT_ORDER,
            key=lambda g: (last_dates[g] is not None, last_dates[g] or datetime.min.replace(tzinfo=timezone.utc))
        )
        selected_group = sorted_groups[0]
        active_group_index = WORKOUT_ORDER.index(selected_group)
    else:
        active_group_index = group_index
        selected_group = WORKOUT_ORDER[active_group_index]

    # 3. Hämta första oavslutade passet i den valda gruppen sorterat på row_index ASC
    uncompleted = [
        w for w in workouts
        if w.get("category") == selected_group and not w.get("completed", False)
    ]
    uncompleted.sort(key=lambda w: int(w.get("row_index") or 0))

    last_date = last_dates.get(selected_group)
    days_since = (datetime.now(timezone.utc).date() - last_date.date()).days if last_date else None

    if not uncompleted:
        return {
            "allCompleted": True,
            "groupIndex": active_group_index,
            "workoutType": selected_group,
            "nextWorkout": None,
            "message": "Alla pass i denna kategori är slutförda!"
        }

    next_row = uncompleted[0]

    exercises = [
        {
            "name": str(next_row.get("exercise") or TAB_EXERCISE_MAPPING.get(selected_group, selected_group)),
            "kg": str(next_row.get("weight_kg", 0.0)),
            "reps": str(next_row.get("reps", 0)),
            "sets": str(next_row.get("sets", 0)),
        }
    ]

    return {
        "allCompleted": False,
        "groupIndex": active_group_index,
        "workoutType": selected_group,
        "originalRowIndex": int(next_row.get("row_index") or 0),
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
    workouts = await fetch_workouts(force_refresh=force_refresh, user_id="Altsten93")

    completed_workouts: list[tuple[dict[str, Any], datetime]] = []
    for w in workouts:
        if w.get("completed") and w.get("workout_date"):
            try:
                d = datetime.strptime(str(w["workout_date"]).strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                completed_workouts.append((w, d))
            except ValueError:
                pass

    if not completed_workouts:
        return {"empty": True}

    now = datetime.now(timezone.utc)
    current_year, current_week, _ = now.isocalendar()

    # --- 1. Nuvarande veckovolym ---
    weekly_by_type: dict[str, float] = {"Chest": 0.0, "Back": 0.0, "Legs": 0.0}
    for w, d in completed_workouts:
        y, wk, _ = d.isocalendar()
        if y == current_year and wk == current_week:
            cat = str(w.get("category", ""))
            if cat in weekly_by_type:
                try:
                    vol = float(w.get("volume") or 0.0)
                except (ValueError, TypeError):
                    vol = 0.0
                weekly_by_type[cat] += vol

    weekly_by_type = {k: round(v, 1) for k, v in weekly_by_type.items()}

    current_week_total = sum(weekly_by_type.values())
    percentage = min(round((current_week_total / WEEKLY_GOAL_KG) * 100, 1), 100.0)
    remaining_vol = max(0.0, round(WEEKLY_GOAL_KG - current_week_total, 1))

    # --- 2. Rullande 6-veckors volym ---
    all_year_weeks_set: set[str] = set()
    week_cat_vol: dict[tuple[str, str], float] = {}
    for w, d in completed_workouts:
        y, wk, _ = d.isocalendar()
        yw = f"{y}-W{wk:02d}"
        all_year_weeks_set.add(yw)
        cat = str(w.get("category", ""))
        try:
            vol = float(w.get("volume") or 0.0)
        except (ValueError, TypeError):
            vol = 0.0
        week_cat_vol[(yw, cat)] = week_cat_vol.get((yw, cat), 0.0) + vol

    sorted_weeks = sorted(all_year_weeks_set)
    rolling_by_cat: dict[str, list[float]] = {cat: [] for cat in WORKOUT_ORDER}
    for i in range(len(sorted_weeks)):
        window_weeks = sorted_weeks[max(0, i - 5) : i + 1]
        for cat in WORKOUT_ORDER:
            vols = [week_cat_vol.get((w_k, cat), 0.0) for w_k in window_weeks]
            avg = round(sum(vols) / len(vols), 1)
            rolling_by_cat[cat].append(avg)

    if len(sorted_weeks) > 12:
        all_weeks = sorted_weeks[-12:]
        for cat in WORKOUT_ORDER:
            rolling_by_cat[cat] = rolling_by_cat[cat][-12:]
    else:
        all_weeks = sorted_weeks

    colors = {"Chest": "#48BB78", "Back": "#F56565", "Legs": "#4299E1"}
    volume_datasets: list[dict[str, Any]] = []

    for w_type in WORKOUT_ORDER:
        volume_datasets.append({
            "label": f"{w_type} Volume (6-Week Avg)",
            "data": rolling_by_cat[w_type],
            "borderColor": colors[w_type],
            "borderWidth": 2,
            "fill": False,
            "pointRadius": 2
        })

    # --- 3. Pass per kategori (Total Sessions) ---
    session_counts: dict[str, int] = {
        cat: sum(1 for w, _ in completed_workouts if w.get("category") == cat)
        for cat in WORKOUT_ORDER
    }
    sessions_data: dict[str, Any] = {
        "labels": WORKOUT_ORDER,
        "data": [session_counts[cat] for cat in WORKOUT_ORDER]
    }

    # --- 4. Adaptionsgraf (Senaste 12 månaderna) ---
    adaption_window_start = now - timedelta(days=365)
    recent_workouts = [(w, d) for w, d in completed_workouts if d >= adaption_window_start]

    adaption_mapping: dict[str, dict[str, Any]] = {
        "Chest": {"color": "#FFD700", "dash": [5, 5]},
        "Back": {"color": "#9370DB", "dash": [2, 3]},
        "Legs": {"color": "#00BFFF", "dash": [10, 3]},
    }

    pts: list[dict[str, Any]] = []
    for w, d in recent_workouts:
        cat = str(w.get("category", ""))
        if cat not in adaption_mapping:
            continue

        try:
            val_int = float(w["intensity"]) if w.get("intensity") is not None else None
            val_diff = float(w["difficulty"]) if w.get("difficulty") is not None else None
            if val_int is not None and val_diff is not None:
                pts.append({
                    "date": d,
                    "workoutType": cat,
                    "intensity": val_int,
                    "difficulty": val_diff
                })
        except (ValueError, TypeError):
            continue

    adaption_datasets: list[dict[str, Any]] = []
    if pts:
        min_int = min(p["intensity"] for p in pts)
        max_int = max(p["intensity"] for p in pts)
        min_diff = min(p["difficulty"] for p in pts)
        max_diff = max(p["difficulty"] for p in pts)

        for p in pts:
            norm_int = 0.5 if min_int == max_int else (p["intensity"] - min_int) / (max_int - min_int)
            norm_diff = 0.5 if min_diff == max_diff else (p["difficulty"] - min_diff) / (max_diff - min_diff)
            p["adaption"] = round(norm_int - norm_diff, 3)

        for w_type in WORKOUT_ORDER:
            cat_pts = sorted([p for p in pts if p["workoutType"] == w_type], key=lambda x: x["date"])
            if cat_pts:
                chart_data: list[dict[str, Any]] = [{"x": p["date"].strftime("%Y-%m-%d"), "y": float(p["adaption"])} for p in cat_pts]
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


@app.post("/api/model/retrain")
async def retrain_model() -> dict[str, Any]:
    """Tränar om svårighetsgradsmodellen på historiska data från Supabase

    och uppdaterar framtida oavslutade pass med nya prediktioner.
    """
    workouts = await fetch_workouts(force_refresh=True, user_id="Altsten93")
    if not workouts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inga träningspass hittades för användaren.",
        )

    df = pd.DataFrame(workouts)

    try:
        metrics = predictor.train(df)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Fel vid träning av modellen: {e}",
        ) from e

    uncompleted = [w for w in workouts if not w.get("completed", False)]
    updated_count = 0

    if uncompleted:
        uncompleted_df = pd.DataFrame(uncompleted)
        preds = predictor.predict(uncompleted_df)

        records_to_update: list[dict[str, Any]] = []
        for idx, row in enumerate(uncompleted):
            pred_val = float(preds[idx]) if idx < len(preds) else 6.0
            int_pred = round(max(1.0, min(10.0, pred_val)))

            raw_diff = row.get("difficulty")
            diff_int = (
                round(float(raw_diff))
                if raw_diff is not None
                and str(raw_diff).strip() != ""
                and str(raw_diff).lower() != "nan"
                else None
            )

            raw_one_rm = row.get("one_rm")
            one_rm_float = (
                float(raw_one_rm)
                if raw_one_rm is not None
                and str(raw_one_rm).strip() != ""
                and str(raw_one_rm).lower() != "nan"
                else None
            )

            record: dict[str, Any] = {
                "user_id": str(row["user_id"]),
                "category": str(row["category"]),
                "row_index": int(row["row_index"]),
                "exercise": str(row["exercise"]),
                "workout_date": str(row["workout_date"]) if row.get("workout_date") else None,
                "completed": False,
                "weight_kg": float(row.get("weight_kg") or 0.0),
                "reps": int(row.get("reps") or 0),
                "sets": int(row.get("sets") or 0),
                "volume": float(row.get("volume") or 0.0),
                "intensity": float(row.get("intensity") or 0.0),
                "difficulty": diff_int,
                "one_rm": one_rm_float,
                "ml_predicted_difficulty": int_pred,
            }
            records_to_update.append(record)

        batch_size = 200
        for i in range(0, len(records_to_update), batch_size):
            chunk = records_to_update[i : i + batch_size]
            supabase.table("workouts").upsert(
                chunk,
                on_conflict="user_id,category,row_index",
            ).execute()

        updated_count = len(records_to_update)

        _cache["data"] = None
        _cache["timestamp"] = 0.0

    return {
        "status": "success",
        "trained_samples": metrics["trained_samples"],
        "mae": metrics["mae"],
        "updated_future_workouts": updated_count,
        "serverMessage": f"MAE: {metrics['mae']} på {metrics['trained_samples']} pass. Uppdaterade {updated_count} framtida pass.",
    }


# Mount static files at the end so routes precede static file handling
app.mount("/", StaticFiles(directory=".", html=True), name="static")