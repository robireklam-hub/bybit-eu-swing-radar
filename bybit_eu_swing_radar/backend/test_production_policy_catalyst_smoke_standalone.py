from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_policy_catalyst_smoke_imports_sibling_research_package_from_foreign_cwd(tmp_path):
    backend = Path(__file__).resolve().parent
    script = backend / "scripts" / "production_policy_catalyst_smoke.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PRODUCTION_RADAR_API_BASE_URL", None)
    env.pop("PRODUCTION_RADAR_API_KEY", None)
    env.pop("EXPECTED_SHA", None)

    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert "required policy-catalyst smoke configuration is missing" in combined
    assert "ModuleNotFoundError" not in combined
