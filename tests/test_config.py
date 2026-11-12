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
        assert cfg.READY_DIR == cfg.DATA_DIR / "ready"
        assert cfg.RAW_OSM_DIR == cfg.RAW_DIR / "osm"
        assert cfg.RAW_GTFS_DIR == cfg.RAW_DIR / "gtfs"
        assert cfg.NETWORK_DIR == cfg.DATA_DIR / "network"
        assert cfg.REPORT_DIR == cfg.DATA_DIR / "report"

    def test_output_directories_are_distinct(self):
        dirs = [
            cfg.RAW_DIR,
            cfg.PREPROCESS_DIR,
            cfg.READY_DIR,
            cfg.NETWORK_DIR,
            cfg.REPORT_DIR,
        ]
        assert len(set(dirs)) == len(dirs)

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

    def test_gtfs_feed_config_shapes_network_defaults_to_none(self):
        feed = cfg.GtfsFeedConfig(provider="p", output_filename="a.zip")
        assert feed.shapes_network is None

    @pytest.mark.parametrize(
        "value", ["switzerland", ["france", "switzerland"], None]
    )
    def test_gtfs_feed_config_accepts_shapes_network(self, value):
        feed = cfg.GtfsFeedConfig(
            provider="p", output_filename="a.zip", shapes_network=value
        )
        assert feed.shapes_network == value

    def test_gtfs_feed_config_shapes_network_is_frozen(self):
        feed = cfg.GtfsFeedConfig(provider="p", output_filename="a.zip")
        with pytest.raises(dataclasses.FrozenInstanceError):
            feed.shapes_network = "switzerland"  # type: ignore[misc]

    def test_gtfs_feed_config_can_be_copied_with_replace(self):
        feed = cfg.GtfsFeedConfig(provider="p", output_filename="a.zip")
        updated = dataclasses.replace(feed, shapes_network="switzerland")
        assert updated.shapes_network == "switzerland"
        assert feed.shapes_network is None

    def test_config_can_be_copied_with_replace(self):
        conf = cfg.Config(osm_countries=["a"], bbox=None, gtfs_feeds={})
        feeds = {"x": cfg.GtfsFeedConfig("p", "x.zip")}
        updated = dataclasses.replace(conf, gtfs_feeds=feeds)
        assert updated.gtfs_feeds == feeds
        assert conf.gtfs_feeds == {}


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

    def test_load_config_is_repeatable(self, write_config):
        # load_config must not mutate state between calls.
        write_config(
            MINIMAL
            + """
[gtfs.a]
provider = "static_url"
url = "u"
shapes_network = "switzerland"
"""
        )
        assert cfg.load_config() == cfg.load_config()


# ---------------------------------------------------------------------------
# load_config: shapes_network
# ---------------------------------------------------------------------------
class TestLoadConfigShapesNetwork:
    def test_defaults_to_none(self, write_config):
        write_config(MINIMAL + '\n[gtfs.spain]\nprovider = "static_url"\nurl = "u"\n')
        assert cfg.load_config().gtfs_feeds["spain"].shapes_network is None

    def test_as_string(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.switzerland]
provider = "static_url"
url = "u"
shapes_network = "switzerland"
"""
        )
        feed = cfg.load_config().gtfs_feeds["switzerland"]
        assert feed.shapes_network == "switzerland"

    def test_as_list_preserves_order(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.france]
provider = "static_url"
url = "u"
shapes_network = ["france", "switzerland", "italy"]
"""
        )
        feed = cfg.load_config().gtfs_feeds["france"]
        assert feed.shapes_network == ["france", "switzerland", "italy"]

    def test_single_item_list_stays_a_list(self, write_config):
        write_config(
            MINIMAL
            + '\n[gtfs.a]\nprovider = "static_url"\nshapes_network = ["switzerland"]\n'
        )
        assert cfg.load_config().gtfs_feeds["a"].shapes_network == ["switzerland"]

    def test_is_not_part_of_options(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.switzerland]
