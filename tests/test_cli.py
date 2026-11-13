"""Tests for the typer CLI (osm / gtfs / all commands).

The heavy lifting (process_osm / process_gtfs) and load_config are mocked, so
these tests only check argument parsing, validation and how options are
forwarded.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from dataclasses import replace

import pytest
import typer
from typer.testing import CliRunner

from transitforge import TransitRouteNetwork
from transitforge.dataclasses import TransitRoute, TransitStop

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


def make_network(*relation_ids: int) -> TransitRouteNetwork:
    routes = {
        rid: TransitRoute(
            relation_id=rid,
            route_type="bus",
            ref="12",
            stops=[TransitStop(rid * 10, 46.0, 7.0, "A")],
        )
        for rid in relation_ids
    }
    return TransitRouteNetwork(routes)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Redirect the directory constants used by cli.py into tmp_path."""
    d = SimpleNamespace(
        network=tmp_path / "networks",
        osm=tmp_path / "osm",
        ready=tmp_path / "ready",
    )
    d.osm.mkdir()
    d.ready.mkdir()
    monkeypatch.setattr(cli, "NETWORK_DIR", d.network)
    monkeypatch.setattr(cli, "RAW_OSM_DIR", d.osm)
    monkeypatch.setattr(cli, "READY_DIR", d.ready)
    return d


@pytest.fixture
def shape_mocks(mocks, dirs, monkeypatch):
    """
    Config where switzerland -> "switzerland" network, france -> two
    networks, italy -> no network. Network loading and shape generation
    are replaced by recorders.
    """
    feeds = {
        "switzerland": GtfsFeedConfig(
            "static_url", "switzerland.gtfs.zip", {"url": "u1"},
            shapes_network="switzerland",
        ),
        "france": GtfsFeedConfig(
            "static_url", "france.gtfs.zip", {"url": "u2"},
            shapes_network=["france", "switzerland"],
        ),
        "italy": GtfsFeedConfig(
            "static_url", "italy.gtfs.zip", {"url": "u3"}
        ),
    }

    config = replace(mocks.config, gtfs_feeds=feeds)
    monkeypatch.setattr(cli, "load_config", lambda: config)

    gtfs_files = {n: dirs.ready / f"{n}.ready.gtfs.zip" for n in feeds}
    mocks.gtfs.return_value = gtfs_files

    loaded = []  # (countries, cache) per call

    def fake_load_networks(countries, cache):
        loaded.append((countries, cache))
        return ("network", tuple([countries] if isinstance(countries, str) else countries))

    add_shapes = MagicMock()
    monkeypatch.setattr(cli, "_load_networks", fake_load_networks)
    monkeypatch.setattr(cli, "add_gtfs_shapes", add_shapes)

    return SimpleNamespace(
        config=config,
        gtfs=mocks.gtfs,
        gtfs_files=gtfs_files,
        loaded=loaded,
        add_shapes=add_shapes,
        dirs=dirs,
    )

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
# _load_one_network
# ---------------------------------------------------------------------------
class TestLoadOneNetwork:
    @pytest.fixture
    def extract(self, monkeypatch):
        built = make_network(1)
        saved = []
        built.save = lambda path: saved.append(path)
        calls = []

        def fake_extract(osm_path):
            calls.append(osm_path)
            return built

        monkeypatch.setattr(cli, "extract_pt_network", fake_extract)
        return SimpleNamespace(network=built, saved=saved, calls=calls)

    def test_loads_cached_pickle(self, dirs, extract, monkeypatch, capsys):
        dirs.network.mkdir()
        pkl = dirs.network / "switzerland.pkl"
        pkl.write_bytes(b"")
        cached = make_network(7)
        loads = []
        monkeypatch.setattr(
            cli.TransitRouteNetwork, "load", lambda path: loads.append(path) or cached
        )

        result = cli._load_one_network("switzerland")

        assert result is cached
        assert loads == [pkl]
        assert extract.calls == []
        assert "Loading cached PT network" in capsys.readouterr().out

    def test_cached_pickle_wins_over_osm_file(self, dirs, extract, monkeypatch):
        dirs.network.mkdir()
        (dirs.network / "france.pkl").write_bytes(b"")
        (dirs.osm / "france.osm.pbf").write_bytes(b"")
        monkeypatch.setattr(cli.TransitRouteNetwork, "load", lambda path: make_network(7))

        cli._load_one_network("france")

        assert extract.calls == []

    def test_missing_pickle_and_osm_raises(self, dirs, extract):
        with pytest.raises(typer.BadParameter, match="OSM extract not found"):
            cli._load_one_network("atlantis")

        assert extract.calls == []

    def test_missing_osm_error_mentions_path_and_download_command(self, dirs, extract):
        with pytest.raises(typer.BadParameter) as exc:
            cli._load_one_network("atlantis")

        assert str(dirs.osm / "atlantis.osm.pbf") in str(exc.value)
        assert "download" in str(exc.value)

    def test_extracts_and_saves_when_only_osm_exists(self, dirs, extract, capsys):
        osm = dirs.osm / "italy.osm.pbf"
        osm.write_bytes(b"")

        result = cli._load_one_network("italy")

        assert result is extract.network
        assert extract.calls == [osm]
        assert extract.saved == [dirs.network / "italy.pkl"]
        out = capsys.readouterr().out
        assert "Extracting PT network from OSM" in out
        assert "Saving PT network" in out

    def test_creates_network_dir_before_saving(self, dirs, extract):
        (dirs.osm / "italy.osm.pbf").write_bytes(b"")
        assert not dirs.network.exists()

        cli._load_one_network("italy")

        assert dirs.network.is_dir()


