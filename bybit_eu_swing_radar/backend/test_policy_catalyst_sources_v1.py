from research.policy_catalyst_sources_v1 import (
    SEC_2026_76_FIXTURE_URL,
    classify_primary_policy_url,
    regression_fixtures,
    source_registry,
)


def test_sec_press_release_is_primary_crypto_regulation_context():
    classified = classify_primary_policy_url(SEC_2026_76_FIXTURE_URL)
    assert classified is not None
    assert classified["provider_code"] == "SEC"
    assert classified["authority_tier"] == "PRIMARY_REGULATOR"
    assert classified["event_class"] == "US_CRYPTO_REGULATION"
    assert classified["context_only"] is True
    assert classified["hard_gate"] is False
    assert classified["score_mutation"] is False
    assert classified["ranking_mutation"] is False
    assert classified["eligibility_mutation"] is False
    assert classified["execution_mutation"] is False


def test_sec_2026_76_is_frozen_regression_fixture_not_trade_direction():
    fixtures = regression_fixtures()
    fixture = next(item for item in fixtures if item["release_no"] == "2026-76")
    assert fixture["url"] == SEC_2026_76_FIXTURE_URL
    assert fixture["event_class"] == "US_CRYPTO_REGULATION"
    assert fixture["trade_direction"] is None
    assert fixture["causal_attribution"] == "UNCONFIRMED_CONTEXT_ONLY"


def test_registry_monitors_sec_press_release_index_not_single_release_only():
    registry = source_registry()
    sec = next(item for item in registry if item["provider_code"] == "SEC")
    assert sec["monitor_url"] == "https://www.sec.gov/newsroom/press-releases"
    assert sec["allowed_path_prefix"] == "/newsroom/press-releases"
    assert "US_CRYPTO_REGULATION" in sec["event_classes"]


def test_spoofed_or_insecure_sec_urls_are_rejected():
    assert classify_primary_policy_url(
        "https://example.com/newsroom/press-releases/2026-76-sec-proposes-new-regulation-crypto-assets"
    ) is None
    assert classify_primary_policy_url(
        "http://www.sec.gov/newsroom/press-releases/2026-76-sec-proposes-new-regulation-crypto-assets"
    ) is None
