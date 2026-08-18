from app.research_signal_context_freeze_api import JOURNAL_SIGNAL_SQL, _recorder_symbols


FORBIDDEN = (
    "net_r",
    "gross_r",
    "exit_reason",
    "closed_at",
    "mfe_r",
    "mae_r",
    "tp1_hit_at",
    "tp2_hit_at",
    "tp3_hit_at",
    "stop_hit_at",
)


def test_journal_query_is_label_blind() -> None:
    lowered = JOURNAL_SIGNAL_SQL.lower()
    for field in FORBIDDEN:
        assert field not in lowered
    assert "j.opened_at" in lowered
    assert "j.signal_key" in lowered
    assert "j.setup_type" in lowered


def test_default_recorder_symbols_are_usdc_and_bounded(monkeypatch) -> None:
    monkeypatch.delenv("MICROSTRUCTURE_SYMBOLS", raising=False)
    symbols = _recorder_symbols()
    assert symbols == ("BTCUSDC", "ETHUSDC", "SOLUSDC")
    assert len(symbols) <= 12
    assert all(symbol.endswith("USDC") for symbol in symbols)


def test_recorder_symbol_override_drops_non_usdc_and_caps(monkeypatch) -> None:
    raw = ",".join([f"C{i}USDC" for i in range(15)] + ["BTCUSDT"])
    monkeypatch.setenv("MICROSTRUCTURE_SYMBOLS", raw)
    symbols = _recorder_symbols()
    assert len(symbols) == 12
    assert "BTCUSDT" not in symbols
