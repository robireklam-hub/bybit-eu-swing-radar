"""Official BLS 2026 key-release schedule fallback for Railway ICS blocking.

Dates are transcribed from the BLS release schedule pages. This is a bounded
fallback only: it is explicit PARTIAL coverage and must not be presented as a
live network feed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SOURCE_URL = "https://www.bls.gov/schedule/2026/"

# date, local time, type, title, severity
BLS_2026_KEY_RELEASES = [
    ("2026-09-01", "10:00", "MACRO_JOLTS", "Job Openings and Labor Turnover Survey — July 2026", "MEDIUM_HIGH"),
    ("2026-09-04", "08:30", "MACRO_JOBS", "Employment Situation — August 2026", "HIGH"),
    ("2026-09-10", "08:30", "MACRO_PPI", "Producer Price Index — August 2026", "MEDIUM_HIGH"),
    ("2026-09-11", "08:30", "MACRO_CPI", "Consumer Price Index — August 2026", "HIGH"),
    ("2026-09-29", "10:00", "MACRO_JOLTS", "Job Openings and Labor Turnover Survey — August 2026", "MEDIUM_HIGH"),
    ("2026-10-02", "08:30", "MACRO_JOBS", "Employment Situation — September 2026", "HIGH"),
    ("2026-10-14", "08:30", "MACRO_CPI", "Consumer Price Index — September 2026", "HIGH"),
    ("2026-10-15", "08:30", "MACRO_PPI", "Producer Price Index — September 2026", "MEDIUM_HIGH"),
    ("2026-10-30", "08:30", "MACRO_ECI", "Employment Cost Index — Third Quarter 2026", "MEDIUM"),
    ("2026-11-03", "10:00", "MACRO_JOLTS", "Job Openings and Labor Turnover Survey — September 2026", "MEDIUM_HIGH"),
    ("2026-11-06", "08:30", "MACRO_JOBS", "Employment Situation — October 2026", "HIGH"),
    ("2026-11-10", "08:30", "MACRO_CPI", "Consumer Price Index — October 2026", "HIGH"),
    ("2026-11-13", "08:30", "MACRO_PPI", "Producer Price Index — October 2026", "MEDIUM_HIGH"),
    ("2026-12-01", "10:00", "MACRO_JOLTS", "Job Openings and Labor Turnover Survey — October 2026", "MEDIUM_HIGH"),
    ("2026-12-04", "08:30", "MACRO_JOBS", "Employment Situation — November 2026", "HIGH"),
    ("2026-12-10", "08:30", "MACRO_CPI", "Consumer Price Index — November 2026", "HIGH"),
    ("2026-12-15", "08:30", "MACRO_PPI", "Producer Price Index — November 2026", "MEDIUM_HIGH"),
]


def embedded_bls_2026_events() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for date_text, time_text, event_type, title, severity in BLS_2026_KEY_RELEASES:
        local = datetime.fromisoformat(f"{date_text}T{time_text}:00").replace(tzinfo=NY)
        dt = local.astimezone(timezone.utc)
        result.append({
            "event_id": f"bls:fallback:{event_type}:{date_text}",
            "event_type": event_type,
            "title": title,
            "event_at": dt.isoformat(),
            "date_precision": "EXACT",
            "severity": severity,
            "symbols": [],
            "source": {
                "name": "U.S. Bureau of Labor Statistics",
                "url": SOURCE_URL,
                "official": True,
            },
            "metadata": {
                "fallback_mode": "EMBEDDED_OFFICIAL_2026",
                "network_live": False,
            },
        })
    return result
