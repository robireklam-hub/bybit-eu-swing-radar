from __future__ import annotations

import pytest

from scripts.run_swing_liquidity_shadow_worker_lineage_guarded import (
    collect_snapshot_with_worker_lineage,
)


API_SHA = "a" * 40
WORKER_SHA = API_SHA
CHECKED_AT = "2026-08-22T00:00:00+00:00"


def _snapshot() -> dict:
    return {
        "scan_data_as_of": CHECKED_AT,
        "candidate_count": 1,
        "candidates": [{"symbol": "BTCUSDC", "side": "long"}],
    }


def _fetch_factory(*, api_sha: str = API_SHA, worker_sha: str = WORKER_SHA, checked_at: str = CHECKED_AT):
    def fetch(url: str, api_key: str | None, timeout: float) -> dict:
        assert timeout == 20.0
        if url.endswith("/version"):
            assert api_key is None
            return {"commit_sha": api_sha}
        if url.endswith("/v1/data-status"):
            assert api_key == "secret"
            return {
                "checked_at": checked_at,
                "worker": {"status": "ok", "source_commit_sha": worker_sha},
            }
        raise AssertionError(url)

    return fetch


def test_worker_lineage_guard_accepts_exact_api_worker_and_scan_identity():
    result = collect_snapshot_with_worker_lineage(
        "https://example.test",
        "secret",
        "https://api.bybit.eu",
        collect=lambda *_: _snapshot(),
        fetch=_fetch_factory(),
    )
    assert result["source_commit_sha"] == API_SHA
    assert result["swing_worker_source_commit_sha"] == WORKER_SHA
    assert result["swing_worker_checked_at"] == CHECKED_AT
    assert result["source_commit_semantics"] == "api_and_swing_worker_exact_commit_parity"
    assert result["candidates"][0]["swing_worker_source_commit_sha"] == WORKER_SHA


def test_worker_lineage_guard_rejects_api_worker_commit_mismatch():
    with pytest.raises(RuntimeError, match="API and swing worker commits differ"):
        collect_snapshot_with_worker_lineage(
            "https://example.test",
            "secret",
            "https://api.bybit.eu",
            collect=lambda *_: _snapshot(),
            fetch=_fetch_factory(worker_sha="b" * 40),
        )


def test_worker_lineage_guard_rejects_scan_status_timestamp_mismatch():
    with pytest.raises(RuntimeError, match="timestamp does not match collected scan"):
        collect_snapshot_with_worker_lineage(
            "https://example.test",
            "secret",
            "https://api.bybit.eu",
            collect=lambda *_: _snapshot(),
            fetch=_fetch_factory(checked_at="2026-08-22T00:00:01+00:00"),
        )


@pytest.mark.parametrize("api_sha,worker_sha", [("bad", WORKER_SHA), (API_SHA, "bad")])
def test_worker_lineage_guard_rejects_invalid_commit_identity(api_sha: str, worker_sha: str):
    with pytest.raises(RuntimeError, match="commit SHA invalid"):
        collect_snapshot_with_worker_lineage(
            "https://example.test",
            "secret",
            "https://api.bybit.eu",
            collect=lambda *_: _snapshot(),
            fetch=_fetch_factory(api_sha=api_sha, worker_sha=worker_sha),
        )
