#!/usr/bin/env python3
"""
sync_withings.py

Automatiserat synkroniseringsskript för att hämta hälsodata från Withings API
och spara/uppdatera den i Supabase-tabellen public.withings_scale_data.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from supabase import Client, create_client

# Konstanter för Withings API
WITHINGS_AUTH_URL = "https://account.withings.net/oauth2_user/authorize2"
WITHINGS_TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
WITHINGS_MEASURE_URL = "https://wbsapi.withings.net/measure"
DEFAULT_REDIRECT_URI = "https://wbsapi.withings.net/v2/oauth2"
DEFAULT_SCOPE = "user.metrics"

# Sökväg till lokal tokenfil
SCRIPT_DIR = Path(__file__).parent.resolve()

def get_tokens_file_path() -> Path:
    candidates = [
        SCRIPT_DIR / "withings_tokens.json",
        SCRIPT_DIR / "training_app" / "withings_tokens.json",
        SCRIPT_DIR.parent / "withings_tokens.json",
        Path.cwd() / "withings_tokens.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

TOKENS_FILE = get_tokens_file_path()

# Hitta .env
def get_env_file_path() -> Path:
    candidates = [
        SCRIPT_DIR / ".env",
        SCRIPT_DIR / "training_app" / ".env",
        SCRIPT_DIR.parent / ".env",
        Path.cwd() / ".env",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

ENV_FILE = get_env_file_path()

# Läs miljövariabler från .env
load_dotenv(dotenv_path=ENV_FILE)


def get_env_var(name: str, fallback_names: Optional[List[str]] = None, default: Optional[str] = None) -> Optional[str]:
    """Hämta miljövariabel med stöd för alternativa variabelnamn och standardvärde."""
    val = os.getenv(name)
    if val and val.strip():
        return val.strip()
    if fallback_names:
        for fb in fallback_names:
            val = os.getenv(fb)
            if val and val.strip():
                return val.strip()
    return default


def get_config() -> Dict[str, str]:
    """Validera och hämta nödvändig konfiguration."""
    supabase_url = get_env_var("SUPABASE_URL")
    supabase_key = get_env_var("SUPABASE_KEY", ["SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"])
    client_id = get_env_var("WITHINGS_CLIENT_ID", ["ClientID_WITHINGS"])
    client_secret = get_env_var("WITHINGS_CLIENT_SECRET", ["Secret_WITHINGS"])
    redirect_uri = get_env_var("WITHINGS_REDIRECT_URI", default=DEFAULT_REDIRECT_URI)

    missing = []
    if not supabase_url:
        missing.append("SUPABASE_URL")
    if not supabase_key:
        missing.append("SUPABASE_KEY / SUPABASE_SERVICE_ROLE_KEY")
    if not client_id:
        missing.append("WITHINGS_CLIENT_ID / ClientID_WITHINGS")
    if not client_secret:
        missing.append("WITHINGS_CLIENT_SECRET / Secret_WITHINGS")

    if missing:
        raise ValueError(f"Saknade obligatoriska miljövariabler i .env: {', '.join(missing)}")

    return {
        "supabase_url": supabase_url,
        "supabase_key": supabase_key,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri or DEFAULT_REDIRECT_URI,
    }


def load_tokens_file() -> Optional[Dict[str, Any]]:
    """Läs sparade tokens från withings_tokens.json om filen finns."""
    if TOKENS_FILE.exists():
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[VARNING] Kunde inte läsa {TOKENS_FILE.name}: {e}", file=sys.stderr)
    return None


def save_tokens_file(token_response_body: Dict[str, Any]) -> Dict[str, Any]:
    """Spara mottagna tokens till withings_tokens.json."""
    expires_in = token_response_body.get("expires_in", 10800)
    tokens_data = {
        "access_token": token_response_body.get("access_token"),
        "refresh_token": token_response_body.get("refresh_token"),
        "token_type": token_response_body.get("token_type", "Bearer"),
        "userid": token_response_body.get("userid"),
        "scope": token_response_body.get("scope"),
        "expires_in": expires_in,
        "expires_at": int(time.time()) + int(expires_in),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tokens_data, f, indent=2, ensure_ascii=False)
    print(f"[INFO] Nya tokens har sparats till {TOKENS_FILE.name}")
    return tokens_data


def generate_auth_url(client_id: str, redirect_uri: str) -> str:
    """Skapa Withings OAuth 2.0 auktoriseringslänk."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "state": "withings_sync",
        "scope": DEFAULT_SCOPE,
        "redirect_uri": redirect_uri,
    }
    return f"{WITHINGS_AUTH_URL}?{urllib.parse.urlencode(params)}"


