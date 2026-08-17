from pathlib import Path

path = Path("bybit_eu_swing_radar/backend/worker.py")
text = path.read_text()

replacements = {
    'f"Coinalyze enrichment coverage: {enriched_count}/{target_count} targeted symbols ({len(analyses)} analyzed total).",':
    'f"Coinalyze rate-budget enrichment: {enriched_count}/{target_count} selected targets ({len(analyses)} analyzed total); compact top/watch coverage is reported separately.",',
    '"worker": {\n                "status": "ok",':
    '"worker": {\n                "status": "ok",\n                "source_commit_sha": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,',
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match, got {count}: {old!r}")
    text = text.replace(old, new, 1)

path.write_text(text)
print("worker coverage wording/source identity patched")