# ---------------------------------------------------------------------------
# _load_networks
# ---------------------------------------------------------------------------
class TestLoadNetworks:
    @pytest.fixture
    def loader(self, monkeypatch):
        nets = {"ch": make_network(1), "fr": make_network(2), "it": make_network(3)}
        calls = []

        def fake(country):
            calls.append(country)
            return nets[country]

        monkeypatch.setattr(cli, "_load_one_network", fake)
        return SimpleNamespace(nets=nets, calls=calls)

    def test_single_string_returns_that_network(self, loader):
        assert cli._load_networks("ch", {}) is loader.nets["ch"]

    def test_single_item_list_returns_that_network_unmerged(self, loader):
        assert cli._load_networks(["fr"], {}) is loader.nets["fr"]

    def test_loaded_networks_are_cached(self, loader):
        cache = {}

        cli._load_networks(["ch", "fr"], cache)

        assert cache == {"ch": loader.nets["ch"], "fr": loader.nets["fr"]}

    def test_cached_networks_are_not_reloaded(self, loader):
        cache = {"ch": loader.nets["ch"]}

        cli._load_networks(["ch", "fr"], cache)

        assert loader.calls == ["fr"]

    def test_cache_is_reused_across_calls(self, loader):
        cache = {}

        cli._load_networks("ch", cache)
        cli._load_networks(["ch", "fr"], cache)
        cli._load_networks("fr", cache)

        assert loader.calls == ["ch", "fr"]

    def test_multiple_countries_are_merged(self, loader):
        merged = cli._load_networks(["ch", "fr", "it"], {})

        assert set(merged.routes) == {1, 2, 3}
        assert merged is not loader.nets["ch"]

    def test_merge_does_not_modify_cached_networks(self, loader):
        cache = {}

        cli._load_networks(["ch", "fr"], cache)

        assert set(cache["ch"].routes) == {1}
        assert set(cache["fr"].routes) == {2}

    def test_merge_is_silent(self, loader, capsys):
        cli._load_networks(["ch", "fr"], {})

        assert "Merged networks" not in capsys.readouterr().out

    def test_earlier_network_wins_a_full_tie(self, monkeypatch):
        first, second = make_network(1), make_network(1)
        nets = {"a": first, "b": second}
        monkeypatch.setattr(cli, "_load_one_network", nets.__getitem__)

        merged = cli._load_networks(["a", "b"], {})

        assert merged.routes[1] is first.routes[1]

    def test_duplicate_countries_merge_harmlessly(self, loader):
        merged = cli._load_networks(["ch", "ch"], {})

        assert set(merged.routes) == {1}
        assert loader.calls == ["ch"]

    def test_loader_error_propagates(self, monkeypatch):
        def boom(country):
            raise typer.BadParameter(f"no {country}")

        monkeypatch.setattr(cli, "_load_one_network", boom)

        with pytest.raises(typer.BadParameter, match="no ch"):
            cli._load_networks(["ch", "fr"], {})


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------
class TestApp:
    def test_help_lists_commands(self):
        result = runner.invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        for command in ("osm", "gtfs", "all", "add-shapes", "filter-gtfs", "extract-network"):
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

    def test_no_shapes_by_default(self, shape_mocks):
        result = runner.invoke(cli.app, ["gtfs"])

        assert result.exit_code == 0, result.output
        shape_mocks.add_shapes.assert_not_called()
        assert shape_mocks.loaded == []

    def test_shape_feed_without_shapes_flag_does_nothing(self, shape_mocks):
        result = runner.invoke(cli.app, ["gtfs", "--shape-feed", "switzerland"])

        assert result.exit_code == 0, result.output
        shape_mocks.add_shapes.assert_not_called()

    def test_shapes_for_feeds_with_a_configured_network(self, shape_mocks):
        result = runner.invoke(cli.app, ["gtfs", "--shapes"])

        assert result.exit_code == 0, result.output
        names = [c.kwargs["name"] for c in shape_mocks.add_shapes.call_args_list]
        # italy has no shapes_network and is skipped
        assert names == ["switzerland", "france"]

    def test_add_shapes_receives_processed_file_network_and_options(self, shape_mocks):
        result = runner.invoke(cli.app, ["gtfs", "--shapes", "-f", "switzerland"])

        assert result.exit_code == 0, result.output
        shape_mocks.add_shapes.assert_called_once_with(
            shape_mocks.gtfs_files["switzerland"],
            ("network", ("switzerland",)),
            verbose=False,
            name="switzerland",
        )

    def test_multi_country_network_config_is_passed_through(self, shape_mocks):
        runner.invoke(cli.app, ["gtfs", "--shapes", "-f", "france"])

        countries, _ = shape_mocks.loaded[0]
        assert countries == ["france", "switzerland"]

    @pytest.mark.parametrize("flag", ["--verbose", "-v"])
    def test_verbose_is_forwarded(self, shape_mocks, flag):
        runner.invoke(cli.app, ["gtfs", "--shapes", "-f", "switzerland", flag])

        assert shape_mocks.add_shapes.call_args.kwargs["verbose"] is True

    def test_network_cache_is_shared_between_feeds(self, shape_mocks):
        runner.invoke(cli.app, ["gtfs", "--shapes"])

        caches = [cache for _, cache in shape_mocks.loaded]
        assert len(caches) == 2
        assert caches[0] is caches[1]

    def test_shape_feed_restricts_feeds(self, shape_mocks):
        result = runner.invoke(
            cli.app, ["gtfs", "--shapes", "--shape-feed", "france"]
        )

        assert result.exit_code == 0, result.output
        names = [c.kwargs["name"] for c in shape_mocks.add_shapes.call_args_list]
        assert names == ["france"]

    def test_multiple_shape_feeds_keep_given_order(self, shape_mocks):
        runner.invoke(
            cli.app,
            ["gtfs", "--shapes", "--shape-feed", "france", "--shape-feed", "switzerland"],
        )

        names = [c.kwargs["name"] for c in shape_mocks.add_shapes.call_args_list]
        assert names == ["france", "switzerland"]

    def test_shape_feed_must_be_among_selected_feeds(self, shape_mocks):
        result = runner.invoke(
            cli.app,
            ["gtfs", "--shapes", "-f", "switzerland", "--shape-feed", "france"],
        )

        assert result.exit_code == 2
        assert "france" in result.output
        shape_mocks.add_shapes.assert_not_called()

    def test_unknown_shape_feed_is_rejected(self, shape_mocks):
        result = runner.invoke(
            cli.app, ["gtfs", "--shapes", "--shape-feed", "atlantis"]
        )

        assert result.exit_code == 2
        assert "atlantis" in result.output

    def test_shape_feed_without_shapes_network_is_rejected(self, shape_mocks):
        result = runner.invoke(
            cli.app, ["gtfs", "--shapes", "--shape-feed", "italy"]
        )

        assert result.exit_code == 2
        assert "No shapes_network configured for GTFS feed 'italy'" in result.output
        shape_mocks.add_shapes.assert_not_called()

    def test_no_configured_feeds_prints_message(self, shape_mocks):
        result = runner.invoke(cli.app, ["gtfs", "--shapes", "-f", "italy"])

        assert result.exit_code == 0, result.output
        assert "No feeds configured for shape generation." in result.output
        shape_mocks.add_shapes.assert_not_called()

    def test_gtfs_is_processed_before_shapes(self, shape_mocks):
        order = []
        shape_mocks.gtfs.side_effect = lambda *a, **k: (
            order.append("gtfs") or shape_mocks.gtfs_files
        )
        shape_mocks.add_shapes.side_effect = lambda *a, **k: order.append("shapes")

        runner.invoke(cli.app, ["gtfs", "--shapes", "-f", "switzerland"])

        assert order == ["gtfs", "shapes"]

    def test_echoes_feed_being_shaped(self, shape_mocks):
        result = runner.invoke(cli.app, ["gtfs", "--shapes", "-f", "switzerland"])

        assert "[switzerland] Generating GTFS shapes." in result.output




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



