import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RADAR_API_KEY", "test-radar-key")
os.environ.setdefault("COINALYZE_API_KEY", "test-coinalyze-key")

from app.main import app


def test_standalone_prospective_funnel_status_route_is_attached():
    paths = {route.path for route in app.routes}
    assert "/v1/day-trade/research/prospective-funnel/status" in paths
