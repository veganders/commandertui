"""Tests for schema version helpers in scryfall.py."""

import gzip
import json
from pathlib import Path

import pytest

import scryfall as sf


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Redirect scryfall's DATA_DIR and _SCHEMA_VERSION_FILE to a temp directory."""
    monkeypatch.setattr(sf, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sf, "_SCHEMA_VERSION_FILE", tmp_path / "schema_version")
    return tmp_path


def _touch_data_files(data_dir: Path) -> None:
    """Create stub data files so data_ready() won't fail on missing files."""
    for bulk_type in sf.WANTED_TYPES:
        p = data_dir / f"{bulk_type}.jsonl.gz"
        with gzip.open(p, "wt") as f:
            f.write(json.dumps({"stub": True}) + "\n")


# ── data_schema_version ───────────────────────────────────────────────────────

class TestDataSchemaVersion:
    def test_missing_file_returns_zero(self, data_dir):
        assert sf.data_schema_version() == 0

    def test_reads_written_version(self, data_dir):
        (data_dir / "schema_version").write_text("2")
        assert sf.data_schema_version() == 2

    def test_corrupt_file_returns_zero(self, data_dir):
        (data_dir / "schema_version").write_text("not-a-number")
        assert sf.data_schema_version() == 0


# ── data_ready ────────────────────────────────────────────────────────────────

class TestDataReady:
    def test_false_when_no_files(self, data_dir):
        assert not sf.data_ready()

    def test_false_when_schema_version_too_low(self, data_dir):
        _touch_data_files(data_dir)
        (data_dir / "schema_version").write_text("1")
        assert not sf.data_ready()

    def test_false_when_schema_version_missing(self, data_dir):
        _touch_data_files(data_dir)
        assert not sf.data_ready()

    def test_true_when_files_exist_and_version_current(self, data_dir):
        _touch_data_files(data_dir)
        (data_dir / "schema_version").write_text(str(sf.CURRENT_SCHEMA_VERSION))
        assert sf.data_ready()

    def test_false_when_one_data_file_missing(self, data_dir):
        _touch_data_files(data_dir)
        (data_dir / "schema_version").write_text(str(sf.CURRENT_SCHEMA_VERSION))
        next(f for f in data_dir.glob("*.jsonl.gz")).unlink()
        assert not sf.data_ready()
