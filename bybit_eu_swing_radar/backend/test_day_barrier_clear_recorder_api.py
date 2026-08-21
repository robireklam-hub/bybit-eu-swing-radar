from app.main import app as radar_app


def test_barrier_clear_rearm_status_route_is_registered_but_hidden_from_openapi():
    matches = [
        route
        for route in radar_app.routes
        if getattr(route, "path", None) == "/v1/day-trade/research/barrier-clear-rearm/status"
    ]
    assert len(matches) == 1
    assert matches[0].include_in_schema is False


def test_barrier_clear_rearm_route_does_not_add_trade_action_surface():
    openapi = radar_app.openapi()
    assert "/v1/day-trade/research/barrier-clear-rearm/status" not in openapi.get("paths", {})
