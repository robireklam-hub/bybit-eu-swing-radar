from pathlib import Path


def test_main_attaches_geopolitical_risk_routes():
    path = Path(__file__).resolve().parent / "app" / "main.py"
    text = path.read_text()
    assert "from app.research_geopolitical_risk_api import attach_geopolitical_risk_research" in text
    assert "attach_geopolitical_risk_research(app, require_api_key)" in text


def test_geopolitical_risk_routes_are_hidden_and_authenticated():
    path = Path(__file__).resolve().parent / "app" / "research_geopolitical_risk_api.py"
    text = path.read_text()
    for route in (
        "/v1/research/geopolitical-risk/spec",
        "/v1/research/geopolitical-risk/capture",
        "/v1/research/geopolitical-risk/status",
    ):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert text.count("include_in_schema=False") >= 3


def test_provider_contract_is_fixed_gdelt_attention_only():
    path = Path(__file__).resolve().parent / "app" / "research_geopolitical_risk_api.py"
    text = path.read_text()
    assert 'GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"' in text
    assert 'GDELT_DOC_HTTP_URL = "http://api.gdeltproject.org/api/v2/doc/doc"' in text
    assert '"mode": "TimelineVolRaw"' in text
    assert '"timespan": "24h"' in text


def test_gdelt_requests_are_serialized_paced_and_429_retry_is_bounded():
    path = Path(__file__).resolve().parent / "app" / "research_geopolitical_risk_api.py"
    text = path.read_text()
    assert "GDELT_REQUEST_SPACING_SECONDS = 6.0" in text
    assert "GDELT_MAX_RATE_LIMIT_RETRIES = 2" in text
    assert "GDELT_RATE_LIMIT_BACKOFF_SECONDS = (12.0, 24.0)" in text
    assert "response.status_code == 429" in text
    assert "await asyncio.sleep(_retry_delay(response, attempt))" in text
    assert "await asyncio.sleep(GDELT_REQUEST_SPACING_SECONDS)" in text
    assert "asyncio.gather" not in text
    assert '"rate_limit_retries"' in text


def test_http_fallback_is_connect_only_and_explicitly_degraded():
    path = Path(__file__).resolve().parent / "app" / "research_geopolitical_risk_api.py"
    text = path.read_text()
    assert "except (httpx.ConnectError, httpx.ConnectTimeout)" in text
    assert 'transport="HTTP"' in text
    assert '"PLAINTEXT_PROVIDER_FALLBACK"' in text
    assert 'return name, payload, _status(\n                    "PARTIAL"' in text


def test_geopolitical_layer_does_not_read_trade_outcomes_or_live_strategy_state():
    root = Path(__file__).resolve().parent
    core = (root / "research" / "geopolitical_risk_shadow.py").read_text().lower()
    api = (root / "app" / "research_geopolitical_risk_api.py").read_text().lower()
    for forbidden in (
        "day_trade_journal",
        "net_r",
        "profit_factor",
        "strict_eligible",
        "borrowability",
        "setup_score",
    ):
        assert forbidden not in core
        assert forbidden not in api
