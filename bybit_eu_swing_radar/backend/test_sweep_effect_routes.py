from fastapi import FastAPI

from app.research_sweep_effect_api import attach_sweep_effect_research


def test_sweep_effect_routes_are_registered_and_hidden() -> None:
    app = FastAPI()

    def require_key() -> None:
        return None

    attach_sweep_effect_research(app, require_key)
    routes = {route.path: route for route in app.routes}
    assert "/v1/research/sweep-effect/spec" in routes
    assert "/v1/research/sweep-effect/status" in routes
    assert routes["/v1/research/sweep-effect/spec"].include_in_schema is False
    assert routes["/v1/research/sweep-effect/status"].include_in_schema is False
