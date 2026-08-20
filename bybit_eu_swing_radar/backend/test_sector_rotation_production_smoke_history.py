from scripts.production_sector_rotation_smoke import _immutable_history_error


def _capture() -> dict:
    captured_at = "2026-08-20T14:10:00+00:00"
    return {
        "captured_at": captured_at,
        "immutable_history": {
            "immutable": True,
            "purpose": "append_only_raw_history",
            "research_family": "sector-rotation",
            "spec_version": "sector-rotation-shadow-v1",
            "captured_at": captured_at,
            "payload_fingerprint": "a" * 64,
            "history_count": 7,
            "bucket_history_count": 2,
        },
    }


def test_sector_rotation_history_guard_accepts_valid_append_only_evidence() -> None:
    assert _immutable_history_error(_capture()) is None


def test_sector_rotation_history_guard_fails_when_history_missing() -> None:
    assert _immutable_history_error({"captured_at": "x"}) == "immutable_history missing"


def test_sector_rotation_history_guard_fails_on_identity_mismatch() -> None:
    capture = _capture()
    capture["immutable_history"]["research_family"] = "other-family"
    assert _immutable_history_error(capture) == "immutable_history research family changed"


def test_sector_rotation_history_guard_fails_on_timestamp_mismatch() -> None:
    capture = _capture()
    capture["immutable_history"]["captured_at"] = "2026-08-20T14:11:00+00:00"
    assert _immutable_history_error(capture) == "immutable_history captured_at mismatch"


def test_sector_rotation_history_guard_fails_without_persisted_fingerprint() -> None:
    capture = _capture()
    capture["immutable_history"]["payload_fingerprint"] = ""
    assert _immutable_history_error(capture) == "immutable_history payload fingerprint missing"


def test_sector_rotation_history_guard_fails_on_empty_bucket_history() -> None:
    capture = _capture()
    capture["immutable_history"]["bucket_history_count"] = 0
    assert _immutable_history_error(capture) == "immutable_history bucket count is empty"
