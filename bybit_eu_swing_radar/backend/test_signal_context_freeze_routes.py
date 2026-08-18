from fastapi import FastAPI

from app.research_signal_context_freeze_api import attach_signal_context_freeze_research


def test_signal_context_freeze_routes_are_registered_and_hidden() -> None:
    app = FastAPI()

    def require_key() -> None:
        return None

    attach_signal_context_freeze_research(app, require_key)
    routes = {route.path: route for route in app.routes}
    expected = {
        "/v1/research/signal-context-freeze/spec",
        "/v1/research/signal-context-freeze/capture",
        "/v1/research/signal-context-freeze/status",
    }
    assert expected.issubset(routes)
    for path in expected:
        assert routes[path].include_in_schema is False