provider = "static_url"
url = "u"
shapes_network = "switzerland"
"""
        )
        feed = cfg.load_config().gtfs_feeds["switzerland"]
        assert feed.options == {"url": "u"}
        assert "shapes_network" not in feed.options

    def test_with_all_keys_set(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.switzerland]
provider = "opentransportdata_ckan"
output_filename = "ch.zip"
dataset_search_query = "GTFS2020"
shapes_network = ["switzerland", "france"]
"""
        )
        feed = cfg.load_config().gtfs_feeds["switzerland"]
        assert feed.provider == "opentransportdata_ckan"
        assert feed.output_filename == "ch.zip"
        assert feed.options == {"dataset_search_query": "GTFS2020"}
        assert feed.shapes_network == ["switzerland", "france"]

    def test_is_set_per_feed(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.a]
provider = "static_url"
url = "ua"
shapes_network = "switzerland"

[gtfs.b]
provider = "static_url"
url = "ub"

[gtfs.c]
provider = "static_url"
url = "uc"
shapes_network = ["france", "italy"]
"""
        )
        feeds = cfg.load_config().gtfs_feeds
        assert feeds["a"].shapes_network == "switzerland"
        assert feeds["b"].shapes_network is None
        assert feeds["c"].shapes_network == ["france", "italy"]

    def test_feeds_do_not_leak_into_each_other(self, write_config):
        # Each [gtfs.<name>] table is copied before popping keys.
        write_config(
            MINIMAL
            + """
[gtfs.a]
provider = "static_url"
shapes_network = "switzerland"

[gtfs.b]
provider = "static_url"
"""
        )
        feeds = cfg.load_config().gtfs_feeds
        assert feeds["a"].options == {}
        assert feeds["b"].options == {}
        assert feeds["b"].shapes_network is None

    @pytest.mark.parametrize(
        "value",
        [
            "[]",
            '""',
            "5",
            "true",
            "[1, 2]",
            '["a", ""]',
            '["a", 1]',
            "[[\"a\"]]",
            "{ a = 1 }",
        ],
    )
    def test_invalid_value_raises(self, write_config, value):
        write_config(
            MINIMAL
            + f'\n[gtfs.a]\nprovider = "static_url"\nshapes_network = {value}\n'
        )
        with pytest.raises(ValueError, match="shapes_network"):
            cfg.load_config()

    def test_invalid_value_error_names_the_feed_and_value(self, write_config):
        write_config(
            MINIMAL
            + '\n[gtfs.france]\nprovider = "static_url"\nshapes_network = []\n'
        )
        with pytest.raises(ValueError, match=r"gtfs\.france\.shapes_network") as exc:
            cfg.load_config()

        assert "[]" in str(exc.value)

    def test_invalid_value_in_one_feed_rejects_the_whole_config(self, write_config):
        write_config(
            MINIMAL
            + """
[gtfs.good]
provider = "static_url"
shapes_network = "switzerland"

[gtfs.bad]
provider = "static_url"
shapes_network = []
"""
        )
        with pytest.raises(ValueError, match=r"gtfs\.bad\.shapes_network"):
            cfg.load_config()


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

    def test_shapes_network_has_no_duplicates(self, real_config):
        # Type and emptiness are already enforced by load_config.
        for name, feed in real_config.gtfs_feeds.items():
            sn = feed.shapes_network

            if sn is None:
                continue

            names = [sn] if isinstance(sn, str) else sn

            assert len(names) == len(set(names)), f"gtfs.{name}: duplicate networks"

    def test_shapes_network_countries_have_a_source_extract_or_pickle(
        self, real_config
    ):
        # A network is loaded from NETWORK_DIR/<n>.pkl or built from
        # RAW_OSM_DIR/<n>.osm.pbf. The latter is only produced for
        # configured OSM countries.
        known = set(real_config.osm_countries)

        for name, feed in real_config.gtfs_feeds.items():
            sn = feed.shapes_network

            if sn is None:
                continue

            for network in [sn] if isinstance(sn, str) else sn:
                has_pickle = (cfg.NETWORK_DIR / f"{network}.pkl").is_file()
                assert network in known or has_pickle, (
                    f"gtfs.{name}: network '{network}' is neither in osm.countries "
                    "nor an existing pickle"
                )