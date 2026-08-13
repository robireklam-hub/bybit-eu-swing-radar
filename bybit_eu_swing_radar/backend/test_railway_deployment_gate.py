import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import scripts.railway_deployment_gate as gate
from scripts.railway_deployment_gate import deployment_result, railway_query, wait_for_deployments


def deployment(sha, status, created_at):
    return {"meta": {"commitHash": sha}, "status": status, "createdAt": created_at}


def test_older_failed_newer_success_passes():
    rows = [deployment("a", "FAILED", "2026-01-01T00:00:00Z"),
            deployment("a", "SUCCESS", "2026-01-01T00:01:00Z")]
    assert deployment_result(rows, "a") == "PASS"


def test_older_success_newer_failed_fails():
    rows = [deployment("a", "SUCCESS", "2026-01-01T00:00:00Z"),
            deployment("a", "FAILED", "2026-01-01T00:01:00Z")]
    assert deployment_result(rows, "a") == "FAIL"


def test_skipped_is_terminal_and_sleeping_is_fail_closed_wait():
    assert deployment_result([deployment("a", "SKIPPED", "2026-01-01T00:00:00Z")], "a") == "FAIL"
    assert deployment_result([deployment("a", "SLEEPING", "2026-01-01T00:00:00Z")], "a") == "WAIT"


def test_other_sha_success_is_ignored():
    assert deployment_result([deployment("b", "SUCCESS", "2026-01-01T00:00:00Z")], "a") == "WAIT"


def test_both_services_require_same_expected_sha():
    rows = {"api": [deployment("a", "SUCCESS", "2026-01-01T00:00:00Z")],
            "worker": [deployment("b", "SUCCESS", "2026-01-01T00:00:00Z")]}
    calls = []
    result = wait_for_deployments("a", {"api": "api", "worker": "worker"},
                                  query=lambda service: calls.append(service) or rows[service],
                                  timeout_seconds=0.01, poll_seconds=0)
    assert result == 1
    assert set(calls) == {"api", "worker"}


class Response:
    def __init__(self, payload):
        self.status = 200
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size=-1):
        return self.payload


def test_project_token_request_is_scoped_read_only_graphql_post(monkeypatch):
    monkeypatch.setenv("RAILWAY_API_TOKEN", "test-project-token")
    captured = {}

    def urlopen(request, timeout):
        captured.update(request=request, timeout=timeout)
        return Response({"data": {"deployments": {"edges": []}}})

    monkeypatch.setattr(gate, "urlopen", urlopen)
    data = railway_query("query { deployments { edges { node { id } } } }",
                         phase="api-service-deployments")
    assert data["deployments"]["edges"] == []
    request = captured["request"]
    body = json.loads(request.data)
    assert request.full_url == "https://backboard.railway.com/graphql/v2"
    assert request.get_method() == "POST"
    assert request.get_header("Project-access-token") == "test-project-token"
    assert request.get_header("Authorization") is None
    assert request.get_header("Content-type") == "application/json"
    assert "query" in body["query"].lower()
    assert "mutation" not in body["query"].lower()
    assert captured["timeout"] == 20


def test_railway_error_output_redacts_project_token(monkeypatch, capsys):
    monkeypatch.setenv("RAILWAY_API_TOKEN", "test-project-token")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")
    monkeypatch.setenv("RAILWAY_API_SERVICE_ID", "api")
    monkeypatch.setenv("RAILWAY_FLOW_WORKER_SERVICE_ID", "worker")
    monkeypatch.setenv("EXPECTED_SHA", "a")
    monkeypatch.setattr(gate, "validate_schema_once",
                        lambda sha: (_ for _ in ()).throw(RuntimeError("test-project-token")))
    assert gate.main() == 1
    assert "test-project-token" not in capsys.readouterr().out


def call_query(monkeypatch, response_or_error, capsys):
    monkeypatch.setenv("RAILWAY_API_TOKEN", "test-project-token")
    def opener(*args, **kwargs):
        if isinstance(response_or_error, Exception):
            raise response_or_error
        return Response(response_or_error)
    monkeypatch.setattr(gate, "urlopen", opener)
    try:
        railway_query("query { deployments { edges { node { id } } } }",
                      phase="api-service-deployments")
    except Exception as exc:
        print(gate.sanitize(exc))
    output = capsys.readouterr().out
    assert "test-project-token" not in output
    return output


