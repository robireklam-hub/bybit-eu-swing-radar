import subprocess
import sys
from pathlib import Path


def _run_with_asyncpg_blocked(code: str) -> subprocess.CompletedProcess[str]:
    backend_dir = Path(__file__).resolve().parent
    wrapped = r'''
import importlib.abc
import sys

class BlockAsyncpg(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "asyncpg":
            raise ModuleNotFoundError("asyncpg intentionally unavailable")
        return None

sys.meta_path.insert(0, BlockAsyncpg())
''' + code
    return subprocess.run(
        [sys.executable, "-c", wrapped],
        cwd=backend_dir,
        text=True,
        capture_output=True,
        check=False,
    )


def test_research_feature_import_does_not_require_asyncpg():
    completed = _run_with_asyncpg_blocked(r'''
from research.microstructure.controlled_pullback_calibration_v1 import MIN_ROWS_PER_SYMBOL
from research.microstructure.controlled_pullback_features_v1 import derive_calibration_feature_rows
assert MIN_ROWS_PER_SYMBOL == 100
assert callable(derive_calibration_feature_rows)
''')
    assert completed.returncode == 0, completed.stderr


def test_controlled_pullback_v2_contract_import_does_not_require_asyncpg():
    completed = _run_with_asyncpg_blocked(r'''
from research.microstructure.controlled_pullback_v2 import preregistration
from research.microstructure.alignment_v3 import alignment_spec
spec = preregistration()
alignment = alignment_spec()
assert spec["strategy_version"] == "0.7.5"
assert spec["outcome_visible"] is False
assert spec["promotion_allowed"] is False
assert alignment["preregistered_strategy_version"] == "0.7.5"
assert alignment["label_blind"] is True
''')
    assert completed.returncode == 0, completed.stderr


def test_collector_exports_remain_available_when_dependencies_exist():
    from research import microstructure

    assert microstructure.MicrostructureConfig is not None
    assert microstructure.MicrostructureRecorder is not None
    assert callable(microstructure.depth_metrics)
