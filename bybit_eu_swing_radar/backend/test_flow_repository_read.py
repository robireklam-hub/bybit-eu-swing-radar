import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import sys
from types import ModuleType, SimpleNamespace


NOW = datetime.now(timezone.utc)


def flow(symbol, age, *, batch="batch-a", coverage="GOOD"):
    return {
        "strategy_mode": "DAY_TRADE",
        "strategy_version": "0.7.2",
        "feature_version": "0.7.2.2",
        "symbol": symbol,
        "data_as_of": (NOW - timedelta(seconds=age)).isoformat(),
        "data_as_of_budapest": NOW.isoformat(),
        "data_quality": "GOOD",
        "coverage_status": coverage,
        "flow_batch_id": batch,
    }


def load_repository(monkeypatch):
    asyncpg = ModuleType("asyncpg")
    asyncpg.Connection = object
    exceptions = ModuleType("asyncpg.exceptions")
    exceptions.UndefinedTableError = type("UndefinedTableError", (Exception,), {})
    asyncpg.exceptions = exceptions
    config = ModuleType("app.config")
    config.settings = SimpleNamespace(database_url="unused", radar_api_key="key")
    models = ModuleType("app.models")

    class FlowModel:
        @classmethod
        def model_validate(cls, value):
            assert value["coverage_status"] != "GOOD" or value["data_quality"] == "GOOD"
            return SimpleNamespace(**value)

    names = [
        "BacktestAggregate", "BacktestGroupStats", "DayTradeBacktestSignal",
        "DayTradeBacktestSignalsResponse", "DayTradeBacktestStatusResponse",
        "DayTradeBacktestSummaryResponse", "DayTradeDiagnosticStatusResponse",
        "DayTradeEdgeDiagnosticsResponse", "DayTradeGateWaterfallResponse",
        "DiagnosticCohortStats", "DiagnosticCountGroup", "DiagnosticGateStep",
        "DiagnosticSegment", "DiagnosticSensitivityStats", "ExcursionThreshold",
        "DayTradeCandidate", "DayTradeJournalSignal", "DayTradeJournalSignalsResponse",
        "DayTradeJournalSummaryResponse", "DayTradeScanResponse", "DayTradeTopCandidatesResponse",
        "DayTradeSymbolAuditResponse", "JournalAggregate", "JournalGroupStats", "MarketRegime",
        "MomentumResponse", "ScanResponse", "Setup", "TopCandidatesResponse", "WatchlistResponse",
    ]
    for name in names:
        setattr(models, name, type(name, (), {}))
    models.DayTradeFlowContextResponse = FlowModel
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setitem(sys.modules, "asyncpg.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "app.config", config)
    monkeypatch.setitem(sys.modules, "app.models", models)
    sys.modules.pop("app.repository", None)
    return importlib.import_module("app.repository")


class FakeRepository:
    def __init__(self, values):
        self.values = values
        self.reads = []
        self.writes = 0

    async def get_cache(self, key):
        self.reads.append(key)
        return self.values.get(key)


def test_repository_context_read_applies_freshness_before_validation(monkeypatch):
    module = load_repository(monkeypatch)
    cached = flow("PENGUUSDC", 301)
    original = deepcopy(cached)
    repo = FakeRepository({"day_trade_flow:PENGUUSDC": cached})
    result = asyncio.run(module._get_day_trade_flow(repo, "PENGUUSDC"))
    assert result.data_quality == "DEGRADED" and result.coverage_status != "GOOD"
    assert cached == original and repo.writes == 0


def test_repository_context_read_keeps_fresh_good(monkeypatch):
    module = load_repository(monkeypatch)
    repo = FakeRepository({"day_trade_flow:WIFUSDC": flow("WIFUSDC", 0)})
    result = asyncio.run(module._get_day_trade_flow(repo, "WIFUSDC"))
    assert result.data_quality == "GOOD" and result.coverage_status == "GOOD"


def test_repository_status_recounts_batch_and_ignores_foreign_symbol(monkeypatch):
    module = load_repository(monkeypatch)
    status = {"symbols": ["FRESH", "STALE", "MISMATCH", "MISSING", "NOMATCH"], "flow_batch_id": "batch-a"}
    repo = FakeRepository({
        "day_trade_flow_status": status,
        "day_trade_flow:FRESH": flow("FRESH", 0),
        "day_trade_flow:STALE": flow("STALE", 301),
        "day_trade_flow:MISMATCH": flow("MISMATCH", 0, batch="batch-b"),
        "day_trade_flow:NOMATCH": flow("NOMATCH", 0, coverage="NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH"),
        "day_trade_flow:FOREIGN": flow("FOREIGN", 0),
    })
    result = asyncio.run(module._get_day_trade_flow_status(repo))
    assert result == {**status, "processed": 5, "good": 1, "partial": 3, "no_derivative_match": 1}
    assert "day_trade_flow:FOREIGN" not in repo.reads


def test_repository_status_rejects_old_status_new_batch_payload(monkeypatch):
    module = load_repository(monkeypatch)
    values = {"day_trade_flow_status": {"symbols": ["BTC"], "flow_batch_id": "batch-a"},
              "day_trade_flow:BTC": flow("BTC", 0, batch="batch-b")}
    repo = FakeRepository(values)
    assert asyncio.run(module._get_day_trade_flow_status(repo))["partial"] == 1
    values["day_trade_flow_status"] = {"symbols": ["BTC"], "flow_batch_id": "batch-b"}
    assert asyncio.run(module._get_day_trade_flow_status(repo))["good"] == 1


def test_repository_legacy_status_always_moves_good_to_partial(monkeypatch):
    module = load_repository(monkeypatch)
    for status in (
        {"flow_batch_id": "batch-a", "good": 2, "partial": 1, "no_derivative_match": 3},
        {"symbols": ["BTC"], "data_as_of": NOW.isoformat(), "good": 2, "partial": 1, "no_derivative_match": 3},
        {"symbols": ["BTC"], "data_as_of": (NOW - timedelta(days=1)).isoformat(), "good": 2, "partial": 1, "no_derivative_match": 3},
    ):
        result = asyncio.run(module._get_day_trade_flow_status(FakeRepository({"day_trade_flow_status": status})))
        assert (result["good"], result["partial"], result["no_derivative_match"], result["processed"]) == (0, 3, 3, 6)


def test_context_endpoint_returns_repository_degraded_result(monkeypatch):
    repository = load_repository(monkeypatch)
    fastapi = ModuleType("fastapi")
    class FastAPI:
        def __init__(self, **kwargs): pass
        def get(self, *args, **kwargs): return lambda function: function
    fastapi.FastAPI = FastAPI
    fastapi.Depends = lambda value: value
    fastapi.Header = lambda *args, **kwargs: None
    fastapi.Query = lambda value, **kwargs: value
    fastapi.HTTPException = type("HTTPException", (Exception,), {})
    provider = ModuleType("app.providers.bybit")
    provider.BybitClient = type("BybitClient", (), {})
    monkeypatch.setitem(sys.modules, "fastapi", fastapi)
    monkeypatch.setitem(sys.modules, "app.providers.bybit", provider)
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    cached = flow("PENGUUSDC", 301)
    main.repo = FakeRepository({"day_trade_flow:PENGUUSDC": cached})
    main.repo.get_day_trade_flow = lambda symbol: repository._get_day_trade_flow(main.repo, symbol)
    result = asyncio.run(main.day_trade_flow("PENGUUSDC"))
    assert result.data_quality == "DEGRADED" and result.coverage_status != "GOOD"


def test_version_endpoint_is_side_effect_free(monkeypatch):
    load_repository(monkeypatch)
    fastapi = ModuleType("fastapi")
    class FastAPI:
        def __init__(self, **kwargs): pass
        def get(self, *args, **kwargs): return lambda function: function
    fastapi.FastAPI = FastAPI
    fastapi.Depends = lambda value: value
    fastapi.Header = lambda *args, **kwargs: None
    fastapi.Query = lambda value, **kwargs: value
    fastapi.HTTPException = type("HTTPException", (Exception,), {})
    provider = ModuleType("app.providers.bybit")
    provider.BybitClient = type("BybitClient", (), {"server_time": lambda self: (_ for _ in ()).throw(AssertionError())})
    monkeypatch.setitem(sys.modules, "fastapi", fastapi)
    monkeypatch.setitem(sys.modules, "app.providers.bybit", provider)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "commit-123")
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    main.repo._connect = lambda: (_ for _ in ()).throw(AssertionError("DB access"))
    assert asyncio.run(main.version()) == {"commit_sha": "commit-123"}
