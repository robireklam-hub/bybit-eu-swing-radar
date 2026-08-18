from pathlib import Path


def test_swing_liquidity_workflow_has_guarded_backup_schedule():
    root = Path(__file__).resolve().parents[2]
    text = (root / ".github/workflows/swing-liquidity-shadow.yml").read_text()

    assert 'cron: "23 * * * *"' in text
    assert 'cron: "53 * * * *"' in text
    assert "SWING_LIQUIDITY_MIN_AGE_SECONDS" in text
    assert "'4500' || '2700'" in text
    assert "python scripts/swing_liquidity_capture_due.py" in text
    assert "if: steps.due.outputs.capture_due == 'true'" in text
    assert "if: steps.due.outputs.capture_due != 'true'" in text
