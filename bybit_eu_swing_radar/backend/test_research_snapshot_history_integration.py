from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = {'app/research_derivatives_positioning_api.py': ('derivatives-positioning', 'research_derivatives_positioning_snapshots'), 'app/research_btc_onchain_api.py': ('btc-onchain', 'research_btc_onchain_snapshots'), 'app/research_eth_onchain_api.py': ('eth-onchain', 'research_eth_onchain_snapshots'), 'app/research_btc_macro_cycle_etf_api.py': ('btc-macro-cycle-etf', 'research_btc_macro_cycle_etf_snapshots'), 'app/research_event_tokenomics_api.py': ('event-tokenomics', 'research_event_tokenomics_snapshots'), 'app/research_market_regime_api.py': ('market-regime', 'research_market_regime_snapshots'), 'app/research_liquidation_context_api.py': ('liquidation-context', 'research_liquidation_context_snapshots'), 'app/research_relative_strength_api.py': ('relative-strength', 'research_relative_strength_snapshots'), 'app/research_sector_rotation_api.py': ('sector-rotation', 'research_sector_rotation_snapshots'), 'app/research_geopolitical_risk_api.py': ('geopolitical-risk', 'research_geopolitical_risk_snapshots'), 'app/research_geopolitical_event_v2_api.py': ('geopolitical-event-v2', 'research_geopolitical_event_v2_snapshots'), 'app/research_cross_layer_context_api.py': ('cross-layer-context', 'research_cross_layer_context_snapshots'), 'app/research_cross_layer_context_v2_api.py': ('cross-layer-context-v2', 'research_cross_layer_context_snapshots')}


def test_mutable_research_collectors_preserve_append_only_raw_history():
    for relative, (family, table) in TARGETS.items():
        text = (ROOT / relative).read_text()
        assert "from research.research_snapshot_history import append_snapshot_history" in text
        assert f'research_family="{family}"' in text
        assert 'snapshot["immutable_history"] = history' in text
        assert text.index("append_snapshot_history(") < text.index(f"INSERT INTO {table} (")


def test_history_integration_keeps_existing_materializations():
    for relative, (_, table) in TARGETS.items():
        text = (ROOT / relative).read_text()
        table_pos = text.index(f"INSERT INTO {table} (")
        assert "ON CONFLICT" in text[table_pos:]
        assert "DO UPDATE" in text[table_pos:]
