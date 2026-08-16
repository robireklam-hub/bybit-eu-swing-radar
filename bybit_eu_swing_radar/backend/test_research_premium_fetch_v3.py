import pytest

from research_premium_fetch_v3 import HistoricalPremiumAPI, PAGE_LIMIT


class FakePremiumAPI(HistoricalPremiumAPI):
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    async def _get(self, path: str, **params):
        self.calls.append((path, params))
        if not self.pages:
            raise AssertionError("unexpected extra request")
        return self.pages.pop(0)


@pytest.mark.asyncio
async def test_premium_history_pages_backwards_and_normalizes():
    first_ms = 1_700_000_000_000
    second_ms = first_ms + 3_600_000
    third_ms = second_ms + 3_600_000
    full_page = [[str(third_ms), "0", "0", "0", "0.003"]]
    full_page.extend(
        [[str(second_ms), "0", "0", "0", "0.002"]] * (PAGE_LIMIT - 1)
    )
    api = FakePremiumAPI(
        [
            {"retCode": 0, "result": {"list": full_page}},
            {
                "retCode": 0,
                "result": {"list": [[str(first_ms), "0", "0", "0", "0.001"]]},
            },
        ]
    )
    points = await api.premium_history(
        "BTCUSDT", start_ms=first_ms, end_ms=third_ms
    )
    assert [(point.ts, point.close) for point in points] == [
        (first_ms // 1000, 0.001),
        (second_ms // 1000, 0.002),
        (third_ms // 1000, 0.003),
    ]
    assert api.calls[1][1]["end"] == second_ms - 1
