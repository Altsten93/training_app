import os
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import Client, create_client

load_dotenv()  # Loads variables from .env locally

app = FastAPI()

# Shared token to protect this endpoint from public calls
SYNC_SECRET = os.getenv("SHEET_SYNC_SECRET", "MY_SUPER_SECRET_SYNC_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
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

    return {
        "status": "success",
        "total_records": len(records_to_save)
    }

# Mount static files at the end so routes precede static file handling
app.mount("/", StaticFiles(directory=".", html=True), name="static")