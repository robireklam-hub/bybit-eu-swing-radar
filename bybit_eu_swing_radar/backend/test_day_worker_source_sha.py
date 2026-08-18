from __future__ import annotations

import importlib
import inspect


def test_day_worker_exposes_railway_source_commit(monkeypatch):
    expected = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", expected)

    import day_worker

    module = importlib.reload(day_worker)
    try:
        assert module.SOURCE_COMMIT_SHA == expected
        source = inspect.getsource(module.run)
        assert '"source_commit_sha": SOURCE_COMMIT_SHA' in source
    finally:
        monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
        importlib.reload(day_worker)
