from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.microstructure_research as research_api


class FakeRecorder:
    def __init__(self, *, enabled: bool = True):
        self.config = SimpleNamespace(
            enabled=enabled,
            symbols=("BTCUSDC",),
            bucket_seconds=5,
            depth=50,
        )
        self.run_calls = 0
        self.running = False
        self._release = asyncio.Event()

    async def run(self) -> None:
        self.run_calls += 1
        self.running = True
        try:
            await self._release.wait()
        finally:
            self.running = False

    async def stop(self) -> None:
        self._release.set()

    def status(self):
        return {
            "research_only": True,
            "live_strategy_mutated": False,
            "enabled": self.config.enabled,
            "running": self.running,
            "symbols": list(self.config.symbols),
        }


@pytest.mark.asyncio
async def test_ensure_task_started_is_idempotent(monkeypatch) -> None:
    recorder = FakeRecorder()
    monkeypatch.setattr(research_api, "_recorder", recorder)
    monkeypatch.setattr(research_api, "_config", recorder.config)
    monkeypatch.setattr(research_api, "_task", None)

    assert research_api._ensure_task_started() is True
    await asyncio.sleep(0)
    assert recorder.run_calls == 1
    assert recorder.running is True
    assert research_api._ensure_task_started() is False

    await research_api._shutdown()
    assert recorder.running is False


@pytest.mark.asyncio
async def test_status_route_self_starts_without_lifecycle_support(monkeypatch) -> None:
    recorder = FakeRecorder()
    monkeypatch.setattr(research_api, "_recorder", recorder)
    monkeypatch.setattr(research_api, "_config", recorder.config)
    monkeypatch.setattr(research_api, "_task", None)

    captured = {}

    class MinimalApp:
        def get(self, path, **kwargs):
            def decorator(function):
                captured[path] = function
                return function
            return decorator

    research_api.attach_microstructure_research(MinimalApp(), lambda: None)
    payload = await captured["/v1/research/microstructure/status"]()

    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["enabled"] is True
    assert payload["running"] is True
    assert recorder.run_calls == 1

    await research_api._shutdown()


def test_disabled_recorder_does_not_start(monkeypatch) -> None:
    recorder = FakeRecorder(enabled=False)
    monkeypatch.setattr(research_api, "_recorder", recorder)
    monkeypatch.setattr(research_api, "_config", recorder.config)
    monkeypatch.setattr(research_api, "_task", None)

    assert research_api._ensure_task_started() is False
    assert recorder.run_calls == 0
