import app.main


def test_historical_flow_v2_routes_are_attached_without_live_route_replacement():
    routes = {route.path for route in app.main.app.router.routes}
    assert "/v1/day-trade/research/flow/v2/run" in routes
    assert "/v1/day-trade/research/flow/v2/status" in routes
    assert "/v1/day-trade/research/flow/v2/report" in routes
    assert "/v1/day-trade/top-candidates" in routes
    assert "/v1/day-trade/flow/status" in routes
