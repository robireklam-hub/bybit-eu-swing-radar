from datetime import datetime, timezone

from scripts.production_microstructure_data_access_smoke import (
    LIMIT,
    LOOKBACK_MINUTES,
    validate_bucket_payload,
)


def _payload(symbol="BTCUSDC"):
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "symbol": symbol,
        "bucket_start": now,
        "bucket_seconds": 5,
        "spread_bps": 0.2,
        "microprice": 120000.1,
        "imbalance_10": 0.05,
        "imbalance_50": 0.02,
        "signed_quote_flow": 100.0,
        "total_quote_volume": 1000.0,
        "bid_added_quote": 10.0,
        "bid_removed_quote": 5.0,
        "ask_added_quote": 5.0,
        "ask_removed_quote": 10.0,
        "book_ready": True,
    }
    summary = {
        "row_count": 1,
        "book_ready_ratio": 1.0,
        "trade_bucket_ratio": 1.0,
        "trade_count": 1,
        "book_message_count": 10,
        "total_quote_volume": 1000.0,
        "signed_quote_flow": 100.0,
        "mean_spread_bps": 0.2,
        "p95_spread_bps": 0.2,
        "mean_imbalance_10": 0.05,
        "mean_imbalance_50": 0.02,
        "mean_microprice_displacement_bps": 0.01,
        "mean_book_pressure_ratio": 0.2,
    }
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "outcome_fields_read": False,
        "promotion_allowed": False,
        "source_table": "microstructure_buckets",
        "symbol": symbol,
        "lookback_minutes": LOOKBACK_MINUTES,
        "limit": LIMIT,
        "row_count": 1,
        "first_bucket_at": now,
        "last_bucket_at": now,
        "summary": summary,
        "rows": [row],
    }


def test_data_access_smoke_contract_accepts_fresh_label_blind_rows():
    assert validate_bucket_payload(_payload(), "BTCUSDC") == (True, "ok")


def test_data_access_smoke_rejects_outcome_leakage():
    payload = _payload()
    payload["rows"][0]["net_r"] = 1.2
    assert validate_bucket_payload(payload, "BTCUSDC")[1] == "outcome_field_leakage"


def test_data_access_smoke_rejects_live_mutation_or_unbounded_contract():
    payload = _payload()
    payload["live_strategy_mutated"] = True
    assert validate_bucket_payload(payload, "BTCUSDC")[1] == "live_strategy_mutated_not_false"

    payload = _payload()
    payload["limit"] = LIMIT + 1
    assert validate_bucket_payload(payload, "BTCUSDC")[1] == "query_bounds_mismatch"
