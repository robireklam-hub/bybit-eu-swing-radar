from fastapi import FastAPI

from app.research_cross_layer_context_api import attach_cross_layer_context_research


def test_cross_layer_routes_are_registered_and_hidden() -> None:
    app = FastAPI()

    def require_key() -> None:
        return None

    attach_cross_layer_context_research(app, require_key)
    routes = {route.path: route for route in app.routes}
    expected = {
        "/v1/research/cross-layer-context/spec",
        "/v1/research/cross-layer-context/capture",
        "/v1/research/cross-layer-context/status",
    }
    assert expected.issubset(routes)
    for path in expected:
        assert routes[path].include_in_schema is False