def test_safe_http_diagnostics_401_403_429(monkeypatch, capsys):
    for code, reason in ((401, "Unauthorized"), (403, "Forbidden"), (429, "Too Many Requests")):
        error = HTTPError("https://railway", code, reason, {}, None)
        output = call_query(monkeypatch, error, capsys)
        assert f"http_status={code}" in output
        assert f"http_reason={reason}" in output


def test_safe_graphql_schema_error_redacts_token(monkeypatch, capsys):
    output = call_query(monkeypatch, {
        "errors": [{"message": "Unknown field test-project-token"}], "data": None
    }, capsys)
    assert "graphql_errors=" in output
    assert "[REDACTED]" in output
    assert "response_keys=['data', 'errors']" in output


def test_safe_invalid_json_and_url_error_diagnostics(monkeypatch, capsys):
    class InvalidResponse(Response):
        def read(self, size=-1):
            return b"not-json"
    monkeypatch.setenv("RAILWAY_API_TOKEN", "test-project-token")
    monkeypatch.setattr(gate, "urlopen", lambda *args, **kwargs: InvalidResponse({}))
    try:
        railway_query("query { deployments { edges { node { id } } } }",
                      phase="api-service-deployments")
    except Exception as exc:
        assert "json_error=JSONDecodeError" in str(exc)

    output = call_query(monkeypatch, URLError(RuntimeError("test-project-token")), capsys)
    assert "network_error=RuntimeError" in output


def test_pr_validation_uses_base_sha_and_has_no_production_smoke():
    workflow = Path("../../.github/workflows/production-flow-freshness-smoke.yml").read_text()
    validation = workflow.split("  production-smoke:", 1)[0]
    production = workflow.split("  production-smoke:", 1)[1]
    assert "EXPECTED_SHA: ${{ github.event.pull_request.base.sha }}" in validation
    assert "RAILWAY_GATE_MODE: validate-once" in validation
    assert "production_flow_freshness_smoke.py" not in validation
    assert "github.event.workflow_run.head_sha" in production
    assert "inputs.expected_sha || 'main'" in production
    assert "github.event_name != 'pull_request'" in production


def test_validation_mode_uses_exactly_two_deployment_calls_without_scope_query(monkeypatch, capsys):
    monkeypatch.setenv("RAILWAY_API_TOKEN", "token")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")
    monkeypatch.setenv("RAILWAY_API_SERVICE_ID", "api")
    monkeypatch.setenv("RAILWAY_FLOW_WORKER_SERVICE_ID", "worker")
    monkeypatch.setenv("EXPECTED_SHA", "a")
    monkeypatch.setenv("RAILWAY_GATE_MODE", "validate-once")
    calls = []
    monkeypatch.setattr(gate, "query_deployments", lambda service: calls.append(service) or [
        deployment("a", "SUCCESS", "2026-01-01T00:00:00Z")
    ])
    gate.API_CALLS = 2
    assert gate.main() == 0
    assert calls == ["api", "worker"]
    assert "Railway API calls: 2" in capsys.readouterr().out


def test_combined_poll_queries_both_services_once_per_round(monkeypatch):
    monkeypatch.setenv("RAILWAY_API_SERVICE_ID", "api")
    monkeypatch.setenv("RAILWAY_FLOW_WORKER_SERVICE_ID", "worker")
    calls = []
    monkeypatch.setattr(gate, "query_both_deployments", lambda api, worker: calls.append((api, worker)) or {
        "api": [deployment("a", "SUCCESS", "2026-01-01T00:00:00Z")],
        "flow-worker": [deployment("a", "SUCCESS", "2026-01-01T00:00:00Z")],
    })
    assert gate.wait_for_both_deployments("a", timeout_seconds=1, poll_seconds=30) == 0
    assert calls == [("api", "worker")]


def test_post_merge_defaults_cap_combined_queries_at_32():
    import inspect
    signature = inspect.signature(gate.wait_for_both_deployments)
    assert signature.parameters["timeout_seconds"].default == 960
    assert signature.parameters["poll_seconds"].default == 30
    # One immediate query plus at most 31 further rounds before the 960s deadline.
    assert 1 + 960 // 30 <= 33
