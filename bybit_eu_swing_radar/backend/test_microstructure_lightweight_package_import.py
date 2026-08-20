import subprocess
import sys
from pathlib import Path


def test_research_feature_import_does_not_require_asyncpg():
    backend_dir = Path(__file__).resolve().parent
    code = r'''
import importlib.abc
import sys

class BlockAsyncpg(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "asyncpg":
            raise ModuleNotFoundError("asyncpg intentionally unavailable")
        return None

sys.meta_path.insert(0, BlockAsyncpg())
from research.microstructure.controlled_pullback_calibration_v1 import MIN_ROWS_PER_SYMBOL
from research.microstructure.controlled_pullback_features_v1 import derive_calibration_feature_rows
assert MIN_ROWS_PER_SYMBOL == 100
assert callable(derive_calibration_feature_rows)
'''
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_collector_exports_remain_available_when_dependencies_exist():
    from research import microstructure

    assert microstructure.MicrostructureConfig is not None
    assert microstructure.MicrostructureRecorder is not None
    assert callable(microstructure.depth_metrics)
