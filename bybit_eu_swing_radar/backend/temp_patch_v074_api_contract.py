"""Temporary trusted-CI patch helper; self-deletes before merge."""
from pathlib import Path
import subprocess

BRANCH = "fix/v074-api-contract"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True)


def main() -> None:
    backend = Path(__file__).resolve().parent
    repo = backend.parent.parent
    run("git", "fetch", "origin", BRANCH, cwd=repo)
    run("git", "checkout", "-B", BRANCH, f"origin/{BRANCH}", cwd=repo)

    models = backend / "app" / "models.py"
    text = models.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''class DayTradeCandidate(BaseModel):\n    symbol: str\n    base_asset: str\n    quote_asset: str = "USDC"\n    strategy_mode: Literal["DAY_TRADE"] = "DAY_TRADE"\n    side: Literal["long", "short"]\n''',
        '''class DayTradeCandidate(BaseModel):\n    symbol: str\n    base_asset: str\n    quote_asset: str = "USDC"\n    strategy_mode: Literal["DAY_TRADE"] = "DAY_TRADE"\n    strategy_version: str | None = None\n    side: Literal["long", "short"]\n''',
        "candidate strategy version",
    )
    text = replace_once(
        text,
        '''    exclusions: list[dict[str, str]] = Field(default_factory=list)\n    journal: dict[str, Any] = Field(default_factory=dict)\n    notes: list[str] = Field(default_factory=list)\n\n\nclass DayTradeTopCandidatesResponse(BaseModel):\n''',
        '''    exclusions: list[dict[str, str]] = Field(default_factory=list)\n    journal: dict[str, Any] = Field(default_factory=dict)\n    prospective_funnel: dict[str, Any] = Field(default_factory=dict)\n    notes: list[str] = Field(default_factory=list)\n\n\nclass DayTradeTopCandidatesResponse(BaseModel):\n''',
        "scan prospective funnel",
    )
    text = replace_once(
        text,
        '''class DayTradeAuditTrigger(BaseModel):\n    timeframe: str\n    condition: str\n    price: float\n    requires_close: bool\n    volume_confirmation: str\n    triggered: bool\n''',
        '''class DayTradeAuditTrigger(BaseModel):\n    timeframe: str\n    condition: str\n    price: float\n    requires_close: bool\n    volume_confirmation: str\n    triggered: bool\n    route: str = "NONE"\n    model: str = ""\n''',
        "audit trigger route model",
    )
    models.write_text(text, encoding="utf-8")

    main_py = backend / "app" / "main.py"
    main_text = main_py.read_text(encoding="utf-8")
    main_text = replace_once(
        main_text,
        '    """Start exactly one v0.7.3 research replay batch inside Railway."""',
        '    """Start exactly one v0.7.4 research replay batch inside Railway."""',
        "backtest dispatch docstring",
    )
    main_py.write_text(main_text, encoding="utf-8")

    test = backend / "test_day_v074_api_contract.py"
    test.write_text('''from datetime import datetime, timezone\n\nfrom app.models import DayTradeAuditTrigger, DayTradeCandidate, DayTradeScanResponse\n\n\ndef test_day_candidate_contract_exposes_optional_strategy_version():\n    field = DayTradeCandidate.model_fields["strategy_version"]\n    assert field.default is None\n\n\ndef test_audit_trigger_contract_preserves_route_and_model_and_is_backward_compatible():\n    current = DayTradeAuditTrigger(\n        timeframe="5m",\n        condition="closed range breakout",\n        price=100.0,\n        requires_close=True,\n        volume_confirmation="not triggered",\n        triggered=True,\n        route="CLOSED_5M_RANGE_BREAKOUT",\n        model="CLOSED_5M_12_BAR_RANGE_BREAKOUT",\n    )\n    assert current.model_dump()["route"] == "CLOSED_5M_RANGE_BREAKOUT"\n    assert current.model_dump()["model"] == "CLOSED_5M_12_BAR_RANGE_BREAKOUT"\n\n    legacy = DayTradeAuditTrigger(\n        timeframe="5m",\n        condition="legacy",\n        price=100.0,\n        requires_close=True,\n        volume_confirmation="legacy",\n        triggered=False,\n    )\n    assert legacy.route == "NONE"\n    assert legacy.model == ""\n\n\ndef test_scan_contract_preserves_pinned_v073_prospective_funnel_metadata():\n    scan = DayTradeScanResponse(\n        data_as_of=datetime(2026, 8, 19, tzinfo=timezone.utc),\n        data_as_of_budapest="2026-08-19T22:00:00+02:00",\n        data_quality="GOOD",\n        prospective_funnel={\n            "spec_version": "v073-prospective-funnel-v1",\n            "strategy_version": "0.7.3",\n            "live_strategy_version": "0.7.4",\n        },\n    )\n    assert scan.prospective_funnel["strategy_version"] == "0.7.3"\n    assert scan.prospective_funnel["live_strategy_version"] == "0.7.4"\n''', encoding="utf-8")

    run("git", "fetch", "origin", "main", cwd=repo)
    run("git", "checkout", "origin/main", "--", ".github/workflows/backend-tests.yml", cwd=repo)
    Path(__file__).unlink()
    pycache = backend / "__pycache__"
    if pycache.exists():
        for child in pycache.glob("temp_patch_v074_api_contract*.pyc"):
            child.unlink()

    run("git", "diff", "--check", cwd=repo)
    run("git", "config", "user.name", "github-actions[bot]", cwd=repo)
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-m", "Expose day v0.7.4 trigger API contract", cwd=repo)
    run("git", "push", "origin", f"HEAD:{BRANCH}", cwd=repo)


if __name__ == "__main__":
    main()
