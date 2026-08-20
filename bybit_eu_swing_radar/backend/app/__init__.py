"""Application package bootstrap."""

from app.market_context_alerts import install_market_context_route_enrichment

# Install before app.main declares the existing public Action routes. The
# enrichment is context-only and never mutates scores, gates or execution.
install_market_context_route_enrichment()
