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

integration_targets = {
    relative: (family, table)
    for relative, (family, table, _, _) in targets.items()
}
(root / 'test_research_snapshot_history_integration.py').write_text(
    'from pathlib import Path\n\nROOT = Path(__file__).resolve().parent\nTARGETS = ' + repr(integration_targets) + '''\n\n
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
'''
)
