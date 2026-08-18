from app.research_btc_macro_cycle_etf_api import (
    parse_farside_html,
    parse_fed_board_table,
    parse_fred_csv,
)


def test_parse_fred_csv_accepts_fredgraph_shape() -> None:
    text = "observation_date,DGS10\n2026-08-14,4.31\n2026-08-17,.\n2026-08-18,4.28\n"
    rows = parse_fred_csv(text, "DGS10")
    assert rows == [("2026-08-14", 4.31), ("2026-08-18", 4.28)]


def test_parse_farside_html_preserves_negative_parentheses() -> None:
    html = """
    <table>
      <tr><th>Date</th><th>IBIT</th><th>FBTC</th><th>Total</th></tr>
      <tr><td>14 Aug 2026</td><td>(10.5)</td><td>20.0</td><td>9.5</td></tr>
      <tr><td>17 Aug 2026</td><td>100.0</td><td>-</td><td>100.0</td></tr>
      <tr><td>Total</td><td>89.5</td><td>20.0</td><td>109.5</td></tr>
    </table>
    """
    rows = parse_farside_html(html)
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-08-14"
    assert rows[0]["funds"]["IBIT"] == -10_500_000.0
    assert rows[0]["total_usd"] == 9_500_000.0
    assert rows[1]["funds"]["FBTC"] is None


def test_parse_fed_board_daily_csv_table() -> None:
    text = (
        "Series,Series Description,2026-07-29,2026-07-30\n"
        "RIFLGFCY10_N.B,10-year Treasury,4.67,4.68\n"
    )
    rows = parse_fed_board_table(text, "RIFLGFCY10_N.B")
    assert rows == [("2026-07-29", 4.67), ("2026-07-30", 4.68)]


def test_parse_fed_board_monthly_html_table() -> None:
    html = """
    <table>
      <tr><th>Series</th><th>Series Description</th><th>2026-05</th><th>2026-06</th></tr>
      <tr><td>JRXWTFB_N.M</td><td>Nominal Broad Dollar Index</td><td>118.7792</td><td>120.0835</td></tr>
    </table>
    """
    rows = parse_fed_board_table(html, "JRXWTFB_N.M")
    assert rows == [("2026-05", 118.7792), ("2026-06", 120.0835)]
