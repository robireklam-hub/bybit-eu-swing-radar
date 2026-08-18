from scripts.production_event_tokenomics_shadow_smoke import run_smoke, validate_capture


def _capture() -> dict:
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "persisted": True,
        "captured_at": "2026-08-18T06:00:01+00:00",
        "captured_hour": "2026-08-18T06:00:00+00:00",
        "source_commit_sha": "abc",
        "spec": {"version": "event-tokenomics-shadow-v1"},
        "tracked_symbols": ["BTCUSDC", "ETHUSDC"],
        "event_count": 1,
        "coverage": {
            "tracked_symbol_count": 2,
            "source_status": {
                "fomc_schedule": {"status": "LIVE", "events": 1},
                "bls_macro": {"status": "LIVE", "events": 1},
                "coinmarketcal": {"status": "MISSING_KEY", "events": 0},
            },
        },
        "events": [{"event_type": "MACRO_FOMC_MINUTES", "title": "x"}],
    }


def test_validate_capture_accepts_missing_optional_keys() -> None:
    ok, reason = validate_capture(_capture())
    assert ok is True
    assert reason == "ok"


def test_smoke_accepts_concurrent_same_hour_exact_sha_overwrite() -> None:
    capture = _capture()

    def fetch(url: str, api_key: str, timeout: float, method: str = "GET") -> dict:
        if url.endswith("/version"):
            return {"commit_sha": "abc"}
        if url.endswith("/capture"):
            return capture
        if url.endswith("/status"):
            return {
                "research_only": True,
                "promotion_allowed": False,
                "snapshot_count": 1,
                "latest": {
                    "captured_at": "2026-08-18T06:00:02+00:00",
                    "captured_hour": capture["captured_hour"],
                    "source_commit_sha": "abc",
                },
            }
        raise AssertionError(url)

    assert run_smoke("https://example", "key", "abc", fetch=fetch, sleep=lambda _: None) == 0


def test_smoke_rejects_wrong_sha_even_in_same_hour() -> None:
    capture = _capture()

    def fetch(url: str, api_key: str, timeout: float, method: str = "GET") -> dict:
        if url.endswith("/version"):
            return {"commit_sha": "abc"}
        if url.endswith("/capture"):
            return capture
        return {
            "research_only": True,
            "promotion_allowed": False,
            "snapshot_count": 1,
            "latest": {
                "captured_hour": capture["captured_hour"],
                "source_commit_sha": "older",
            },
        }

    assert run_smoke("https://example", "key", "abc", fetch=fetch, sleep=lambda _: None) == 1
