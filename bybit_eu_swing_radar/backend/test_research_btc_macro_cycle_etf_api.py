from app.research_btc_macro_cycle_etf_api import parse_farside_html, parse_fred_csv


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
