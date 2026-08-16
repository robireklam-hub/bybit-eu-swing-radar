import pytest

from research_historical_flow_fetch_v2 import HistoricalFlowAPI, choose_derivative_market


class FakeFlowAPI(HistoricalFlowAPI):
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    async def _get(self, path: str, **params):
        self.calls.append((path, params))
        if not self.pages:
            raise AssertionError("unexpected extra request")
        return self.pages.pop(0)


def test_choose_derivative_market_matches_turnover_first_policy():
    instruments = [
        {
            "status": "Trading",
            "baseCoin": "BTC",
            "quoteCoin": "USDC",
            "contractType": "LinearPerpetual",
            "symbol": "BTCPERP",
        },
        {
            "status": "Trading",
            "baseCoin": "BTC",
            "quoteCoin": "USDT",
            "contractType": "LinearPerpetual",
            "symbol": "BTCUSDT",
        },
    ]
    tickers = [
        {"symbol": "BTCPERP", "turnover24h": "100"},
        {"symbol": "BTCUSDT", "turnover24h": "1000"},
    ]
    assert choose_derivative_market("BTC", instruments, tickers) == "BTCUSDT"


@pytest.mark.asyncio
async def test_open_interest_history_follows_cursor_and_normalizes():
    api = FakeFlowAPI(
        [
            {
                "retCode": 0,
                "result": {
                    "list": [{"timestamp": "2000", "openInterest": "2"}],
                    "nextPageCursor": "next",
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [{"timestamp": "1000", "openInterest": "1"}],
                    "nextPageCursor": "",
                },
            },
        ]
    )
    points = await api.open_interest_history("BTCUSDT", start_ms=0, end_ms=3000)
    assert [(point.ts, point.value) for point in points] == [(1, 1.0), (2, 2.0)]
    assert api.calls[1][1]["cursor"] == "next"


@pytest.mark.asyncio
async def test_funding_history_walks_backwards_without_forward_leakage():
    api = FakeFlowAPI(
        [
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {"fundingRateTimestamp": "300000", "fundingRate": "0.003"},
                        {"fundingRateTimestamp": "200000", "fundingRate": "0.002"},
                    ]
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {"fundingRateTimestamp": "100000", "fundingRate": "0.001"},
                    ]
                },
            },
        ]
    )
    points = await api.funding_history("BTCUSDT", start_ms=100000, end_ms=300000)
    assert [(point.ts, point.rate) for point in points] == [
        (100, 0.001),
        (200, 0.002),
        (300, 0.003),
    ]
    assert api.calls[1][1]["endTime"] == 199999