def clean_auth_code(code_or_url: str) -> str:
    """Extrahera auth code om användaren råkar klistra in hela callback-URL:en."""
    code_or_url = code_or_url.strip().strip("'\"")
    if "code=" in code_or_url:
        parsed = urllib.parse.urlparse(code_or_url)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params and params["code"]:
            return params["code"][0].strip()
        # Fallback om strängen saknar protokoll/scheme
        parts = code_or_url.split("code=")
        if len(parts) > 1:
            code_part = parts[1].split("&")[0].split(" ")[0].strip()
            return code_part
    return code_or_url


def exchange_authorization_code(config: Dict[str, str], code: str) -> Dict[str, Any]:
    """Byt en authorization code mot initiala access- och refresh-tokens."""
    clean_code = clean_auth_code(code)
    print(f"[INFO] Byter authorization code mot tokens...")
    payload = {
        "action": "requesttoken",
        "grant_type": "authorization_code",
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": clean_code,
        "redirect_uri": config["redirect_uri"],
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(WITHINGS_TOKEN_URL, data=payload, headers=headers, timeout=30)
    
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP-fel vid hämtning av tokens ({resp.status_code}): {resp.text}")

    data = resp.json()
    if data.get("status") != 0:
        error_msg = data.get("error") or data.get("message") or f"Felstatus {data.get('status')}"
        if "invalid code" in str(error_msg).lower():
            hint = (
                "\n[TIPS] Withings authorization code är endast giltig i 30 sekunder från att den genererades "
                "och kan endast användas en gång. Om det tog mer än 30 sekunder från inloggningen har koden löpt ut.\n"
                "Kör 'python sync_withings.py', klicka på länken och klistra in URL/kod direkt i prompten!"
            )
            raise RuntimeError(f"Withings API-fel vid tokenutbyte: {error_msg}. {hint}")
        raise RuntimeError(f"Withings API-fel vid tokenutbyte: {error_msg}")

    body = data.get("body", {})
    return save_tokens_file(body)


def refresh_tokens(config: Dict[str, str], refresh_token: str) -> Dict[str, Any]:
    """Förnya access token med refresh token."""
    print("[INFO] Förnyar access token via Withings OAuth...")
    payload = {
        "action": "requesttoken",
        "grant_type": "refresh_token",
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "refresh_token": refresh_token,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(WITHINGS_TOKEN_URL, data=payload, headers=headers, timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(f"HTTP-fel vid refresh av tokens ({resp.status_code}): {resp.text}")

    data = resp.json()
    if data.get("status") != 0:
        error_msg = data.get("error") or data.get("message") or f"Felstatus {data.get('status')}"
        raise RuntimeError(f"Withings API-fel vid refresh: {error_msg}")

    body = data.get("body", {})
    return save_tokens_file(body)


def get_access_token(config: Dict[str, str], cli_code: Optional[str] = None) -> str:
    """
    Säkerställ att vi har en giltig access token.
    1. Om sparad refresh_token finns (fil eller .env): förnya access token.
    2. Om auth_code angavs (CLI eller .env): byt mot tokens.
    3. Annars: instruera användaren med auktoriseringslänk och avbryt/fråga.
    """
    saved_tokens = load_tokens_file()
    env_refresh_token = get_env_var("WITHINGS_REFRESH_TOKEN")
    refresh_token = None

    if saved_tokens and saved_tokens.get("refresh_token"):
        refresh_token = saved_tokens["refresh_token"]
    elif env_refresh_token:
        refresh_token = env_refresh_token

    # Om refresh-token finns: förnya enligt specifikation A
    if refresh_token:
        try:
            tokens = refresh_tokens(config, refresh_token)
            return tokens["access_token"]
        except Exception as e:
            print(f"[VARNING] Misslyckades med att förnya token via refresh_token: {e}", file=sys.stderr)
            print("[INFO] Försöker med eventuell auth code istället...")

    # Om vi har en auth code
    code = cli_code or get_env_var("WITHINGS_AUTH_CODE")
    if code:
        tokens = exchange_authorization_code(config, code)
        return tokens["access_token"]

    # Inga tokens och ingen auth code hittades -> Instruera användaren
    auth_url = generate_auth_url(config["client_id"], config["redirect_uri"])
    print("\n" + "=" * 80)
    print("INGEN GILTIG TOKEN ELLER AUTH-KOD HITTADES")
    print("=" * 80)
    print("Gör så här för att auktorisera Withings (tar ca 10 sekunder):")
    print("1. Öppna följande länk i din webbläsare:")
    print(f"\n   {auth_url}\n")
    print("2. Logga in och klicka på 'Allow' / 'Godkänn behörighet'.")
    print("3. Webbläsaren omdirigeras till en sida som visar:")
    print('   {"status":2554,"error":"Not implemented"}')
    print("   (Detta felmeddelande på skärmen är helt normalt för callback-URL:en!)")
    print("4. Kopiera hela URL:en från webbläsarens adressfält.")
    print("   (Den innehåller ?code=...&state=...)")
    print("5. OBS! Koden är giltig i 30 sekunder från att du godkände.")
    print("   Klistra in den direkt här nedanför eller kör:")
    print('   python sync_withings.py --code "DIN_URL_ELLER_KOD"')
    print("=" * 80 + "\n")

    # Om vi körs interaktivt i terminalen kan vi fråga direkt
    if sys.stdin and sys.stdin.isatty():
        try:
            prompt_code = input("Klistra in koden här nu (eller tryck Enter för att avbryta): ").strip()
            if prompt_code:
                tokens = exchange_authorization_code(config, prompt_code)
                return tokens["access_token"]
        except (EOFError, KeyboardInterrupt):
            pass

    sys.exit(1)


def fetch_withings_measurements(access_token: str, startdate: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Hämta mätdata från Withings Measure API.
    Endpoint: POST https://wbsapi.withings.net/measure
    Hanterar paginering via 'more' och 'offset'.
    """
    all_measuregrps = []
    offset = None
    page = 1

    print("[INFO] Hämtar mätdata från Withings Measure API...")

    while True:
        payload = {
            "action": "getmeas",
            "category": 1,  # Endast riktiga mätningar
            "meastypes": "1,5,6,8,11,76,77,88",
        }
        if startdate:
            payload["startdate"] = startdate
        if offset is not None:
            payload["offset"] = offset

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        resp = requests.post(WITHINGS_MEASURE_URL, data=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP-fel från Withings Measure API ({resp.status_code}): {resp.text}")

        data = resp.json()
        status = data.get("status")
        if status != 0:
            error_msg = data.get("error") or data.get("message") or f"Statuskod {status}"
            raise RuntimeError(f"Withings API-fel vid getmeas (status {status}): {error_msg}")

        body = data.get("body", {})
        measuregrps = body.get("measuregrps", [])
        all_measuregrps.extend(measuregrps)
        print(f"[INFO] Hämtade sida {page}: {len(measuregrps)} mätgrupper.")

        # Kontrollera om det finns fler mätningar att hämta
        more = body.get("more")
        next_offset = body.get("offset")
        if more and next_offset:
            offset = next_offset
            page += 1
        else:
            break

    print(f"[INFO] Totalt mottagna mätgrupper från Withings: {len(all_measuregrps)}")
    return all_measuregrps


def convert_measure_groups_to_records(measuregrps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Konvertera Withings mätgrupper till rader för Supabase-tabellen withings_scale_data.
    Formel: real_value = raw_value * 10^(unit)
    
    Mappning:
    - 1  -> weight_kg (avrundat till 2 decimaler)
    - 6  -> fat_ratio_pct (avrundat till 2 decimaler)
    - 8  -> fat_mass_kg (avrundat till 2 decimaler)
    - 76 -> muscle_mass_kg (avrundat till 2 decimaler)
    - 77 -> hydration_kg (avrundat till 2 decimaler)
    - 88 -> bone_mass_kg (avrundat till 2 decimaler)
    - 11 -> heart_pulse_bpm (heltal)
    - date -> measured_at (ISO 8601 UTC)
    """
    records_by_time: Dict[str, Dict[str, Any]] = {}

    for grp in measuregrps:
        # Endast category 1 (riktiga mätningar)
        if grp.get("category") != 1:
            continue

        raw_date = grp.get("date")
        if not raw_date:
            continue

        dt = datetime.fromtimestamp(raw_date, tz=timezone.utc)
        measured_at_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if measured_at_str not in records_by_time:
            records_by_time[measured_at_str] = {
                "measured_at": measured_at_str,
                "weight_kg": None,
                "fat_ratio_pct": None,
                "fat_mass_kg": None,
                "muscle_mass_kg": None,
                "bone_mass_kg": None,
                "hydration_kg": None,
                "heart_pulse_bpm": None,
            }

        rec = records_by_time[measured_at_str]

        for m in grp.get("measures", []):
            m_type = m.get("type")
            raw_val = m.get("value")
            unit = m.get("unit", 0)

            if raw_val is None:
                continue

            real_val = raw_val * (10 ** unit)

            if m_type == 1:
                rec["weight_kg"] = round(real_val, 2)
            elif m_type == 6:
                rec["fat_ratio_pct"] = round(real_val, 2)
            elif m_type == 8:
                rec["fat_mass_kg"] = round(real_val, 2)
            elif m_type == 76:
                rec["muscle_mass_kg"] = round(real_val, 2)
            elif m_type == 77:
                rec["hydration_kg"] = round(real_val, 2)
            elif m_type == 88:
                rec["bone_mass_kg"] = round(real_val, 2)
            elif m_type == 11:
                rec["heart_pulse_bpm"] = int(round(real_val))

    # Filtrera bort poster där alla mått är None
    clean_records = [
        rec for rec in records_by_time.values()
        if any(rec[k] is not None for k in [
            "weight_kg", "fat_ratio_pct", "fat_mass_kg",
            "muscle_mass_kg", "bone_mass_kg", "hydration_kg", "heart_pulse_bpm"
        ])
    ]

    # Sortera efter datum i kronologisk ordning
    clean_records.sort(key=lambda x: x["measured_at"])
    return clean_records


def upsert_to_supabase(supabase: Client, records: List[Dict[str, Any]], batch_size: int = 100) -> int:
    """
    Spara mätdata i Supabase via upsert (on_conflict='measured_at').
    Delar upp i batcher för att hantera stora datamängder stabilt.
    """
    if not records:
        print("[INFO] Inga mätposter att synkronisera.")
        return 0

    total_records = len(records)
    total_synced = 0

    print(f"[INFO] Synkroniserar {total_records} rader till Supabase-tabellen 'withings_scale_data'...")

    for i in range(0, total_records, batch_size):
        batch = records[i:i + batch_size]
        try:
            response = supabase.table("withings_scale_data").upsert(
                batch,
                on_conflict="measured_at"
            ).execute()

            # Antal rader som returnerades från upsert
            synced_count = len(response.data) if response.data else len(batch)
            total_synced += synced_count
            print(f"[INFO] Synkroniserade batch {i // batch_size + 1} ({len(batch)} rader).")
        except Exception as e:
            print(f"[FEL] Fel vid upsert till Supabase för batch vid index {i}: {e}", file=sys.stderr)
            raise

    return total_synced


def sync_withings_data() -> dict:
    """
    Anropsbar funktion för backend och schemalagda jobb:
    Förnyar token automatiskt och sparar nya mätningar till Supabase.
    Returnerar en dict med status och antal synkade rader.
    """
    try:
        config = get_config()
    except Exception as e:
        return {"status": "error", "message": f"Konfigurationsfel: {e}", "synced_rows": 0}

    saved_tokens = load_tokens_file()
    env_refresh_token = get_env_var("WITHINGS_REFRESH_TOKEN")
    refresh_token = saved_tokens.get("refresh_token") if saved_tokens else env_refresh_token
    access_token = saved_tokens.get("access_token") if saved_tokens else None
    expires_at = saved_tokens.get("expires_at", 0) if saved_tokens else 0

    if not refresh_token and not access_token:
        return {"status": "skipped", "message": "Inga Withings-tokens konfigurerade.", "synced_rows": 0}

    now_ts = int(time.time())
    # Om access_token saknas eller löper ut inom 5 minuter, förnya med refresh_token
    if not access_token or (expires_at - now_ts < 300):
        if refresh_token:
            try:
                new_tokens = refresh_tokens(config, refresh_token)
                access_token = new_tokens["access_token"]
            except Exception as e:
                return {"status": "error", "message": f"Kunde inte förnya access token: {e}", "synced_rows": 0}
        else:
            return {"status": "error", "message": "Access token utgången och ingen refresh_token sparad.", "synced_rows": 0}

    # Hämta mätningar från Withings API
    try:
        measuregrps = fetch_withings_measurements(access_token)
    except Exception as e:
        # Om det berodde på 401 eller utgången token, prova att förnya en gång och försök igen
        if refresh_token:
            try:
                print("[INFO] Försöker förnya access token efter fel vid hämtning...")
                new_tokens = refresh_tokens(config, refresh_token)
                access_token = new_tokens["access_token"]
                measuregrps = fetch_withings_measurements(access_token)
            except Exception as retry_err:
                return {"status": "error", "message": f"Kunde inte hämta data från Withings efter token-förnyelse: {retry_err}", "synced_rows": 0}
        else:
            return {"status": "error", "message": f"Kunde inte hämta data från Withings: {e}", "synced_rows": 0}

    records = convert_measure_groups_to_records(measuregrps)
    if not records:
        return {"status": "success", "message": "Inga mätningar att spara.", "synced_rows": 0}

    try:
        supabase_client = create_client(config["supabase_url"], config["supabase_key"])
        synced_count = upsert_to_supabase(supabase_client, records)
        return {
            "status": "success",
            "synced_rows": synced_count,
            "total_measurements": len(measuregrps),
            "unique_timestamps": len(records),
        }
    except Exception as e:
        return {"status": "error", "message": f"Fel vid upsert till Supabase: {e}", "synced_rows": 0}


def main():
    parser = argparse.ArgumentParser(description="Synkronisera Withings mätdata till Supabase.")
    parser.add_argument("--code", help="Withings OAuth 2.0 authorization code för initial tokenhämtning.")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch-storlek vid upsert till Supabase (standard: 100).")
    args = parser.parse_args()

    print("=" * 60)
    print("STARTAR SYNKRONISERING: WITHINGS -> SUPABASE")
    print("=" * 60)

    try:
        config = get_config()
    except Exception as e:
        print(f"[KONFIGURATIONSFEL] {e}", file=sys.stderr)
        sys.exit(1)

    # 1. Hämta giltig access token (eller förnya / byt code)
    access_token = get_access_token(config, cli_code=args.code)

    # 2. Hämta mätningar från Withings API
    try:
        measuregrps = fetch_withings_measurements(access_token)
    except Exception as e:
        print(f"[FEL] Kunde inte hämta data från Withings: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Konvertera och formatera mätvärden
    records = convert_measure_groups_to_records(measuregrps)
    print(f"[INFO] Formaterade {len(records)} unika mättillfällen.")

    # 4. Upsert till Supabase
    try:
        supabase = create_client(config["supabase_url"], config["supabase_key"])
        total_synced = upsert_to_supabase(supabase, records, batch_size=args.batch_size)
    except Exception as e:
        print(f"[FEL] Kunde inte synkronisera till Supabase: {e}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print(f"KLART: Synkronisering slutförd! Totalt synkade rader: {total_synced}")
    print("=" * 60)


if __name__ == "__main__":
    main()
