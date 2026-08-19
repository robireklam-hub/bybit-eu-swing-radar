from pathlib import Path

backend = Path(__file__).resolve().parent
path = backend / "temp_apply_day_v074_versioning.py"
text = path.read_text(encoding="utf-8")
redundant = '        ra("does not change v0.7.3 STRICT gates", "does not change v0.7.4 STRICT gates", "openapi flow assertion"),\n'
if redundant in text:
    text = text.replace(redundant, "", 1)
old = '        spec.write_text(spec_text.rstrip() + annex + "\\n", encoding="utf-8")\n'
new = '        spec.write_text(spec_text.rstrip() + annex.rstrip() + "\\n", encoding="utf-8")\n'
if old not in text:
    raise RuntimeError("spec EOF normalization target not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
