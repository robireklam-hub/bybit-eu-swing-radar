from fastapi import FastAPI

from app.research_derivatives_positioning_api import attach_derivatives_positioning_research


def test_liquidation_context_routes_are_registered_and_hidden() -> None:
    app = FastAPI()

    def require_key() -> None:
        return None

    attach_derivatives_positioning_research(app, require_key)
    routes = {route.path: route for route in app.routes}
    expected = {
        "/v1/research/liquidation-context/spec",
        "/v1/research/liquidation-context/capture",
        "/v1/research/liquidation-context/status",
    }
    assert expected.issubset(routes)
    for path in expected:
        assert routes[path].include_in_schema is False
