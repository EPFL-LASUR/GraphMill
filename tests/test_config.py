"""Tests for the config module (config.toml loading and path constants)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from graphmill import config as cfg
from graphmill.osm import SUPPORTED_COUNTRIES

# Captured at import time, before any test monkeypatches CONFIG_FILE.
REAL_CONFIG_FILE = cfg.CONFIG_FILE


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write a TOML string to a temp file and point cfg.CONFIG_FILE at it."""

    def _write(text: str) -> Path:
        path = tmp_path / "config.toml"
        path.write_text(text)
        monkeypatch.setattr(cfg, "CONFIG_FILE", path)
        return path

    return _write


MINIMAL = """
[osm]
countries = ["switzerland"]
"""


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
class TestPaths:
    def test_directory_layout(self):
        assert cfg.DATA_DIR == cfg.PROJECT_ROOT / "data"
        assert cfg.RAW_DIR == cfg.DATA_DIR / "raw"
        assert cfg.PREPROCESS_DIR == cfg.DATA_DIR / "preprocess"
        assert cfg.OTP_DIR == cfg.DATA_DIR / "ready"
        assert cfg.RAW_OSM_DIR == cfg.RAW_DIR / "osm"
        assert cfg.RAW_GTFS_DIR == cfg.RAW_DIR / "gtfs"

    def test_config_file_location(self):
        assert REAL_CONFIG_FILE == cfg.PROJECT_ROOT / "config" / "config.toml"

    def test_project_root_is_absolute(self):
        assert cfg.PROJECT_ROOT.is_absolute()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
class TestDataclasses:
    def test_gtfs_feed_config_options_default_is_empty_and_not_shared(self):
        a = cfg.GtfsFeedConfig(provider="p", output_filename="a.zip")
        b = cfg.GtfsFeedConfig(provider="p", output_filename="b.zip")
        assert a.options == {}
        assert a.options is not b.options

    def test_gtfs_feed_config_is_frozen(self):
        feed = cfg.GtfsFeedConfig(provider="p", output_filename="a.zip")
        with pytest.raises(dataclasses.FrozenInstanceError):
            feed.provider = "other"  # type: ignore[misc]

    def test_config_is_frozen(self):
        conf = cfg.Config(osm_countries=[], bbox=None, gtfs_feeds={})
        with pytest.raises(dataclasses.FrozenInstanceError):
            conf.bbox = (1, 2, 3, 4)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_config: [osm]
# ---------------------------------------------------------------------------
class TestLoadConfigOsm:
    def test_minimal_config(self, write_config):
        write_config(MINIMAL)
        conf = cfg.load_config()
        assert conf.osm_countries == ["switzerland"]
        assert conf.bbox is None
        assert conf.gtfs_feeds == {}

    def test_multiple_countries_preserve_order(self, write_config):
        write_config('[osm]\ncountries = ["france", "italy", "austria"]\n')
        assert cfg.load_config().osm_countries == ["france", "italy", "austria"]

    def test_bbox_is_converted_to_tuple_of_floats(self, write_config):
        write_config('[osm]\ncountries = ["switzerland"]\nbbox = [5, 45, 11, 48]\n')
        bbox = cfg.load_config().bbox
        assert bbox == (5.0, 45.0, 11.0, 48.0)
        assert isinstance(bbox, tuple)
        assert all(isinstance(v, float) for v in bbox)

    @pytest.mark.parametrize("bbox", ["[1, 2, 3]", "[1, 2, 3, 4, 5]", "[]"])
    def test_bbox_with_wrong_length_raises(self, write_config, bbox):
        write_config(f'[osm]\ncountries = ["switzerland"]\nbbox = {bbox}\n')
        with pytest.raises(ValueError, match="osm.bbox must have 4 values"):
            cfg.load_config()

    def test_bbox_with_non_numeric_values_raises(self, write_config):
        write_config('[osm]\ncountries = ["switzerland"]\nbbox = ["a", "b", "c", "d"]\n')
        with pytest.raises(ValueError):
            cfg.load_config()

    def test_missing_osm_section_raises(self, write_config):
        write_config('[gtfs.x]\nprovider = "static_url"\n')
        with pytest.raises(KeyError):
            cfg.load_config()

    def test_missing_countries_key_raises(self, write_config):
        write_config("[osm]\n")
        with pytest.raises(KeyError):
            cfg.load_config()

    def test_missing_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "does_not_exist.toml")
        with pytest.raises(FileNotFoundError):
            cfg.load_config()

    def test_invalid_toml_raises(self, write_config):
        import tomllib

        write_config("[osm\ncountries = ")
        with pytest.raises(tomllib.TOMLDecodeError):
            cfg.load_config()


