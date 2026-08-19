from pathlib import Path

path = Path(__file__).resolve().parent / "temp_apply_day_v074_versioning.py"
text = path.read_text(encoding="utf-8")
line = '        ra("does not change v0.7.3 STRICT gates", "does not change v0.7.4 STRICT gates", "openapi flow assertion"),\n'
if text.count(line) != 1:
    raise RuntimeError("expected exactly one redundant openapi assertion transform")
path.write_text(text.replace(line, "", 1), encoding="utf-8")
Path(__file__).unlink()
