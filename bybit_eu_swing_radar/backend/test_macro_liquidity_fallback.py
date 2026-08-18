from research.macro_liquidity_fallback import parse_h41_series, parse_nyfed_rrp


def test_parse_h41_walcl_series() -> None:
    text = (
        "Series,Series Description,2026-07-22,2026-07-29\n"
        "RESPPMA_N.WW,Total assets less eliminations,6580000,6595000\n"
    )
    assert parse_h41_series(text, "RESPPMA_N.WW") == [
        ("2026-07-22", 6580000.0),
        ("2026-07-29", 6595000.0),
    ]


def test_parse_nyfed_rrp_aggregates_overnight_operations_by_day() -> None:
    payload = {
        "repo": {
            "operations": [
                {
                    "operationDate": "2026-08-14",
                    "term": "Overnight",
                    "termCalendarDays": 1,
                    "totalAmtAccepted": "1.250",
                },
                {
                    "operationDate": "2026-08-14",
                    "term": "Overnight",
                    "termCalendarDays": "1",
                    "totalAmtAccepted": "0.750",
                },
                {
                    "operationDate": "2026-08-14",
                    "term": "7 Day",
                    "termCalendarDays": 7,
                    "totalAmtAccepted": "100.000",
                },
                {
                    "operationDate": "2026-08-17",
                    "term": "Overnight",
                    "totalAmtAccepted": 2.125,
                },
            ]
        }
    }
    assert parse_nyfed_rrp(payload) == [
        ("2026-08-14", 2.0),
        ("2026-08-17", 2.125),
    ]
