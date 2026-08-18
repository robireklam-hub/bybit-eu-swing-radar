import scripts.production_sweep_effect_shadow_smoke as smoke


def _spec() -> dict:
    return {
        "spec_version": "sweep-forward-effect-v1",
        "research_only": True,
        "live_strategy_mutated": False,
        "label_gate_before_outcomes": True,
        "promotion_allowed": False,
        "sample_gate": {"minimum_closed_signals": 60},
    }


def _waiting_status() -> dict:
    return {
        "status": "WAITING_FOR_FORWARD_SAMPLE",
        "source_commit_sha": "abc",
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "outcomes_loaded": False,
        "effects": None,
        "sample": {
            "gate": {
                "ready": False,
                "closed_signal_count": 12,
                "long_count": 6,
                "short_count": 6,
                "distinct_utc_days": 4,
                "attribute_coverage_pct": 100.0,
            }
        },
    }


def test_smoke_accepts_fail_closed_waiting_state(monkeypatch, capsys) -> None:
    monkeypatch.setattr(smoke, "KEY", "secret")
    monkeypatch.setattr(smoke, "EXPECTED_SHA", "abc")
    monkeypatch.setattr(smoke, "wait_for_exact_sha", lambda: None)
    monkeypatch.setattr(
        smoke,
        "request_json",
        lambda path: _spec() if path.endswith("/spec") else _waiting_status(),
    )
    smoke.main()
    output = capsys.readouterr().out
    assert "SWEEP FORWARD EFFECT V1 VERIFIED." in output
    assert '"outcomes_loaded": false' in output


def test_smoke_rejects_outcomes_below_gate(monkeypatch) -> None:
    payload = _waiting_status()
    payload["outcomes_loaded"] = True
    monkeypatch.setattr(smoke, "KEY", "secret")
    monkeypatch.setattr(smoke, "EXPECTED_SHA", "abc")
    monkeypatch.setattr(smoke, "wait_for_exact_sha", lambda: None)
    monkeypatch.setattr(
        smoke,
        "request_json",
        lambda path: _spec() if path.endswith("/spec") else payload,
    )
    try:
        smoke.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected fail-closed smoke failure")
