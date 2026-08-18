from pathlib import Path

from app.research_eth_onchain_api import _merge_metric_rows


def test_main_attaches_eth_onchain_routes_directly():
    path = Path(__file__).resolve().parent / "app" / "main.py"
    text = path.read_text()
    assert "from app.research_eth_onchain_api import attach_eth_onchain_research" in text
    assert "attach_eth_onchain_research(app, require_api_key)" in text


def test_eth_onchain_routes_are_hidden_and_authenticated():
    path = Path(__file__).resolve().parent / "app" / "research_eth_onchain_api.py"
    text = path.read_text()
    for route in (
        "/v1/research/eth-onchain/spec",
        "/v1/research/eth-onchain/capture",
        "/v1/research/eth-onchain/status",
    ):
        assert route in text
    assert text.count("dependencies=[Depends(require_api_key)]") >= 3
    assert text.count("include_in_schema=False") >= 3


def test_coin_metrics_requests_are_isolated_and_fail_transparent():
    path = Path(__file__).resolve().parent / "app" / "research_eth_onchain_api.py"
    text = path.read_text()
    assert '"metrics": metric' in text
    assert '"ignore_forbidden_errors": "true"' in text
    assert '"ignore_unsupported_errors": "true"' in text
    assert "asyncio.gather" in text
    assert "for metric in COIN_METRICS" in text


def test_metric_rows_merge_by_timestamp_without_overwriting_other_metrics():
    results = [
        (
            "TxCnt",
            [{"asset": "eth", "time": "2026-08-17T00:00:00Z", "TxCnt": "10"}],
            {"status": "LIVE"},
        ),
        (
            "FeeTotNtv",
            [{"asset": "eth", "time": "2026-08-17T00:00:00Z", "FeeTotNtv": "2"}],
            {"status": "LIVE"},
        ),
    ]
    rows = _merge_metric_rows(results)
    assert rows == [
        {
            "asset": "eth",
            "time": "2026-08-17T00:00:00Z",
            "TxCnt": "10",
            "FeeTotNtv": "2",
        }
    ]
