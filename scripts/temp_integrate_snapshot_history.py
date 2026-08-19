from pathlib import Path

root = Path('bybit_eu_swing_radar/backend')
targets = {
    'app/research_derivatives_positioning_api.py': ('derivatives-positioning', 'research_derivatives_positioning_snapshots', 'captured_hour', 'snapshot.get("source_commit_sha")'),
    'app/research_btc_onchain_api.py': ('btc-onchain', 'research_btc_onchain_snapshots', 'captured_hour', 'snapshot.get("source_commit_sha")'),
    'app/research_eth_onchain_api.py': ('eth-onchain', 'research_eth_onchain_snapshots', 'captured_hour', 'snapshot.get("source_commit_sha")'),
    'app/research_btc_macro_cycle_etf_api.py': ('btc-macro-cycle-etf', 'research_btc_macro_cycle_etf_snapshots', 'captured_hour', 'snapshot.get("source_commit_sha")'),
    'app/research_event_tokenomics_api.py': ('event-tokenomics', 'research_event_tokenomics_snapshots', 'captured_hour', 'snapshot.get("source_commit_sha")'),
    'app/research_market_regime_api.py': ('market-regime', 'research_market_regime_snapshots', 'captured_hour', 'source_sha'),
    'app/research_liquidation_context_api.py': ('liquidation-context', 'research_liquidation_context_snapshots', 'captured_hour', 'snapshot.get("source_commit_sha")'),
    'app/research_relative_strength_api.py': ('relative-strength', 'research_relative_strength_snapshots', 'captured_day', 'source_sha'),
    'app/research_sector_rotation_api.py': ('sector-rotation', 'research_sector_rotation_snapshots', 'captured_day', 'snapshot.get("source_commit_sha")'),
    'app/research_geopolitical_risk_api.py': ('geopolitical-risk', 'research_geopolitical_risk_snapshots', 'captured_hour', 'snapshot.get("source_commit_sha")'),
    'app/research_geopolitical_event_v2_api.py': ('geopolitical-event-v2', 'research_geopolitical_event_v2_snapshots', 'source_timestamp', 'snapshot.get("source_commit_sha")'),
    'app/research_cross_layer_context_api.py': ('cross-layer-context', 'research_cross_layer_context_snapshots', 'captured_hour', 'source_sha'),
    'app/research_cross_layer_context_v2_api.py': ('cross-layer-context-v2', 'research_cross_layer_context_snapshots', 'captured_hour', 'source_sha'),
}

for relative, (family, table, bucket_expr, source_expr) in targets.items():
    path = root / relative
    text = path.read_text()
    import_line = 'from research.research_snapshot_history import append_snapshot_history\n'
    if import_line not in text:
        anchor = 'import asyncpg\n'
        if anchor not in text:
            raise SystemExit(f'missing import anchor: {relative}')
        text = text.replace(anchor, anchor + import_line, 1)

    if f'research_family="{family}"' not in text:
        table_anchor = f'INSERT INTO {table} ('
        table_pos = text.find(table_anchor)
        if table_pos < 0:
            raise SystemExit(f'missing materialization table anchor: {relative} {table}')
        exec_pos = text.rfind('await connection.execute(', 0, table_pos)
        if exec_pos < 0:
            raise SystemExit(f'missing execute anchor: {relative}')
        line_start = text.rfind('\n', 0, exec_pos) + 1
        indent = text[line_start:exec_pos]
        if indent not in {'        ', '            '}:
            raise SystemExit(f'unexpected execute indent {indent!r}: {relative}')
        insertion = '\n'.join([
            f'{indent}history = await append_snapshot_history(',
            f'{indent}    connection,',
            f'{indent}    research_family="{family}",',
            f'{indent}    spec_version=SPEC_VERSION,',
            f'{indent}    captured_at=captured_at,',
            f'{indent}    capture_bucket={bucket_expr},',
            f'{indent}    source_commit_sha={source_expr},',
            f'{indent}    snapshot=snapshot,',
            f'{indent})',
            f'{indent}snapshot["immutable_history"] = history',
            '',
        ])
        text = text[:line_start] + insertion + text[line_start:]
    path.write_text(text)

(root / 'test_research_snapshot_history_integration.py').write_text('''from pathlib import Path\n\nROOT = Path(__file__).resolve().parent\nTARGETS = {\n    "app/research_derivatives_positioning_api.py": "derivatives-positioning",\n    "app/research_btc_onchain_api.py": "btc-onchain",\n    "app/research_eth_onchain_api.py": "eth-onchain",\n    "app/research_btc_macro_cycle_etf_api.py": "btc-macro-cycle-etf",\n    "app/research_event_tokenomics_api.py": "event-tokenomics",\n    "app/research_market_regime_api.py": "market-regime",\n    "app/research_liquidation_context_api.py": "liquidation-context",\n    "app/research_relative_strength_api.py": "relative-strength",\n    "app/research_sector_rotation_api.py": "sector-rotation",\n    "app/research_geopolitical_risk_api.py": "geopolitical-risk",\n    "app/research_geopolitical_event_v2_api.py": "geopolitical-event-v2",\n    "app/research_cross_layer_context_api.py": "cross-layer-context",\n    "app/research_cross_layer_context_v2_api.py": "cross-layer-context-v2",\n}\n\ndef test_mutable_research_collectors_preserve_append_only_raw_history():\n    for relative, family in TARGETS.items():\n        text = (ROOT / relative).read_text()\n        assert "from research.research_snapshot_history import append_snapshot_history" in text\n        assert f\'research_family="{family}"\' in text\n        assert 'snapshot["immutable_history"] = history' in text\n        assert text.index("append_snapshot_history(") < text.index("ON CONFLICT")\n\ndef test_history_integration_keeps_existing_materializations():\n    for relative in TARGETS:\n        text = (ROOT / relative).read_text()\n        assert "ON CONFLICT" in text\n        assert "DO UPDATE" in text\n''')
