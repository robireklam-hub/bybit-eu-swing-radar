from __future__ import annotations

from scripts import production_day_derivatives_context_smoke as smoke


SHA = "abc123"


def _status(sha: str = SHA) -> dict:
    return {
        "worker": {
            "source_commit_sha": sha,
            "coinalyze_priority_symbols": list(smoke.CORE_SYMBOLS),
            "coinalyze_priority_targeted_symbols": list(smoke.CORE_SYMBOLS),
            "coinalyze_priority_missing_analysis_symbols": [],
            "coinalyze_priority_target_coverage_complete": True,
        }
    }


def _setup(symbol: str, *, usable: bool = False) -> dict:
    return {
        "symbol": symbol,
        "derivatives": {"funding_rate": 0.0001} if usable else {},
    }


def _flow(symbol: str, *, sha: str = SHA, usable: bool = True) -> dict:
    return {
        "symbol": symbol,
        "source_commit_sha": sha,
        "data_quality": "GOOD" if usable else "DEGRADED",
        "coverage_status": "GOOD" if usable else "STALE_FLOW_CONTEXT",
        "bybit_global_derivatives": (
            {"open_interest_value_quote": 1_000_000, "funding_rate_decimal": 0.0001}
            if usable
            else {}
        ),
    }


def test_evaluate_accepts_fresh_flow_fallback_when_setup_context_is_empty() -> None:
    setups = {symbol: _setup(symbol) for symbol in smoke.CORE_SYMBOLS}
    flows = {symbol: _flow(symbol) for symbol in smoke.CORE_SYMBOLS}

    assert smoke.evaluate(_status(), setups, flows, SHA) == []


def test_evaluate_fails_closed_on_missing_core_target_or_stale_flow() -> None:
    status = _status()
    status["worker"]["coinalyze_priority_targeted_symbols"] = ["BTCUSDC"]
    setups = {symbol: _setup(symbol) for symbol in smoke.CORE_SYMBOLS}
    flows = {symbol: _flow(symbol) for symbol in smoke.CORE_SYMBOLS}
    flows["ETHUSDC"] = _flow("ETHUSDC", usable=False)

    failures = smoke.evaluate(status, setups, flows, SHA)

    assert any("day core target mismatch" in failure for failure in failures)
    assert any("ETHUSDC: Flow fallback" in failure for failure in failures)


def test_run_smoke_polls_until_exact_day_and_flow_workers_execute() -> None:
    stale_flows = {
        symbol: _flow(symbol, sha="old") for symbol in smoke.CORE_SYMBOLS
    }
    current_flows = {symbol: _flow(symbol) for symbol in smoke.CORE_SYMBOLS}
    setups = {symbol: _setup(symbol) for symbol in smoke.CORE_SYMBOLS}
    batches = [
        {"/version": {"commit_sha": SHA}},
        {
            "/v1/day-trade/status": _status("old"),
            **{
                f"/v1/day-trade/setup/{symbol}": setups[symbol]
                for symbol in smoke.CORE_SYMBOLS
            },
            **{
                f"/v1/day-trade/flow/{symbol}": stale_flows[symbol]
                for symbol in smoke.CORE_SYMBOLS
            },
        },
        {
            "/v1/day-trade/status": _status(),
            **{
                f"/v1/day-trade/setup/{symbol}": setups[symbol]
                for symbol in smoke.CORE_SYMBOLS
            },
            **{
                f"/v1/day-trade/flow/{symbol}": current_flows[symbol]
                for symbol in smoke.CORE_SYMBOLS
            },
        },
    ]
    call_index = 0

    def fetch(url: str, _api_key: str, _timeout: float) -> dict:
        nonlocal call_index
        path = url.removeprefix("https://prod")
        if path == "/version":
            return batches[0][path]
        batch_index = 1 if call_index < 7 else 2
        call_index += 1
        return batches[batch_index][path]

    sleeps: list[float] = []
    result = smoke.run_smoke(
        "https://prod",
        "secret",
        SHA,
        fetch=fetch,
        sleep=sleeps.append,
    )

    assert result == 0
    assert sleeps == [15]
