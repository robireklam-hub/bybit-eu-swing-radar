from app.research_event_tokenomics_api import (
    fomc_schedule_events,
    normalize_bybit_announcements,
    normalize_coinmarketcal,
    normalize_tokenomist_unlocks,
    parse_bls_ics,
)


def test_bls_ics_parses_high_value_macro_events() -> None:
    text = """BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:cpi-1\nDTSTART;TZID=America/New_York:20260911T083000\nSUMMARY:Consumer Price Index for August 2026\nEND:VEVENT\nBEGIN:VEVENT\nUID:noise-1\nDTSTART:20260912T120000Z\nSUMMARY:Low signal release\nEND:VEVENT\nEND:VCALENDAR\n"""
    events = parse_bls_ics(text)
    assert len(events) == 1
    assert events[0]["event_type"] == "MACRO_CPI"
    assert events[0]["event_at"] == "2026-09-11T12:30:00+00:00"


def test_bybit_announcements_are_context_only_and_symbol_filtered() -> None:
    payload = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "title": "Bybit Will Delist CRV Spot Pair",
                    "description": "CRV update",
                    "type": {"key": "delistings"},
                    "tags": ["Spot", "Delistings"],
                    "url": "https://ann.example/crv",
                    "publishTime": 1787030000000,
                },
                {
                    "title": "Unrelated listing",
                    "description": "ZZZ only",
                    "type": {"key": "new_crypto"},
                    "tags": ["Spot Listings"],
                    "url": "https://ann.example/zzz",
                    "publishTime": 1787030000000,
                },
            ]
        },
    }
    events = normalize_bybit_announcements(payload, ["BTCUSDC", "CRVUSDC"])
    assert len(events) == 1
    assert events[0]["event_type"] == "EXCHANGE_DELISTING"
    assert events[0]["symbols"] == ["CRVUSDC"]
    assert events[0]["source"]["is_bybit_eu_specific"] is False


def test_coinmarketcal_preserves_estimated_date_semantics() -> None:
    payload = {
        "data": [
            {
                "id": "1",
                "title": "ETH roadmap release",
                "date": "2026-09-01T00:00:00Z",
                "displayedDate": "By 01 Sep",
                "dateType": "deadline",
                "isEstimated": True,
                "impact": 8.5,
                "categories": ["Release"],
                "coins": [{"symbol": "eth"}],
            }
        ]
    }
    events = normalize_coinmarketcal(payload, ["ETHUSDC"])
    assert events[0]["is_estimated"] is True
    assert events[0]["display_date"] == "By 01 Sep"
    assert events[0]["severity"] == "HIGH"


def test_tokenomist_unlock_size_is_normalized() -> None:
    payload = {
        "data": [
            {
                "tokenId": "hyperliquid",
                "tokenSymbol": "HYPE",
                "releasedPercentage": 40.0,
                "dataSource": "Whitepaper",
                "upcomingEvent": {
                    "unlockDate": "2026-08-25T12:00:00Z",
                    "cliffUnlocks": {
                        "totalCliffAmount": 100.0,
                        "totalCliffValue": 5_000_000.0,
                        "valueToMarketCap": 2.5,
                    },
                },
            }
        ]
    }
    events = normalize_tokenomist_unlocks(payload, ["HYPEUSDC"])
    assert len(events) == 1
    assert events[0]["event_type"] == "TOKEN_UNLOCK"
    assert events[0]["severity"] == "HIGH"
    assert events[0]["tokenomics"]["value_to_market_cap_pct"] == 2.5


def test_fomc_schedule_has_upcoming_2026_decisions() -> None:
    events = fomc_schedule_events()
    ids = {event["event_id"] for event in events}
    assert "fed:MACRO_FOMC_DECISION:2026-09-16" in ids
    assert all(event["source"]["official"] is True for event in events)