# ---------------------------------------------------------------------------
# add-shapes
# ---------------------------------------------------------------------------
class TestAddShapesCommand:
    @pytest.fixture
    def ready(self, shape_mocks):
        """Create the ready file of every feed."""
        for path in shape_mocks.gtfs_files.values():
            path.write_bytes(b"")
        return shape_mocks

    def test_feed_option_is_required(self, ready):
        result = runner.invoke(cli.app, ["add-shapes"])

        assert result.exit_code == 2
        ready.add_shapes.assert_not_called()

    def test_adds_shapes_to_the_ready_file(self, ready):
        result = runner.invoke(cli.app, ["add-shapes", "-f", "switzerland"])

        assert result.exit_code == 0, result.output
        ready.add_shapes.assert_called_once_with(
            ready.dirs.ready / "switzerland.ready.gtfs.zip",
            ("network", ("switzerland",)),
            verbose=False,
            name="switzerland",
        )

    def test_does_not_reprocess_gtfs(self, ready):
        runner.invoke(cli.app, ["add-shapes", "-f", "switzerland"])

        ready.gtfs.assert_not_called()

    @pytest.mark.parametrize("flag", ["--verbose", "-v"])
    def test_verbose_is_forwarded(self, ready, flag):
        runner.invoke(cli.app, ["add-shapes", "-f", "switzerland", flag])

        assert ready.add_shapes.call_args.kwargs["verbose"] is True

    def test_multiple_feeds_share_one_network_cache(self, ready):
        result = runner.invoke(
            cli.app, ["add-shapes", "-f", "switzerland", "-f", "france"]
        )

        assert result.exit_code == 0, result.output
        names = [c.kwargs["name"] for c in ready.add_shapes.call_args_list]
        assert names == ["switzerland", "france"]
        assert ready.loaded[0][1] is ready.loaded[1][1]

    def test_unknown_feed_is_rejected_before_any_work(self, ready):
        result = runner.invoke(
            cli.app, ["add-shapes", "-f", "switzerland", "-f", "atlantis"]
        )

        assert result.exit_code == 2
        assert "atlantis" in result.output
        ready.add_shapes.assert_not_called()
        assert ready.loaded == []

    def test_feed_without_shapes_network_is_rejected(self, ready):
        result = runner.invoke(cli.app, ["add-shapes", "-f", "italy"])

        assert result.exit_code == 2
        assert "No shapes_network configured for GTFS feed 'italy'" in result.output
        ready.add_shapes.assert_not_called()

    def test_missing_ready_file_is_rejected(self, ready):
        ready.gtfs_files["switzerland"].unlink()

        result = runner.invoke(cli.app, ["add-shapes", "-f", "switzerland"])

        assert result.exit_code == 2
        assert "Ready GTFS file not found for 'switzerland'" in result.output
        ready.add_shapes.assert_not_called()
        assert ready.loaded == []  # no network loaded for a missing file

    def test_failure_on_later_feed_keeps_earlier_results(self, ready):
        # Documents current behaviour: feeds are validated one at a time.
        ready.gtfs_files["france"].unlink()

        result = runner.invoke(
            cli.app, ["add-shapes", "-f", "switzerland", "-f", "france"]
        )

        assert result.exit_code == 2
        assert [c.kwargs["name"] for c in ready.add_shapes.call_args_list] == [
            "switzerland"
        ]

    def test_echoes_feed_being_shaped(self, ready):
        result = runner.invoke(cli.app, ["add-shapes", "-f", "switzerland"])

        assert "[switzerland] Generating GTFS shapes." in result.output


