from app.main import app


def test_standalone_prospective_funnel_status_route_is_attached():
    paths = {route.path for route in app.routes}
    assert "/v1/day-trade/research/prospective-funnel/status" in paths
