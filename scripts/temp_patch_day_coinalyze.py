from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "bybit_eu_swing_radar" / "backend"
worker = ROOT / "worker.py"
src = worker.read_text()
pattern = re.compile(
    r'    exchange_names: dict\[str, str\] = \{\}\n'
    r'    exchange_metadata_error: str \| None = None\n'
    r'    quote_order = \("USDC", "USDT", "USD"\)\n'
    r'    if partial_safe:\n.*?'
    r'(?=    market_map = select_coinalyze_markets\()',
    re.S,
)
replacement = '''    exchange_names: dict[str, str] = {}\n    exchange_metadata_error: str | None = None\n    quote_order = ("USDC", "USDT", "USD")\n    # Coinalyze future-markets exposes an exchange code, not a venue name.\n    # Resolve that code for both engines before venue ranking. Day keeps its\n    # historical USDC-first quote preference and score-enrichment semantics;\n    # swing keeps USDT-first context selection and partial-safe endpoints.\n    try:\n        exchange_rows = await api.exchanges()\n        exchange_names = {\n            str(row.get("code", "")).upper(): str(row.get("name", ""))\n            for row in exchange_rows\n            if isinstance(row, dict) and row.get("code") and row.get("name")\n        }\n    except Exception as exc:\n        exchange_metadata_error = f"exchanges: {type(exc).__name__}: {exc}"\n        if not partial_safe:\n            for item in selected_analyses:\n                item.missing_data.append(\n                    "Coinalyze exchange metadata unavailable; derivatives enrichment skipped"\n                )\n            return False, exchange_metadata_error\n\n    if partial_safe:\n        quote_order = ("USDT", "USDC", "USD")\n\n'''
src2, count = pattern.subn(replacement, src, count=1)
if count != 1:
    raise SystemExit(f"worker exchange-metadata anchor count={count}")
worker.write_text(src2)

day = ROOT / "day_worker.py"
src = day.read_text()

def replace_once(old: str, new: str, label: str) -> None:
    global src
    count = src.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    src = src.replace(old, new, 1)

replace_once(
    '    enrich_coinalyze,\n',
    '    coinalyze_payload_complete,\n    enrich_coinalyze,\n',
    'day import',
)
replace_once(
'''def build_day_regime(\n    analyses: list[DayAnalysis],\n    now: datetime,\n    coinalyze_request_ok: bool,\n    coinalyze_enriched_symbols: int,\n    borrowability_ok: bool,\n) -> dict[str, Any]:\n    btc = next(\n''',
'''def build_day_regime(\n    analyses: list[DayAnalysis],\n    now: datetime,\n    coinalyze_request_ok: bool,\n    coinalyze_enriched_symbols: int,\n    borrowability_ok: bool,\n    coinalyze_complete_symbols: int | None = None,\n) -> dict[str, Any]:\n    complete_symbols = (\n        coinalyze_enriched_symbols\n        if coinalyze_complete_symbols is None\n        else coinalyze_complete_symbols\n    )\n    btc = next(\n''',
    'day regime signature',
)
replace_once(
'''    if len(analyses) > 0 and coinalyze_enriched_symbols == len(analyses):\n        coinalyze_quality = "GOOD"\n    elif coinalyze_enriched_symbols > 0:\n''',
'''    if (\n        len(analyses) > 0\n        and coinalyze_request_ok\n        and complete_symbols == len(analyses)\n    ):\n        coinalyze_quality = "GOOD"\n    elif coinalyze_enriched_symbols > 0:\n''',
    'day quality',
)
replace_once(
'''        coinalyze_enriched_count = sum(\n            1 for item in analyses if item.derivatives\n        )\n        regime = build_day_regime(\n            analyses,\n            now,\n            coinalyze_ok,\n            coinalyze_enriched_count,\n            borrow_ok,\n        )\n''',
'''        coinalyze_enriched_count = sum(\n            1 for item in analyses if item.derivatives\n        )\n        coinalyze_complete_count = sum(\n            1 for item in analyses if coinalyze_payload_complete(item.derivatives)\n        )\n        regime = build_day_regime(\n            analyses,\n            now,\n            coinalyze_ok,\n            coinalyze_enriched_count,\n            borrow_ok,\n            coinalyze_complete_symbols=coinalyze_complete_count,\n        )\n''',
    'day counter',
)
replace_once(
'''            "coinalyze_enriched_symbols": coinalyze_enriched_count,\n            "borrowability_checked_symbols": len(analyses) if borrow_ok else 0,\n''',
'''            "coinalyze_enriched_symbols": coinalyze_enriched_count,\n            "coinalyze_complete_symbols": coinalyze_complete_count,\n            "borrowability_checked_symbols": len(analyses) if borrow_ok else 0,\n''',
    'day coverage',
)
replace_once(
'''                    "status": (\n                        "ok"\n                        if coinalyze_enriched_count == len(analyses) and len(analyses) > 0\n                        else "partial"\n                        if coinalyze_enriched_count > 0\n                        else "degraded"\n                    ),\n                    "data_as_of": (\n                        now.isoformat() if coinalyze_enriched_count > 0 else None\n                    ),\n                    "coverage": f"{coinalyze_enriched_count}/{len(analyses)}",\n                    "missing_fields": (\n                        []\n                        if coinalyze_enriched_count == len(analyses)\n                        else [\n                            coinalyze_error\n                            or "Derivatives enrichment is only partially available"\n                        ]\n                    ),\n''',
'''                    "status": (\n                        "ok"\n                        if (\n                            coinalyze_ok\n                            and coinalyze_complete_count == len(analyses)\n                            and len(analyses) > 0\n                        )\n                        else "partial"\n                        if coinalyze_enriched_count > 0\n                        else "degraded"\n                    ),\n                    "data_as_of": (\n                        now.isoformat() if coinalyze_enriched_count > 0 else None\n                    ),\n                    "coverage": f"{coinalyze_complete_count}/{len(analyses)}",\n                    "any_field_coverage": f"{coinalyze_enriched_count}/{len(analyses)}",\n                    "complete_coverage": f"{coinalyze_complete_count}/{len(analyses)}",\n                    "missing_fields": (\n                        []\n                        if (\n                            coinalyze_ok\n                            and coinalyze_complete_count == len(analyses)\n                            and len(analyses) > 0\n                        )\n                        else [\n                            coinalyze_error\n                            or "Derivatives enrichment is only partially available"\n                        ]\n                    ),\n''',
    'day status',
)
day.write_text(src)
