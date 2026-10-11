import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from sync_withings import (
    convert_measure_groups_to_records,
    clean_auth_code,
    generate_auth_url,
    fetch_withings_measurements,
    upsert_to_supabase,
    save_tokens_file,
    load_tokens_file,
    TOKENS_FILE
)


def test_clean_auth_code():
    # Direct code
    assert clean_auth_code("abc123xyz") == "abc123xyz"
    # Strips whitespace
    assert clean_auth_code("  abc123xyz  ") == "abc123xyz"
    # Full URL
    url = "https://wbsapi.withings.net/v2/oauth2?code=my_secret_code_123&state=withings_sync"
    assert clean_auth_code(url) == "my_secret_code_123"


def test_generate_auth_url():
    client_id = "test_client_id"
    redirect_uri = "https://wbsapi.withings.net/v2/oauth2"
    url = generate_auth_url(client_id, redirect_uri)
    assert "client_id=test_client_id" in url
    assert "response_type=code" in url
    assert "user.metrics" in url


def test_convert_measure_groups_to_records():
    # Sample Withings measure groups
    sample_groups = [
        {
            "grpid": 101,
            "category": 1,
            "date": 1600000000,  # 2020-09-13T12:26:40Z
            "measures": [
                {"value": 78456, "type": 1, "unit": -3},   # weight_kg = 78.46
                {"value": 1825, "type": 6, "unit": -2},    # fat_ratio_pct = 18.25
                {"value": 1432, "type": 8, "unit": -2},    # fat_mass_kg = 14.32
                {"value": 6125, "type": 76, "unit": -2},   # muscle_mass_kg = 61.25
                {"value": 4512, "type": 77, "unit": -2},   # hydration_kg = 45.12
                {"value": 289, "type": 88, "unit": -2},    # bone_mass_kg = 2.89
                {"value": 68, "type": 11, "unit": 0},      # heart_pulse_bpm = 68
                {"value": 6413, "type": 5, "unit": -2},    # fat free mass (should be ignored)
            ],
        },
        # Category 2 (user objective) should be ignored
        {
            "grpid": 102,
            "category": 2,
            "date": 1600000000,
            "measures": [
                {"value": 75000, "type": 1, "unit": -3}
            ],
        },
    ]

    records = convert_measure_groups_to_records(sample_groups)
    assert len(records) == 1
    rec = records[0]

    assert rec["measured_at"] == "2020-09-13T12:26:40Z"
    assert rec["weight_kg"] == 78.46
    assert rec["fat_ratio_pct"] == 18.25
    assert rec["fat_mass_kg"] == 14.32
    assert rec["muscle_mass_kg"] == 61.25
    assert rec["hydration_kg"] == 45.12
    assert rec["bone_mass_kg"] == 2.89
    assert rec["heart_pulse_bpm"] == 68
    assert "fat_free_mass_kg" not in rec


def test_convert_measure_groups_merging_same_timestamp():
    # Two groups with the same timestamp should be merged to avoid DB conflict
    sample_groups = [
        {
            "grpid": 201,
            "category": 1,
            "date": 1600000000,
            "measures": [
                {"value": 80000, "type": 1, "unit": -3},  # weight_kg = 80.0
            ],
        },
        {
            "grpid": 202,
            "category": 1,
            "date": 1600000000,
            "measures": [
                {"value": 72, "type": 11, "unit": 0},      # heart_pulse_bpm = 72
            ],
        },
    ]

    records = convert_measure_groups_to_records(sample_groups)
    assert len(records) == 1
    assert records[0]["weight_kg"] == 80.0
    assert records[0]["heart_pulse_bpm"] == 72


@patch("sync_withings.requests.post")
def test_fetch_withings_measurements_pagination(mock_post):
    # Mocking two pages
    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.json.return_value = {
        "status": 0,
        "body": {
            "more": 1,
            "offset": 9999,
            "measuregrps": [{"grpid": 1, "category": 1, "date": 1000, "measures": []}]
        }
    }

    page2_resp = MagicMock()
    page2_resp.status_code = 200
    page2_resp.json.return_value = {
        "status": 0,
        "body": {
            "more": 0,
            "offset": 0,
            "measuregrps": [{"grpid": 2, "category": 1, "date": 2000, "measures": []}]
        }
    }

    mock_post.side_effect = [page1_resp, page2_resp]

    groups = fetch_withings_measurements("fake_token")
    assert len(groups) == 2
    assert mock_post.call_count == 2


@patch("sync_withings.requests.post")
def test_fetch_withings_measurements_error_status(mock_post):
    error_resp = MagicMock()
    error_resp.status_code = 200
    error_resp.json.return_value = {
        "status": 401,
        "error": "Invalid access token"
    }
    mock_post.return_value = error_resp

    with pytest.raises(RuntimeError) as exc_info:
        fetch_withings_measurements("fake_token")
    assert "status 401" in str(exc_info.value)


def test_upsert_to_supabase():
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_upsert = MagicMock()
    mock_execute = MagicMock()

    mock_supabase.table.return_value = mock_table
    mock_table.upsert.return_value = mock_upsert
    mock_upsert.execute.return_value = MagicMock(data=[{"id": 1}, {"id": 2}])

    records = [
        {"measured_at": "2020-01-01T00:00:00Z", "weight_kg": 75.0},
        {"measured_at": "2020-01-02T00:00:00Z", "weight_kg": 75.5},
    ]

    synced = upsert_to_supabase(mock_supabase, records, batch_size=100)
    assert synced == 2
    mock_table.upsert.assert_called_once_with(records, on_conflict="measured_at")


@patch("sync_withings.requests.post")
def test_exchange_authorization_code_success(mock_post, tmp_path):
    tokens_file = tmp_path / "withings_tokens.json"
    resp_mock = MagicMock()
    resp_mock.status_code = 200
    resp_mock.json.return_value = {
        "status": 0,
        "body": {
            "access_token": "mock_access",
            "refresh_token": "mock_refresh",
            "expires_in": 3600,
            "userid": "12345",
            "scope": "user.metrics",
        }
    }
    mock_post.return_value = resp_mock

    config = {
        "client_id": "cid",
        "client_secret": "csec",
        "redirect_uri": "https://callback",
    }

    with patch("sync_withings.TOKENS_FILE", tokens_file):
        tokens = save_tokens_file(resp_mock.json.return_value["body"])
        assert tokens["access_token"] == "mock_access"
        assert tokens["refresh_token"] == "mock_refresh"
        assert tokens_file.exists()


@patch("sync_withings.requests.post")
def test_refresh_tokens_failure(mock_post):
    from sync_withings import refresh_tokens
    resp_mock = MagicMock()
    resp_mock.status_code = 200
    resp_mock.json.return_value = {
        "status": 2555,
        "error": "invalid refresh token"
    }
    mock_post.return_value = resp_mock

    config = {
        "client_id": "cid",
        "client_secret": "csec",
    }

    with pytest.raises(RuntimeError) as exc_info:
        refresh_tokens(config, "expired_token")
    assert "Withings API-fel vid refresh: invalid refresh token" in str(exc_info.value)