# ---------------------------------------------------------------------------
# filter-gtfs
# ---------------------------------------------------------------------------
class TestFilterGtfsCommand:
    @pytest.fixture
    def filt(self, mocks, dirs, monkeypatch):
        for name in ("switzerland", "france"):
            (dirs.ready / f"{name}.ready.gtfs.zip").write_bytes(b"")
        mock = MagicMock()
        monkeypatch.setattr(cli, "filter_gtfs_per_mode", mock)
        return SimpleNamespace(mock=mock, dirs=dirs, config=mocks.config)

    def test_feed_and_mode_are_required(self, filt):
        for args in (["filter-gtfs"], ["filter-gtfs", "-f", "france"], ["filter-gtfs", "-m", "3"]):
            assert runner.invoke(cli.app, args).exit_code == 2

        filt.mock.assert_not_called()

    def test_filters_the_ready_file(self, filt):
        result = runner.invoke(cli.app, ["filter-gtfs", "-f", "france", "-m", "3"])

        assert result.exit_code == 0, result.output
        filt.mock.assert_called_once_with(
            gtfs=filt.dirs.ready / "france.ready.gtfs.zip",
            route_types=[3],
            output_path=filt.dirs.ready / "france_filtered.gtfs.zip",
        )

    def test_output_name_has_no_ready_infix(self, filt):
        runner.invoke(cli.app, ["filter-gtfs", "-f", "france", "-m", "3"])

        output = filt.mock.call_args.kwargs["output_path"]
        assert output.name == "france_filtered.gtfs.zip"

    def test_multiple_modes_are_passed_as_a_list(self, filt):
        runner.invoke(
            cli.app, ["filter-gtfs", "-f", "france", "-m", "2", "--mode", "3", "-m", "700"]
        )

        assert filt.mock.call_args.kwargs["route_types"] == [2, 3, 700]

    def test_multiple_feeds_are_filtered_in_order(self, filt):
        result = runner.invoke(
            cli.app, ["filter-gtfs", "-f", "switzerland", "-f", "france", "-m", "3"]
        )

        assert result.exit_code == 0, result.output
        inputs = [c.kwargs["gtfs"].name for c in filt.mock.call_args_list]
        assert inputs == ["switzerland.ready.gtfs.zip", "france.ready.gtfs.zip"]

    def test_unknown_feed_is_rejected(self, filt):
        result = runner.invoke(
            cli.app, ["filter-gtfs", "-f", "france", "-f", "atlantis", "-m", "3"]
        )

        assert result.exit_code == 2
        assert "atlantis" in result.output
        filt.mock.assert_not_called()

    def test_missing_ready_file_is_rejected(self, filt):
        # italy is configured, but has no ready file
        result = runner.invoke(cli.app, ["filter-gtfs", "-f", "italy", "-m", "3"])

        assert result.exit_code == 2
        assert "Ready GTFS feed not found" in result.output
        filt.mock.assert_not_called()

    def test_non_integer_mode_is_rejected(self, filt):
        result = runner.invoke(cli.app, ["filter-gtfs", "-f", "france", "-m", "bus"])

        assert result.exit_code == 2
        filt.mock.assert_not_called()

    def test_echoes_progress(self, filt):
        result = runner.invoke(
            cli.app, ["filter-gtfs", "-f", "france", "-m", "2", "-m", "3"]
        )

        assert "Filtering GTFS feed 'france' for route type(s): 2, 3" in result.output
        assert "Saved filtered feed to:" in result.output
        assert "france_filtered.gtfs.zip" in result.output


