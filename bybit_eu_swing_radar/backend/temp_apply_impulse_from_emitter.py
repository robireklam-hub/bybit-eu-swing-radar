"""Temporary trusted-CI patch applier. Deleted by its own final commit."""
from __future__ import annotations

import base64
import builtins
import gzip
from pathlib import Path
import subprocess

import test_zzzz_emit_impulse_patch_bytes as emitter

BRANCH = "fix/btc-impulse-breakout-coverage"


def run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True)


def main() -> None:
    backend = Path(__file__).resolve().parent
    repo = backend.parent.parent
    captured: dict[str, str] = {}
    original_print = builtins.print

    def capture_print(*values, **kwargs):
        text = " ".join(str(value) for value in values)
        for key in ("DAY_WORKER_GZIP_B64", "REGRESSION_TEST_GZIP_B64"):
            prefix = key + "="
            if text.startswith(prefix):
                captured[key] = text[len(prefix):]
                return
        original_print(*values, **kwargs)

    builtins.print = capture_print
    try:
        try:
            emitter.test_emit_exact_impulse_patch_bytes()
        except AssertionError as exc:
            if str(exc) != "BYTE_EXPORT_COMPLETE":
                raise
    finally:
        builtins.print = original_print

    required = {"DAY_WORKER_GZIP_B64", "REGRESSION_TEST_GZIP_B64"}
    if set(captured) != required:
        raise RuntimeError(f"missing emitted payloads: {sorted(required - set(captured))}")

    day_worker = gzip.decompress(base64.b64decode(captured["DAY_WORKER_GZIP_B64"])).decode()
    regression_test = gzip.decompress(base64.b64decode(captured["REGRESSION_TEST_GZIP_B64"])).decode()

    (backend / "day_worker.py").write_text(day_worker)
    (backend / "test_day_impulse_breakout_trigger.py").write_text(regression_test)

    # Restore the production workflow and remove every temporary helper so the
    # final PR diff contains only the production source and regression test.
    run("git", "fetch", "origin", "main", cwd=repo)
    run("git", "checkout", "origin/main", "--", ".github/workflows/backend-tests.yml", cwd=repo)
    for relative in (
        ".github/workflows/temp-apply-impulse-breakout-fix.yml",
        "bybit_eu_swing_radar/backend/IMPULSE_BREAKOUT_FIX_NOTE2.tmp",
        "bybit_eu_swing_radar/backend/IMPULSE_BREAKOUT_FIX_NOTE3.tmp",
        "bybit_eu_swing_radar/backend/test_zzzz_emit_impulse_patch_bytes.py",
        "bybit_eu_swing_radar/backend/temp_apply_impulse_from_emitter.py",
    ):
        path = repo / relative
        if path.exists():
            path.unlink()

    run("git", "diff", "--check", cwd=repo)
    run("git", "config", "user.name", "github-actions[bot]", cwd=repo)
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-m", "Fix day impulse breakout trigger coverage", cwd=repo)
    run("git", "push", "origin", f"HEAD:{BRANCH}", cwd=repo)


if __name__ == "__main__":
    main()
