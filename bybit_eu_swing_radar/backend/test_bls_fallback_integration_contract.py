import inspect

from app import research_event_tokenomics_api as api


def test_bls_fetch_has_explicit_partial_official_fallback() -> None:
    source = inspect.getsource(api._fetch_bls)
    assert "embedded_bls_2026_events" in source
    assert '"PARTIAL"' in source
    assert 'fallback_mode="EMBEDDED_OFFICIAL_2026"' in source
