"""Application package bootstrap."""

import app.market_context_alerts as market_context_alerts
from app.market_context_compat import install_market_context_compatibility_bridge

# Install the old-Action compatibility mirror before route registration, then
# install the canonical context-only market enrichment. Both operate on copied
# HTTP responses only and never mutate scores, gates, cached records or execution.
install_market_context_compatibility_bridge(market_context_alerts)
market_context_alerts.install_market_context_route_enrichment()
