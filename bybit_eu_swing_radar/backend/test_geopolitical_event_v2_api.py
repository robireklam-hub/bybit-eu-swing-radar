from __future__ import annotations

import io
import zipfile

import pytest

from app.research_geopolitical_event_v2_api import (
    parse_event_zip,
    parse_lastupdate_manifest,
)
from research.geopolitical_event_shadow_v2 import MIN_EVENT_COLUMNS


def make_row(event_id: str = "123", quad_class: int = 4) -> str:
    row = [""] * MIN_EVENT_COLUMNS
    row[0] = event_id
    row[1] = "20260818"
    row[25] = "1"
    row[26] = "190"
    row[27] = "19"
    row[28] = "19"
    row[29] = str(quad_class)
    row[30] = "-10"
    row[31] = "4"
    row[32] = "3"
    row[33] = "4"
    row[34] = "-2.5"
    row[51] = "UP"
    row[56] = "20260818123000"
    row[57] = "https://example.com/story"
    return "\t".join(row)


def make_zip(lines: list[str], name: str = "20260818123000.export.CSV") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, "\n".join(lines) + "\n")
    return buffer.getvalue()


def test_lastupdate_manifest_selects_event_export_line():
    text = "\n".join(
        [
            "100 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa http://data.gdeltproject.org/gdeltv2/20260818123000.mentions.CSV.zip",
            "1234 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb http://data.gdeltproject.org/gdeltv2/20260818123000.export.CSV.zip",
            "999 cccccccccccccccccccccccccccccccc http://data.gdeltproject.org/gdeltv2/20260818123000.gkg.csv.zip",
        ]
    )
    item = parse_lastupdate_manifest(text)
    assert item["manifest_declared_bytes"] == 1234
    assert item["manifest_md5"] == "b" * 32
    assert item["source_filename"] == "20260818123000.export.CSV.zip"
    assert item["source_file_timestamp"].isoformat() == "2026-08-18T12:30:00+00:00"


def test_lastupdate_manifest_rejects_missing_export():
    with pytest.raises(ValueError):
        parse_lastupdate_manifest("100 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa x.gkg.csv.zip")


def test_event_zip_parses_valid_and_invalid_rows():
    content = make_zip([make_row("1"), "too\tshort", make_row("2", quad_class=1)])
    events, meta = parse_event_zip(content)
    assert meta["total_rows"] == 3
    assert meta["valid_rows"] == 2
    assert meta["invalid_rows"] == 1
    assert [item["global_event_id"] for item in events] == ["1", "2"]


def test_event_zip_requires_exactly_one_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.CSV", make_row("1"))
        archive.writestr("b.CSV", make_row("2"))
    with pytest.raises(ValueError):
        parse_event_zip(buffer.getvalue())
