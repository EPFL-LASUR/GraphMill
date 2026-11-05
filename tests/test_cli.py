"""Tests for the typer CLI (osm / gtfs / all commands).

The heavy lifting (process_osm / process_gtfs) and load_config are mocked, so
these tests only check argument parsing, validation and how options are
forwarded.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from graphmill import cli
from graphmill.config import Config, GtfsFeedConfig

runner = CliRunner()

CONFIG_BBOX = (5.0, 45.0, 11.0, 48.0)


@pytest.fixture
def mocks(monkeypatch):
    config = Config(
        osm_countries=["switzerland", "france"],
        bbox=CONFIG_BBOX,
        gtfs_feeds={
            "switzerland": GtfsFeedConfig("static_url", "switzerland.gtfs.zip", {"url": "u1"}),
            "france": GtfsFeedConfig("static_url", "france.gtfs.zip", {"url": "u2"}),
            "italy": GtfsFeedConfig("static_url", "italy.gtfs.zip", {"url": "u3"}),
        },
    )
    osm_mock, gtfs_mock = MagicMock(), MagicMock()
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "process_osm", osm_mock)
    monkeypatch.setattr(cli, "process_gtfs", gtfs_mock)
    return SimpleNamespace(config=config, osm=osm_mock, gtfs=gtfs_mock)


# ---------------------------------------------------------------------------
# _parse_bbox
# ---------------------------------------------------------------------------
class TestParseBbox:
    def test_none(self):
        assert cli._parse_bbox(None) is None

    def test_valid(self):
        assert cli._parse_bbox("6,46,7.5,47.25") == (6.0, 46.0, 7.5, 47.25)

    def test_negative_values(self):
        assert cli._parse_bbox("-9.5,36,3.3,43.8") == (-9.5, 36.0, 3.3, 43.8)

    @pytest.mark.parametrize("value", ["1,2,3", "1,2,3,4,5", "", "1"])
    def test_wrong_number_of_values(self, value):
        with pytest.raises(typer.BadParameter, match="min_lon,min_lat,max_lon,max_lat"):
            cli._parse_bbox(value)

    def test_non_numeric(self):
        with pytest.raises(typer.BadParameter, match="must be numbers"):
            cli._parse_bbox("a,b,c,d")


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------
class TestApp:
    def test_help_lists_commands(self):
        result = runner.invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        for command in ("osm", "gtfs", "all"):
            assert command in result.output

    def test_unknown_command_fails(self):
        result = runner.invoke(cli.app, ["bogus"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# osm
# ---------------------------------------------------------------------------
class TestOsmCommand:
    def test_uses_config_defaults(self, mocks):
        result = runner.invoke(cli.app, ["osm"])

        assert result.exit_code == 0, result.output
        mocks.osm.assert_called_once()
        args, kwargs = mocks.osm.call_args
        assert list(args[0]) == ["switzerland", "france"]
        assert kwargs == {"bbox": CONFIG_BBOX, "overwrite": False}

    def test_country_option_overrides_config(self, mocks):
        result = runner.invoke(cli.app, ["osm", "--country", "italy", "-c", "austria"])

        assert result.exit_code == 0, result.output
        args, _ = mocks.osm.call_args
        assert list(args[0]) == ["italy", "austria"]

    def test_echoes_countries(self, mocks):
        result = runner.invoke(cli.app, ["osm", "-c", "italy"])
        assert "Processing countries: italy" in result.output

    @pytest.mark.parametrize("flag", ["--overwrite", "-o"])
    def test_overwrite_flag(self, mocks, flag):
        result = runner.invoke(cli.app, ["osm", flag])
        assert result.exit_code == 0, result.output
        assert mocks.osm.call_args.kwargs["overwrite"] is True

    def test_bbox_option_overrides_config(self, mocks):
        result = runner.invoke(cli.app, ["osm", "--bbox", "1,2,3,4"])
        assert result.exit_code == 0, result.output
        assert mocks.osm.call_args.kwargs["bbox"] == (1.0, 2.0, 3.0, 4.0)

    def test_no_bbox_anywhere(self, mocks, monkeypatch):
        cfg = Config(osm_countries=["italy"], bbox=None, gtfs_feeds={})
        monkeypatch.setattr(cli, "load_config", lambda: cfg)
        result = runner.invoke(cli.app, ["osm"])
        assert result.exit_code == 0, result.output
        assert mocks.osm.call_args.kwargs["bbox"] is None

    def test_unsupported_country_is_rejected(self, mocks):
        result = runner.invoke(cli.app, ["osm", "-c", "atlantis"])
        assert result.exit_code == 2
        assert "atlantis" in result.output
        mocks.osm.assert_not_called()

    def test_one_invalid_among_valid_rejects_everything(self, mocks):
        result = runner.invoke(cli.app, ["osm", "-c", "italy", "-c", "atlantis"])
        assert result.exit_code == 2
        mocks.osm.assert_not_called()

    def test_invalid_bbox_is_rejected(self, mocks):
        result = runner.invoke(cli.app, ["osm", "--bbox", "1,2,3"])
        assert result.exit_code == 2
        mocks.osm.assert_not_called()

    def test_non_numeric_bbox_is_rejected(self, mocks):
        result = runner.invoke(cli.app, ["osm", "--bbox", "a,b,c,d"])
        assert result.exit_code == 2
        mocks.osm.assert_not_called()

    @pytest.mark.parametrize("country", ["belgium", "netherlands", "spain"])
    def test_countries_used_in_gtfs_config_are_accepted(self, mocks, country):
        result = runner.invoke(cli.app, ["osm", "-c", country])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# gtfs
# ---------------------------------------------------------------------------
class TestGtfsCommand:
    def test_uses_all_configured_feeds_by_default(self, mocks):
        result = runner.invoke(cli.app, ["gtfs"])

        assert result.exit_code == 0, result.output
        mocks.gtfs.assert_called_once()
        args, kwargs = mocks.gtfs.call_args
        assert args[0] == mocks.config.gtfs_feeds
        assert kwargs == {"bbox": CONFIG_BBOX, "overwrite": False}

    def test_feed_option_selects_subset(self, mocks):
        result = runner.invoke(cli.app, ["gtfs", "--feed", "france", "-f", "italy"])

        assert result.exit_code == 0, result.output
        selected = mocks.gtfs.call_args.args[0]
        assert list(selected) == ["france", "italy"]
        assert selected["france"] is mocks.config.gtfs_feeds["france"]

    def test_unknown_feed_is_rejected(self, mocks):
        result = runner.invoke(cli.app, ["gtfs", "-f", "atlantis"])
        assert result.exit_code == 2
        assert "atlantis" in result.output
        mocks.gtfs.assert_not_called()

    @pytest.mark.parametrize("flag", ["--overwrite", "-o"])
    def test_overwrite_flag(self, mocks, flag):
        result = runner.invoke(cli.app, ["gtfs", flag])
        assert result.exit_code == 0, result.output
        assert mocks.gtfs.call_args.kwargs["overwrite"] is True

    def test_bbox_option_overrides_config(self, mocks):
        result = runner.invoke(cli.app, ["gtfs", "--bbox", "1,2,3,4"])
        assert result.exit_code == 0, result.output
        assert mocks.gtfs.call_args.kwargs["bbox"] == (1.0, 2.0, 3.0, 4.0)

    def test_invalid_bbox_is_rejected(self, mocks):
        result = runner.invoke(cli.app, ["gtfs", "--bbox", "nope"])
        assert result.exit_code == 2
        mocks.gtfs.assert_not_called()

    def test_echoes_feed_names(self, mocks):
        result = runner.invoke(cli.app, ["gtfs", "-f", "france"])
        assert "Processing GTFS feeds: france" in result.output


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------
class TestAllCommand:
    def test_runs_osm_then_gtfs_with_config(self, mocks):
        order = []
        mocks.osm.side_effect = lambda *a, **k: order.append("osm")
        mocks.gtfs.side_effect = lambda *a, **k: order.append("gtfs")

        result = runner.invoke(cli.app, ["all"])

        assert result.exit_code == 0, result.output
        assert order == ["osm", "gtfs"]
        assert "=== OSM ===" in result.output
        assert "=== GTFS ===" in result.output
        osm_args, osm_kwargs = mocks.osm.call_args
        assert osm_args[0] == mocks.config.osm_countries
        assert osm_kwargs == {"bbox": CONFIG_BBOX, "overwrite": False}
        gtfs_args, gtfs_kwargs = mocks.gtfs.call_args
        assert gtfs_args[0] == mocks.config.gtfs_feeds
        assert gtfs_kwargs == {"bbox": CONFIG_BBOX, "overwrite": False}

    def test_overwrite_and_bbox_are_forwarded_to_both(self, mocks):
        result = runner.invoke(cli.app, ["all", "-o", "--bbox", "1,2,3,4"])

        assert result.exit_code == 0, result.output
        for m in (mocks.osm, mocks.gtfs):
            assert m.call_args.kwargs == {"bbox": (1.0, 2.0, 3.0, 4.0), "overwrite": True}

    def test_long_overwrite_flag(self, mocks):
        result = runner.invoke(cli.app, ["all", "--overwrite"])
        assert result.exit_code == 0, result.output
        assert mocks.osm.call_args.kwargs["overwrite"] is True

    def test_invalid_bbox_is_rejected(self, mocks):
        result = runner.invoke(cli.app, ["all", "--bbox", "1,2"])
        assert result.exit_code == 2
        mocks.gtfs.assert_not_called()

    def test_gtfs_not_run_if_osm_fails(self, mocks):
        mocks.osm.side_effect = RuntimeError("osmium missing")
        result = runner.invoke(cli.app, ["all"])
        assert result.exit_code != 0
        mocks.gtfs.assert_not_called()