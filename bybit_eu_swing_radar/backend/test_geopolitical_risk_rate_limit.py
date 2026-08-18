from __future__ import annotations

import asyncio

import httpx
import pytest

from app import research_geopolitical_risk_api as api


def test_retry_delay_honors_seconds_and_is_bounded():
    response = httpx.Response(429, headers={"Retry-After": "7"})
    assert api._retry_delay(response, 0) == 7.0

    response = httpx.Response(429, headers={"Retry-After": "999"})
    assert api._retry_delay(response, 0) == api.GDELT_MAX_RETRY_AFTER_SECONDS

    response = httpx.Response(429, headers={"Retry-After": "not-a-number"})
    assert api._retry_delay(response, 0) == 12.0
    assert api._retry_delay(response, 1) == 24.0


def test_provider_request_retries_429_then_succeeds(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, request=request)
        return httpx.Response(200, json={"timeline": []}, request=request)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)

    async def scenario() -> tuple[dict, int]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await api._provider_request(client, api.GDELT_DOC_URL, {"query": "war"})

    payload, retries = asyncio.run(scenario())
    assert payload == {"timeline": []}
    assert retries == 1
    assert calls == 2
    assert sleeps == [3.0]


def test_provider_request_does_not_retry_non_429(monkeypatch):
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await api._provider_request(client, api.GDELT_DOC_URL, {"query": "war"})

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(scenario())
    assert calls == 1
    assert sleeps == []
