from pathlib import Path

path = Path("bybit_eu_swing_radar/backend/worker.py")
text = path.read_text()


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match, found {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "import asyncpg\nimport httpx\n",
    "import asyncpg\nimport httpx\n\nfrom app.swing_priority import select_compact_priority_sections\n",
)

replace_once(
    "\n\nasync def enrich_coinalyze(\n",
    '''\n\ndef select_coinalyze_targets(\n    analyses: list[Analysis],\n    priority_symbols: list[str] | None = None,\n) -> list[Analysis]:\n    """Select the rate-budget target set with compact top/watch candidates first."""\n    priority_symbols = list(dict.fromkeys(priority_symbols or []))\n    if len(priority_symbols) > COINALYZE_ENRICH_LIMIT:\n        raise RuntimeError(\n            "Coinalyze compact priority set exceeds safe rate budget: "\n            f"{len(priority_symbols)}>{COINALYZE_ENRICH_LIMIT}; "\n            "refusing to publish silently incomplete top/watch coverage"\n        )\n\n    by_symbol = {item.instrument.symbol: item for item in analyses}\n    missing_priority = [symbol for symbol in priority_symbols if symbol not in by_symbol]\n    if missing_priority:\n        raise RuntimeError(\n            "Compact priority symbols missing from swing analyses: "\n            + ", ".join(missing_priority)\n        )\n\n    prioritized = [by_symbol[symbol] for symbol in priority_symbols]\n    priority_set = set(priority_symbols)\n    secondary = sorted(\n        [item for item in analyses if item.instrument.symbol not in priority_set],\n        key=lambda item: (\n            setup_score(\n                item.expansion_score,\n                abs(item.direction_score),\n                item.quality_score,\n            )\n            + (5.0 if item.instrument.symbol in DISCOVERY_SYMBOLS else 0.0)\n        ),\n        reverse=True,\n    )\n    remaining = COINALYZE_ENRICH_LIMIT - len(prioritized)\n    return prioritized + secondary[:remaining]\n\n\nasync def enrich_coinalyze(\n''',
)

replace_once(
    '''    partial_safe: bool = False,\n) -> tuple[bool, str | None]:\n''',
    '''    partial_safe: bool = False,\n    target_analyses: list[Analysis] | None = None,\n) -> tuple[bool, str | None]:\n''',
)

replace_once(
    '''    selected_analyses = sorted(\n        analyses,\n        key=lambda item: (\n            setup_score(\n                item.expansion_score,\n                abs(item.direction_score),\n                item.quality_score,\n            )\n            + (5.0 if item.instrument.symbol in DISCOVERY_SYMBOLS else 0.0)\n        ),\n        reverse=True,\n    )[:COINALYZE_ENRICH_LIMIT]\n''',
    '''    selected_analyses = (\n        list(target_analyses)\n        if target_analyses is not None\n        else select_coinalyze_targets(analyses)\n    )\n    if len(selected_analyses) > COINALYZE_ENRICH_LIMIT:\n        raise RuntimeError(\n            "Coinalyze target set exceeds safe rate budget: "\n            f"{len(selected_analyses)}>{COINALYZE_ENRICH_LIMIT}"\n        )\n''',
)

old_run = '''        coinalyze_ok, coinalyze_error = await enrich_coinalyze(\n            analyses,\n            coinalyze,\n            mutate_scores=False,\n            partial_safe=True,\n        )\n        borrow_ok, borrow_error = await apply_shortability(analyses, bybit)\n        now = datetime.now(timezone.utc)\n\n        long_setups = [setup for analysis in analyses if (setup := build_setup(analysis, "long", now)) is not None]\n'''
new_run = '''        # Shortability is execution semantics and must be known before deciding\n        # which compact short/watch candidates receive the limited Coinalyze budget.\n        borrow_ok, borrow_error = await apply_shortability(analyses, bybit)\n        priority_now = datetime.now(timezone.utc)\n        priority_longs = [\n            setup for analysis in analyses\n            if (setup := build_setup(analysis, "long", priority_now)) is not None\n        ]\n        priority_shorts = [\n            setup for analysis in analyses\n            if (setup := build_setup(analysis, "short", priority_now)) is not None\n        ]\n        priority_longs.sort(key=lambda item: item["setup_score"], reverse=True)\n        priority_shorts.sort(key=lambda item: item["setup_score"], reverse=True)\n        priority_executable_symbols = {\n            item["symbol"] for item in priority_longs + priority_shorts\n        }\n        priority_all_watch = rank_watchlist(\n            analyses, priority_now, priority_executable_symbols\n        )\n        priority_watch = priority_all_watch[:20]\n        priority_liquidity_blocked = [\n            item for item in priority_all_watch\n            if item.get("metrics", {}).get("execution_status") == "LIQUIDITY_BLOCKED"\n        ]\n        compact_priority = select_compact_priority_sections(\n            priority_longs[:10],\n            priority_shorts[:10],\n            priority_watch,\n            priority_liquidity_blocked,\n            limit=3,\n        )\n        coinalyze_priority_symbols = compact_priority["priority_symbols"]\n        coinalyze_targets = select_coinalyze_targets(\n            analyses, coinalyze_priority_symbols\n        )\n        coinalyze_target_symbols = [\n            item.instrument.symbol for item in coinalyze_targets\n        ]\n        coinalyze_ok, coinalyze_error = await enrich_coinalyze(\n            analyses,\n            coinalyze,\n            mutate_scores=False,\n            partial_safe=True,\n            target_analyses=coinalyze_targets,\n        )\n        now = datetime.now(timezone.utc)\n\n        long_setups = [setup for analysis in analyses if (setup := build_setup(analysis, "long", now)) is not None]\n'''
replace_once(old_run, new_run)

