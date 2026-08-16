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
    first_ms = 1_700_000_000_000
    second_ms = first_ms + 3_600_000
    api = FakeFlowAPI(
        [
            {
                "retCode": 0,
                "result": {
                    "list": [{"timestamp": str(second_ms), "openInterest": "2"}],
                    "nextPageCursor": "next",
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [{"timestamp": str(first_ms), "openInterest": "1"}],
                    "nextPageCursor": "",
                },
            },
        ]
    )
    points = await api.open_interest_history(
        "BTCUSDT", start_ms=first_ms, end_ms=second_ms
    )
    assert [(point.ts, point.value) for point in points] == [
        (first_ms // 1000, 1.0),
        (second_ms // 1000, 2.0),
    ]
    assert api.calls[1][1]["cursor"] == "next"


@pytest.mark.asyncio
async def test_funding_history_walks_backwards_without_forward_leakage():
    first_ms = 1_700_000_000_000
    second_ms = first_ms + 8 * 3_600_000
    third_ms = second_ms + 8 * 3_600_000
    api = FakeFlowAPI(
        [
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {"fundingRateTimestamp": str(third_ms), "fundingRate": "0.003"},
                        {"fundingRateTimestamp": str(second_ms), "fundingRate": "0.002"},
                    ]
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {"fundingRateTimestamp": str(first_ms), "fundingRate": "0.001"},
                    ]
                },
            },
        ]
    )
    points = await api.funding_history(
        "BTCUSDT", start_ms=first_ms, end_ms=third_ms
    )
    assert [(point.ts, point.rate) for point in points] == [
        (first_ms // 1000, 0.001),
        (second_ms // 1000, 0.002),
        (third_ms // 1000, 0.003),
    ]
    assert api.calls[1][1]["endTime"] == second_ms - 1
