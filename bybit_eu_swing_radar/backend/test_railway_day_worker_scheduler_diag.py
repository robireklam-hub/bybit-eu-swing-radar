from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.railway_day_worker_scheduler_diag import summarize_scheduler


def test_scheduler_diag_marks_fresh_worker_status():
    now = datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)
    result = summarize_scheduler(
        cron_schedule="*/15 * * * *",
        deployments=[
            {
                "id": "d1",
                "status": "SUCCESS",
                "created_at": "2026-08-18T14:20:00Z",
                "commit_sha": "abc",
            }
        ],
        checked_at=(now - timedelta(minutes=5)).isoformat(),
        worker_sha="abc",
        now=now,
    )
    assert result["diagnosis"] == "DAY_WORKER_STATUS_FRESH"
    assert result["active_deployment_count"] == 0


def test_scheduler_diag_flags_possible_overlap_when_stale_and_active():
    now = datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)
    result = summarize_scheduler(
        cron_schedule="*/15 * * * *",
        deployments=[
            {
                "id": "d1",
                "status": "DEPLOYING",
                "created_at": "2026-08-18T13:15:00Z",
                "commit_sha": "abc",
            }
        ],
        checked_at=(now - timedelta(hours=1)).isoformat(),
        worker_sha="old",
        now=now,
    )
    assert result["diagnosis"] == "POSSIBLE_OVERLAP_BLOCK"
    assert result["active_deployment_count"] == 1


def test_scheduler_diag_flags_stale_without_active_deployment():
    now = datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)
    result = summarize_scheduler(
        cron_schedule="0 * * * *",
        deployments=[{"id": "d1", "status": "SUCCESS"}],
        checked_at=(now - timedelta(hours=1)).isoformat(),
        worker_sha="old",
        now=now,
    )
    assert result["diagnosis"] == "STALE_DAY_WORKER_STATUS"
    assert result["cron_schedule"] == "0 * * * *"