replace_once(
    '''        regime = build_market_regime(analyses, now, coinalyze_ok, borrow_ok)\n\n        enriched_count = sum(1 for item in analyses if item.derivatives)\n        coinalyze_target_count = min(\n            len(analyses), COINALYZE_ENRICH_LIMIT\n        )\n''',
    '''        enriched_count = sum(1 for item in analyses if item.derivatives)\n        coinalyze_target_count = len(coinalyze_targets)\n        coinalyze_priority_targeted_symbols = [\n            symbol for symbol in coinalyze_priority_symbols\n            if symbol in set(coinalyze_target_symbols)\n        ]\n        coinalyze_priority_enriched_symbols = [\n            symbol for symbol in coinalyze_priority_symbols\n            if any(\n                analysis.instrument.symbol == symbol and bool(analysis.derivatives)\n                for analysis in analyses\n            )\n        ]\n        coinalyze_priority_missing_symbols = [\n            symbol for symbol in coinalyze_priority_symbols\n            if symbol not in set(coinalyze_priority_enriched_symbols)\n        ]\n        regime = build_market_regime(analyses, now, coinalyze_ok, borrow_ok)\n        regime["notes"].append(\n            "Coinalyze compact top/watch priority coverage: "\n            f"targeted={len(coinalyze_priority_targeted_symbols)}/"\n            f"{len(coinalyze_priority_symbols)}, "\n            f"enriched={len(coinalyze_priority_enriched_symbols)}/"\n            f"{len(coinalyze_priority_symbols)}, "\n            f"missing={coinalyze_priority_missing_symbols}."\n        )\n''',
)

replace_once(
    '''            "coinalyze_enrichment_limit": COINALYZE_ENRICH_LIMIT,\n            "borrowability_checked_symbols": len(analyses) if borrow_ok else 0,\n''',
    '''            "coinalyze_enrichment_limit": COINALYZE_ENRICH_LIMIT,\n            "coinalyze_targeted_symbol_list": coinalyze_target_symbols,\n            "coinalyze_priority_symbols": coinalyze_priority_symbols,\n            "coinalyze_priority_targeted_symbols": coinalyze_priority_targeted_symbols,\n            "coinalyze_priority_enriched_symbols": coinalyze_priority_enriched_symbols,\n            "coinalyze_priority_missing_symbols": coinalyze_priority_missing_symbols,\n            "coinalyze_priority_full_target_coverage": (\n                set(coinalyze_priority_targeted_symbols) == set(coinalyze_priority_symbols)\n            ),\n            "coinalyze_priority_full_enrichment": (\n                set(coinalyze_priority_enriched_symbols) == set(coinalyze_priority_symbols)\n            ),\n            "borrowability_checked_symbols": len(analyses) if borrow_ok else 0,\n''',
)

replace_once(
    '''                    "coverage": (\n                        f"{enriched_count}/{coinalyze_target_count} targeted "\n                        f"({len(analyses)} analyzed total)"\n                    ),\n                    "missing_fields": [] if coinalyze_ok else [coinalyze_error or "enrichment unavailable"],\n''',
    '''                    "coverage": (\n                        f"rate-budget targets enriched {enriched_count}/{coinalyze_target_count}; "\n                        f"compact priority targeted {len(coinalyze_priority_targeted_symbols)}/"\n                        f"{len(coinalyze_priority_symbols)}, enriched "\n                        f"{len(coinalyze_priority_enriched_symbols)}/"\n                        f"{len(coinalyze_priority_symbols)}; "\n                        f"{len(analyses)} analyzed total"\n                    ),\n                    "priority_targeted_symbols": coinalyze_priority_targeted_symbols,\n                    "priority_enriched_symbols": coinalyze_priority_enriched_symbols,\n                    "priority_missing_symbols": coinalyze_priority_missing_symbols,\n                    "missing_fields": [] if coinalyze_ok else [coinalyze_error or "enrichment unavailable"],\n''',
)

replace_once(
    '''            f"coinalyze={enriched_count}/{coinalyze_target_count} targeted, "\n''',
    '''            f"coinalyze={enriched_count}/{coinalyze_target_count} rate-budget targets, "\n            f"priority={len(coinalyze_priority_enriched_symbols)}/"\n            f"{len(coinalyze_priority_symbols)} enriched, "\n''',
)

path.write_text(text)
print("worker.py semantic Coinalyze priority patch applied")
