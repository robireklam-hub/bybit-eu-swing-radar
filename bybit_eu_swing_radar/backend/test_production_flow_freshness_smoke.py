from datetime import datetime, timedelta, timezone
import io
import json
from urllib.error import HTTPError, URLError

import scripts.production_flow_freshness_smoke as smoke
from scripts.production_flow_freshness_smoke import PATHS, fetch_json, run_smoke


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def status(**overrides):
    value = {
        "data_as_of": NOW.isoformat(),
        "flow_batch_id": "batch-a",
        "processed": 3,
        "good": 1,
        "partial": 1,
        "no_derivative_match": 1,
    }
    value.update(overrides)
    return value


def context(age, quality="GOOD", coverage="GOOD", **overrides):
    value = {
        "data_as_of": (NOW - timedelta(seconds=age)).isoformat(),
        "data_quality": quality,
        "coverage_status": coverage,
        "flow_batch_id": "batch-a",
    }
    value.update(overrides)
    return value


def run(responses):
    calls = []

    def fetch(url, api_key, timeout):
        calls.append((url, api_key, timeout))
        return responses[len(calls) - 1]

    return run_smoke("https://production.example", "secret", fetch=fetch), calls


def test_exactly_four_gets_in_order_and_boundary_good_passes():
    result, calls = run([status(), context(300), context(1), context(2)])
    assert result == 0
    assert [call[0] for call in calls] == [
        f"https://production.example{path}" for _, path in PATHS
    ]
    assert all(call[1] == "secret" for call in calls)
    assert len(calls) == 4


def test_stale_good_fails_without_retry():
    result, calls = run([status(), context(300.001), context(1), context(2)])
    assert result == 1
    assert len(calls) == 4


def test_status_counter_invariant_fails():
    result, _ = run([status(processed=4), context(1), context(2), context(3)])
    assert result == 1


def test_missing_and_malformed_timestamp_fail():
    missing = context(1)
    missing.pop("data_as_of")
    malformed = context(1, generated_at="not-a-time")
    result, _ = run([status(), missing, malformed, context(1)])
    assert result == 1


def test_specific_non_good_coverage_reasons_do_not_false_fail():
    result, _ = run([
        status(good=0, partial=2, no_derivative_match=1),
        context(301, "DEGRADED", "NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH"),
        context(301, "PARTIAL", "PARTIAL_OI_HISTORY_ERROR"),
        context(1, "PARTIAL", "PARTIAL_OI_HISTORY_ERROR"),
    ])
    assert result == 0


def test_timestamp_priority_uses_generated_at_first():
    result, _ = run([
        status(),
        context(1, generated_at=(NOW - timedelta(seconds=301)).isoformat()),
        context(1),
        context(1),
    ])
    assert result == 1


def test_success_reports_worker_execution_not_verified(capsys):
    result, calls = run([status(), context(1), context(2), context(3)])
    assert result == 0 and len(calls) == 4
    output = capsys.readouterr().out
    assert "DEPLOYMENT VERIFIED, WORKER EXECUTION NOT VERIFIED." in output
    assert "WORKER EXECUTION VERIFIED" not in output


def test_request_exception_continues_without_retry_and_redacts_secret(capsys):
    calls = []
    responses = [status(), URLError("test-secret"), context(1), context(2)]

    def fetch(url, api_key, timeout):
        calls.append(url)
        value = responses[len(calls) - 1]
        if isinstance(value, Exception):
            raise value
        return value

    result = run_smoke("https://production.example", "test-secret", fetch=fetch)
    assert result == 1
    assert calls == [f"https://production.example{path}" for _, path in PATHS]
    assert "test-secret" not in capsys.readouterr().out


class Response:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = json.dumps(body if body is not None else {"ok": True}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size=-1):
        return self.body


def test_fetch_json_constructs_get_header_url_and_timeout(monkeypatch):
    captured = {}

    def urlopen(request, timeout):
        captured.update(request=request, timeout=timeout)
        return Response(body={"ok": True})

    monkeypatch.setattr(smoke, "urlopen", urlopen)
    assert fetch_json("https://example.test/path", "test-key", 7) == {"ok": True}
    request = captured["request"]
    assert request.full_url == "https://example.test/path"
    assert request.get_method() == "GET"
    assert request.get_header("X-radar-key") == "test-key"
    assert captured["timeout"] == 7


def test_fetch_json_non_2xx_and_invalid_json_fail_without_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(smoke, "urlopen", lambda *args, **kwargs: calls.append(1) or Response(500))
    try:
        fetch_json("https://example.test", "key", 1)
        assert False
    except RuntimeError:
        pass
    assert len(calls) == 1

    response = Response()
    response.body = b"not-json"
    monkeypatch.setattr(smoke, "urlopen", lambda *args, **kwargs: response)
    try:
        fetch_json("https://example.test", "key", 1)
        assert False
    except ValueError:
        pass
