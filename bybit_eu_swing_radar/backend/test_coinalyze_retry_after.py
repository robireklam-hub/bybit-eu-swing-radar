from __future__ import annotations

import pytest

import worker
from worker import CoinalyzeAPI


class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, bool]:
        return {"ok": True}


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_sleep"),
    [
        ({"Retry-After": "41.975"}, 42),
        ({"Retry-After": "5"}, 5),
        ({"Retry-After": "not-a-number"}, 60),
        ({}, 60),
    ],
)
async def test_coinalyze_retry_after_accepts_decimal_and_falls_back_safely(
    monkeypatch,
    headers,
    expected_sleep,
):
    monkeypatch.setattr(worker, "COINALYZE_API_KEY", "test-key")
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)
    client = FakeClient(
        [
            FakeResponse(429, headers),
            FakeResponse(200),
        ]
    )

    payload = await CoinalyzeAPI(client).get("/open-interest")

    assert payload == {"ok": True}
    assert sleeps == [expected_sleep]
    assert client.calls == 2