# ---------------------------------------------------------------------------
# extract-network
# ---------------------------------------------------------------------------
class TestExtractNetworkCommand:
    @pytest.fixture
    def extract(self, mocks, dirs, monkeypatch):
        mock = MagicMock()
        monkeypatch.setattr(cli, "extract_pt_network", mock)

        def pbf(*names):
            for name in names:
                (dirs.osm / f"{name}.osm.pbf").write_bytes(b"")

        def pkl(*names):
            dirs.network.mkdir(exist_ok=True)
            for name in names:
                (dirs.network / f"{name}.pkl").write_bytes(b"")

        return SimpleNamespace(mock=mock, dirs=dirs, config=mocks.config, pbf=pbf, pkl=pkl)

    def extracted(self, extract):
        return [c.args[0].name for c in extract.mock.call_args_list]

    # -- selection -------------------------------------------------------

    def test_uses_config_countries_by_default(self, extract):
        extract.pbf("switzerland", "france")

        result = runner.invoke(cli.app, ["extract-network"])

        assert result.exit_code == 0, result.output
        assert self.extracted(extract) == ["switzerland.osm.pbf", "france.osm.pbf"]

    def test_country_option_overrides_config(self, extract):
        extract.pbf("italy", "austria")

        result = runner.invoke(cli.app, ["extract-network", "--country", "italy", "-c", "austria"])

        assert result.exit_code == 0, result.output
        assert self.extracted(extract) == ["italy.osm.pbf", "austria.osm.pbf"]

    def test_pbf_path_comes_from_raw_osm_dir(self, extract):
        extract.pbf("italy")

        runner.invoke(cli.app, ["extract-network", "-c", "italy"])

        extract.mock.assert_called_once_with(extract.dirs.osm / "italy.osm.pbf")

    def test_duplicate_countries_are_extracted_once(self, extract):
        extract.pbf("italy")

        runner.invoke(cli.app, ["extract-network", "-c", "italy", "-c", "italy"])

        assert extract.mock.call_count == 1

    def test_given_order_is_kept(self, extract):
        extract.pbf("italy", "austria", "france")

        runner.invoke(cli.app, ["extract-network", "-c", "france", "-c", "italy", "-c", "austria"])

        assert self.extracted(extract) == ["france.osm.pbf", "italy.osm.pbf", "austria.osm.pbf"]

    def test_custom_extracts_are_not_limited_to_supported_countries(self, extract):
        extract.pbf("grand_lausanne")

        result = runner.invoke(cli.app, ["extract-network", "-c", "grand_lausanne"])

        assert result.exit_code == 0, result.output
        assert self.extracted(extract) == ["grand_lausanne.osm.pbf"]

    # -- existing pickles / overwrite -------------------------------------------

    def test_existing_pickle_is_skipped(self, extract):
        extract.pbf("italy")
        extract.pkl("italy")

        result = runner.invoke(cli.app, ["extract-network", "-c", "italy"])

        assert result.exit_code == 0, result.output
        extract.mock.assert_not_called()
        assert "[italy] Already extracted" in result.output

    def test_only_missing_pickles_are_extracted(self, extract):
        extract.pbf("italy", "austria")
        extract.pkl("italy")

        runner.invoke(cli.app, ["extract-network", "-c", "italy", "-c", "austria"])

        assert self.extracted(extract) == ["austria.osm.pbf"]

    def test_skipped_country_does_not_need_its_pbf(self, extract):
        extract.pkl("italy")  # pickle exists, no PBF on disk

        result = runner.invoke(cli.app, ["extract-network", "-c", "italy"])

        assert result.exit_code == 0, result.output
        extract.mock.assert_not_called()

    @pytest.mark.parametrize("flag", ["--overwrite", "-o"])
    def test_overwrite_reextracts_existing_pickles(self, extract, flag):
        extract.pbf("italy")
        extract.pkl("italy")

        result = runner.invoke(cli.app, ["extract-network", "-c", "italy", flag])

        assert result.exit_code == 0, result.output
        assert self.extracted(extract) == ["italy.osm.pbf"]
        assert "Already extracted" not in result.output

    def test_overwrite_requires_the_pbf(self, extract):
        extract.pkl("italy")  # pickle but no PBF

        result = runner.invoke(cli.app, ["extract-network", "-c", "italy", "-o"])

        assert result.exit_code == 2
        extract.mock.assert_not_called()

    # -- missing PBF -------------------------------------------------------------

    def test_missing_pbf_is_rejected(self, extract):
        result = runner.invoke(cli.app, ["extract-network", "-c", "atlantis"])

        assert result.exit_code == 2
        assert "atlantis" in result.output
        assert "not found" in result.output
        extract.mock.assert_not_called()

    def test_missing_pbf_is_detected_before_any_extraction(self, extract):
        extract.pbf("italy")  # valid, and listed first

        result = runner.invoke(cli.app, ["extract-network", "-c", "italy", "-c", "atlantis"])

        assert result.exit_code == 2
        extract.mock.assert_not_called()

    def test_all_missing_countries_are_reported(self, extract):
        result = runner.invoke(cli.app, ["extract-network", "-c", "atlantis", "-c", "narnia"])

        assert result.exit_code == 2
        assert "atlantis" in result.output
        assert "narnia" in result.output

    def test_default_countries_without_pbf_are_rejected(self, extract):
        result = runner.invoke(cli.app, ["extract-network"])

        assert result.exit_code == 2
        extract.mock.assert_not_called()

    # -- output / failures ---------------------------------------------------------

    def test_echoes_progress_per_country(self, extract):
        extract.pbf("italy")

        result = runner.invoke(cli.app, ["extract-network", "-c", "italy"])

        assert "[italy] Extracting PT network from OSM." in result.output
        assert "[italy] Saved PT network:" in result.output
        assert "italy.pkl" in result.output

    def test_extraction_failure_stops_the_remaining_countries(self, extract):
        extract.pbf("italy", "austria")
        extract.mock.side_effect = RuntimeError("osmium missing")

        result = runner.invoke(cli.app, ["extract-network", "-c", "italy", "-c", "austria"])

        assert result.exit_code != 0
        assert isinstance(result.exception, RuntimeError)
        assert extract.mock.call_count == 1

    def test_does_not_run_osm_or_gtfs_processing(self, extract, mocks):
        extract.pbf("italy")

        runner.invoke(cli.app, ["extract-network", "-c", "italy"])

        mocks.osm.assert_not_called()
        mocks.gtfs.assert_not_called()