# ---------------------------------------------------------------------------
# load_config: [gtfs.<name>]
# ---------------------------------------------------------------------------
class TestLoadConfigGtfs:
    def test_feed_fields_are_parsed(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.france]
provider = "static_url"
url = "https://example.com/france.zip"
output_filename = "fr.zip"
"""
        )
        feed = cfg.load_config().gtfs_feeds["france"]
        assert feed.provider == "static_url"
        assert feed.output_filename == "fr.zip"
        assert feed.options == {"url": "https://example.com/france.zip"}

    def test_options_exclude_provider_and_output_filename(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.switzerland]
provider = "opentransportdata_ckan"
publisher = "opentransportdata.swiss"
dataset_search_query = "GTFS2020"
output_filename = "switzerland.gtfs.zip"
"""
        )
        feed = cfg.load_config().gtfs_feeds["switzerland"]
        assert feed.options == {
            "publisher": "opentransportdata.swiss",
            "dataset_search_query": "GTFS2020",
        }

    def test_output_filename_defaults_to_name(self, write_config):
        write_config(MINIMAL + '\n[gtfs.spain]\nprovider = "static_url"\nurl = "u"\n')
        assert cfg.load_config().gtfs_feeds["spain"].output_filename == "spain.gtfs.zip"

    def test_missing_provider_raises(self, write_config):
        write_config(MINIMAL + '\n[gtfs.spain]\nurl = "u"\n')
        with pytest.raises(ValueError, match="gtfs.spain is missing a 'provider' key"):
            cfg.load_config()

    def test_empty_provider_raises(self, write_config):
        write_config(MINIMAL + '\n[gtfs.spain]\nprovider = ""\n')
        with pytest.raises(ValueError, match="missing a 'provider'"):
            cfg.load_config()

    def test_multiple_feeds(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.a]
provider = "static_url"
url = "ua"

[gtfs.b]
provider = "static_url"
url = "ub"
"""
        )
        feeds = cfg.load_config().gtfs_feeds
        assert list(feeds) == ["a", "b"]
        assert feeds["a"].options["url"] == "ua"
        assert feeds["b"].options["url"] == "ub"


# ---------------------------------------------------------------------------
# The real config/config.toml shipped with the project
# ---------------------------------------------------------------------------
@pytest.fixture
def real_config(monkeypatch):
    if not REAL_CONFIG_FILE.exists():
        pytest.skip("config/config.toml not found")
    monkeypatch.setattr(cfg, "CONFIG_FILE", REAL_CONFIG_FILE)
    return cfg.load_config()


class TestRealConfig:
    def test_real_config_loads(self, real_config):
        assert real_config.osm_countries
        assert real_config.gtfs_feeds

    def test_every_feed_has_known_provider(self, real_config):
        from graphmill.gtfs import PROVIDERS

        for name, feed in real_config.gtfs_feeds.items():
            assert feed.provider in PROVIDERS, f"gtfs.{name}: unknown provider {feed.provider}"

    def test_static_url_feeds_have_url(self, real_config):
        for name, feed in real_config.gtfs_feeds.items():
            if feed.provider == "static_url":
                assert feed.options.get("url", "").startswith("http"), name

    def test_output_filenames_are_unique(self, real_config):
        names = [f.output_filename for f in real_config.gtfs_feeds.values()]
        assert len(names) == len(set(names))

    def test_osm_countries_are_supported(self, real_config):
        unsupported = set(real_config.osm_countries) - SUPPORTED_COUNTRIES
        assert not unsupported