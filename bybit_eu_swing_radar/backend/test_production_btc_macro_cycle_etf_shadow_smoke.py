from scripts.production_btc_macro_cycle_etf_shadow_smoke import run_smoke, validate_capture


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
        "spec": {"version": "btc-macro-cycle-etf-shadow-v1"},
        "cycle": {"tip_height": 950000},
        "btc_price": {"symbol": "BTCUSDC", "data_points": 299},
        "macro": {"us_10y_yield": {"latest": 4.5}, "broad_usd_index": {"latest": 120.0}},
        "etf": {"latest_daily_flow_usd": 1.0},
        "coverage": {"source_status": {
            "cycle": {"status": "LIVE"},
            "btc_price": {"status": "LIVE"},
            "fred_us_10y_yield": {"status": "LIVE"},
            "fred_broad_usd_index": {"status": "LIVE"},
            "etf_flows": {"status": "LIVE"},
        }},
    }


def test_validate_capture_requires_live_etf_and_macro() -> None:
    ok, reason = validate_capture(_capture())
    assert ok is True
    assert reason == "ok"


def test_smoke_accepts_same_hour_rewrite_with_exact_sha() -> None:
    capture = _capture()
    def fetch(url: str, api_key: str, timeout: float, method: str = "GET") -> dict:
        if url.endswith("/version"):
            return {"commit_sha": "abc"}
        if url.endswith("/capture"):
            return capture
        if url.endswith("/status"):
            return {"snapshot_count": 1, "latest": {"captured_hour": capture["captured_hour"], "source_commit_sha": "abc"}}
        raise AssertionError(url)
    assert run_smoke("https://example", "key", "abc", fetch=fetch, sleep=lambda _: None) == 0
