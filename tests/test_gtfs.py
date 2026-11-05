"""Tests for the gtfs module (CKAN discovery, providers, clip, preprocess, process).

No network access is needed: httpx is faked, and the gtfsmith functions are
replaced by recorders so only *this* package's logic is tested.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from graphmill import gtfs as g
from graphmill.config import GtfsFeedConfig


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
class FakeStreamResponse:
    def __init__(self, chunks=(b"abc", b"def"), headers=None, fail_after=None, status_error=None):
        self.chunks = list(chunks)
        self.headers = headers if headers is not None else {"Content-Length": str(sum(map(len, chunks)))}
        self.fail_after = fail_after
        self.status_error = status_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def iter_bytes(self):
        for i, chunk in enumerate(self.chunks):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("connection dropped")
            yield chunk


class FakeJsonResponse:
    def __init__(self, payload, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._payload


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Redirect every directory constant used by gtfs.py into tmp_path."""
    d = SimpleNamespace(
        raw=tmp_path / "raw" / "gtfs",
        pre=tmp_path / "preprocess",
        ready=tmp_path / "ready",
    )
    monkeypatch.setattr(g, "RAW_GTFS_DIR", d.raw)
    monkeypatch.setattr(g, "PREPROCESS_DIR", d.pre)
    monkeypatch.setattr(g, "OTP_DIR", d.ready)
    return d


@pytest.fixture
def stream(monkeypatch):
    calls = []
    state = {"response": FakeStreamResponse()}

    def fake_stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return state["response"]

    monkeypatch.setattr(g.httpx, "stream", fake_stream)
    return SimpleNamespace(calls=calls, set=lambda r: state.update(response=r))


def make_dataset(resources=None, slug="fahrplan-2026-gtfs2020"):
    return g.Dataset(
        name="Timetable 2026",
        slug=slug,
        temporal_start=date(2025, 12, 14),
        temporal_end=date(2026, 12, 12),
        resources=resources or [],
    )


def zip_resource(name, url="https://example.com/x.zip", fmt="ZIP"):
    return {"name": name, "url": url, "format": fmt}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
class TestParseTemporalCoverage:
    def test_parses_iso_range(self):
        assert g._parse_temporal_coverage("2025-12-14/2026-12-12") == (date(2025, 12, 14), date(2026, 12, 12))

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            g._parse_temporal_coverage("2025-12-14")  # no "/"

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            g._parse_temporal_coverage("nope/2026-12-12")


class TestLocalizedValue:
    def test_prefers_requested_language(self):
        assert g._localized_value({"en": "English", "de": "Deutsch"}) == "English"

    def test_falls_back_to_german(self):
        assert g._localized_value({"de": "Deutsch", "fr": "Français"}) == "Deutsch"

    def test_falls_back_to_first_value(self):
        assert g._localized_value({"fr": "Français"}) == "Français"

    def test_other_language(self):
        assert g._localized_value({"en": "E", "fr": "F"}, language="fr") == "F"

    def test_empty_dict(self):
        assert g._localized_value({}) == ""

    @pytest.mark.parametrize("value,expected", [("plain", "plain"), (None, ""), ("", ""), (42, "42")])
    def test_non_dict_values(self, value, expected):
        assert g._localized_value(value) == expected


class TestResourceDate:
    @pytest.mark.parametrize(
        "name",
        ["gtfs_fp2026_2026-10-28.zip", "gtfs_fp2026_20261028.zip", "Timetable 2026-10-28 (full)"],
    )
    def test_extracts_date(self, name):
        assert g._resource_date({"name": name}) == date(2026, 10, 28)

    def test_localized_name(self):
        assert g._resource_date({"name": {"en": "feed 2026-01-02"}}) == date(2026, 1, 2)

    @pytest.mark.parametrize("name", ["no date here", "", "123456789012"])
    def test_no_date_raises(self, name):
        with pytest.raises(ValueError, match="No date found"):
            g._resource_date({"name": name})

    def test_missing_name_raises(self):
        with pytest.raises(ValueError):
            g._resource_date({})

    def test_impossible_date_raises(self):
        with pytest.raises(ValueError):
            g._resource_date({"name": "2026-13-45"})


# ---------------------------------------------------------------------------
# CKAN: package_show / discover_current_dataset / find_latest_resource
# ---------------------------------------------------------------------------
class TestPackageShow:
    def test_returns_result_and_sends_slug(self, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["url"] = url
            seen["kwargs"] = kwargs
            return FakeJsonResponse({"success": True, "result": {"title": "T"}})

        monkeypatch.setattr(g.httpx, "get", fake_get)

        assert g.package_show("fahrplan-2026-gtfs2020") == {"title": "T"}
        assert seen["url"] == f"{g.CKAN_API}/package_show"
        assert seen["kwargs"]["params"] == {"id": "fahrplan-2026-gtfs2020"}
        assert seen["kwargs"]["headers"] == g.DEFAULT_HEADERS

    def test_unsuccessful_payload_raises(self, monkeypatch):
        monkeypatch.setattr(g.httpx, "get", lambda *a, **k: FakeJsonResponse({"success": False, "error": "x"}))
        with pytest.raises(RuntimeError, match="package_show failed"):
            g.package_show("slug")

    def test_http_error_propagates(self, monkeypatch):
        monkeypatch.setattr(
            g.httpx, "get", lambda *a, **k: FakeJsonResponse({}, status_error=g.httpx.HTTPError("500"))
        )
        with pytest.raises(g.httpx.HTTPError):
            g.package_show("slug")


class TestDiscoverCurrentDataset:
    def test_slug_is_built_from_year(self, monkeypatch):
        requested = []

        def fake_show(slug):
            requested.append(slug)
            return {"title": "TT", "temporal_coverage": "2026-01-01/2026-12-31", "resources": [{"a": 1}]}

        monkeypatch.setattr(g, "package_show", fake_show)

        ds = g.discover_current_dataset(today=date(2026, 5, 17))

        assert requested == ["fahrplan-2026-gtfs2020"]
        assert ds == g.Dataset("TT", "fahrplan-2026-gtfs2020", date(2026, 1, 1), date(2026, 12, 31), [{"a": 1}])

    def test_defaults_to_today(self, monkeypatch):
        requested = []
        monkeypatch.setattr(g, "package_show", lambda slug: requested.append(slug) or {})
        g.discover_current_dataset()
        assert requested == [f"fahrplan-{date.today().year}-gtfs2020"]

    def test_missing_temporal_coverage_falls_back_to_calendar_year(self, monkeypatch):
        monkeypatch.setattr(g, "package_show", lambda slug: {"title": "TT", "resources": []})
        ds = g.discover_current_dataset(today=date(2027, 3, 1))
        assert (ds.temporal_start, ds.temporal_end) == (date(2027, 1, 1), date(2027, 12, 31))

    def test_missing_title_and_resources_use_defaults(self, monkeypatch):
        monkeypatch.setattr(g, "package_show", lambda slug: {})
        ds = g.discover_current_dataset(today=date(2026, 1, 1))
        assert ds.name == "fahrplan-2026-gtfs2020"
        assert ds.resources == []


class TestFindLatestResource:
    def test_picks_most_recent_zip(self):
        old = zip_resource("feed 2026-01-01", url="https://e/old.zip")
        new = zip_resource("feed 2026-10-28", url="https://e/new.zip")
        mid = zip_resource("feed 2026-06-15", url="https://e/mid.zip")
        assert g.find_latest_resource(make_dataset([old, new, mid])) is new

    def test_ignores_non_zip_and_missing_url(self):
        good = zip_resource("feed 2026-01-01")
        newer_but_csv = zip_resource("feed 2026-12-01", fmt="CSV")
        newer_no_url = {"name": "feed 2026-12-02", "format": "ZIP"}
        assert g.find_latest_resource(make_dataset([good, newer_but_csv, newer_no_url])) is good

    def test_format_is_case_insensitive(self):
        res = zip_resource("feed 2026-01-01", fmt="zip")
        assert g.find_latest_resource(make_dataset([res])) is res

    def test_no_zip_raises(self):
        with pytest.raises(RuntimeError, match="No downloadable ZIP resources"):
            g.find_latest_resource(make_dataset([zip_resource("x 2026-01-01", fmt="CSV")]))

    def test_empty_resources_raises(self):
        with pytest.raises(RuntimeError):
            g.find_latest_resource(make_dataset([]))

    def test_zip_without_date_in_name_raises(self):
        with pytest.raises(ValueError):
            g.find_latest_resource(make_dataset([zip_resource("no date")]))

    def test_localized_names(self):
        a = zip_resource({"en": "feed 2026-01-01"})
        b = zip_resource({"de": "feed 2026-02-01"})
        assert g.find_latest_resource(make_dataset([a, b])) is b


# ---------------------------------------------------------------------------
# _download
# ---------------------------------------------------------------------------
class TestDownload:
    def test_writes_file_and_creates_parent_dirs(self, tmp_path, stream):
        dest = tmp_path / "a" / "b" / "feed.zip"
        g._download("https://example.com/feed.zip", dest)
        assert dest.read_bytes() == b"abcdef"
        assert not list(dest.parent.glob("*.tmp"))

    def test_sends_headers_and_follows_redirects(self, tmp_path, stream):
        g._download("https://example.com/feed.zip", tmp_path / "f.zip")
        method, url, kwargs = stream.calls[0]
        assert (method, url) == ("GET", "https://example.com/feed.zip")
        assert kwargs["headers"] == g.DEFAULT_HEADERS
        assert kwargs["follow_redirects"] is True

    def test_missing_content_length(self, tmp_path, stream):
        stream.set(FakeStreamResponse(headers={}))
        dest = tmp_path / "f.zip"
        g._download("https://example.com", dest)
        assert dest.read_bytes() == b"abcdef"

    def test_http_error_leaves_nothing(self, tmp_path, stream):
        stream.set(FakeStreamResponse(status_error=g.httpx.HTTPError("404")))
        dest = tmp_path / "f.zip"
        with pytest.raises(g.httpx.HTTPError):
            g._download("https://example.com", dest)
        assert not dest.exists()
        assert not list(tmp_path.glob("*.tmp"))

    def test_mid_download_failure_keeps_existing_file_and_cleans_tmp(self, tmp_path, stream):
        dest = tmp_path / "f.zip"
        dest.write_bytes(b"good")
        stream.set(FakeStreamResponse(chunks=[b"a", b"b"], fail_after=1))
        with pytest.raises(RuntimeError):
            g._download("https://example.com", dest)
        assert dest.read_bytes() == b"good"
        assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class TestProviderRegistry:
    def test_builtin_providers_registered(self):
        assert {"opentransportdata_ckan", "static_url"} <= set(g.PROVIDERS)

    def test_register_provider_decorator_registers_and_returns_function(self, monkeypatch):
        monkeypatch.setattr(g, "PROVIDERS", dict(g.PROVIDERS))

        @g.register_provider("custom")
        def custom(name, feed, overwrite=False):
            return Path("x")

        assert g.PROVIDERS["custom"] is custom
        assert custom("n", None) == Path("x")

    def test_download_feed_dispatches_to_provider(self, monkeypatch):
        calls = []

        def fake(feed_name, feed, overwrite):
            calls.append((feed_name, feed, overwrite))
            return Path("result.zip")

        monkeypatch.setitem(g.PROVIDERS, "fake", fake)
        feed = GtfsFeedConfig(provider="fake", output_filename="x.zip")

        assert g.download_feed("name", feed, overwrite=True) == Path("result.zip")
        assert calls == [("name", feed, True)]

    def test_download_feed_unknown_provider(self):
        feed = GtfsFeedConfig(provider="nope", output_filename="x.zip")
        with pytest.raises(ValueError, match="Unknown GTFS provider 'nope'") as exc:
            g.download_feed("name", feed)
        assert "static_url" in str(exc.value)

    def test_download_all_feed(self, monkeypatch):
        seen = []

        def fake_download_feed(name, feed, overwrite=False):
            seen.append((name, overwrite))
            return Path(f"{name}.zip")

        monkeypatch.setattr(g, "download_feed", fake_download_feed)
        feeds = {
            "a": GtfsFeedConfig("static_url", "a.zip"),
            "b": GtfsFeedConfig("static_url", "b.zip"),
        }

        assert g.download_all_feed(feeds, overwrite=True) == {"a": Path("a.zip"), "b": Path("b.zip")}
        assert seen == [("a", True), ("b", True)]


class TestStaticUrlProvider:
    def feed(self, **options):
        return GtfsFeedConfig("static_url", "france.gtfs.zip", options)

    def test_downloads_to_raw_dir(self, dirs, monkeypatch):
        calls = []
        monkeypatch.setattr(g, "_download", lambda url, dest: calls.append((url, dest)))

        result = g.PROVIDERS["static_url"]("france", self.feed(url="https://e/f.zip"))

        assert result == dirs.raw / "france.gtfs.zip"
        assert calls == [("https://e/f.zip", dirs.raw / "france.gtfs.zip")]

    def test_missing_url_raises(self, dirs):
        with pytest.raises(ValueError, match="no 'url' set"):
            g.PROVIDERS["static_url"]("france", self.feed())

    def test_existing_file_is_not_redownloaded(self, dirs, monkeypatch):
        dirs.raw.mkdir(parents=True)
        (dirs.raw / "france.gtfs.zip").write_bytes(b"old")
        calls = []
        monkeypatch.setattr(g, "_download", lambda *a: calls.append(a))

        result = g.PROVIDERS["static_url"]("france", self.feed(url="https://e/f.zip"))

        assert result == dirs.raw / "france.gtfs.zip"
        assert calls == []

    def test_overwrite_redownloads(self, dirs, monkeypatch):
        dirs.raw.mkdir(parents=True)
        (dirs.raw / "france.gtfs.zip").write_bytes(b"old")
        calls = []
        monkeypatch.setattr(g, "_download", lambda *a: calls.append(a))

        g.PROVIDERS["static_url"]("france", self.feed(url="https://e/f.zip"), overwrite=True)

        assert len(calls) == 1


class TestCkanProvider:
    feed = GtfsFeedConfig("opentransportdata_ckan", "switzerland.gtfs.zip", {"dataset_search_query": "GTFS2020"})

    @pytest.fixture
    def ckan(self, monkeypatch):
        resources = [
            zip_resource("feed 2026-01-01", url="https://e/old.zip"),
            zip_resource("feed 2026-10-28", url="https://e/new.zip"),
        ]
        monkeypatch.setattr(g, "discover_current_dataset", lambda: make_dataset(resources))
        calls = []
        monkeypatch.setattr(g, "_download", lambda url, dest: calls.append((url, dest)))
        return calls

    def test_downloads_latest_resource(self, dirs, ckan):
        result = g.PROVIDERS["opentransportdata_ckan"]("switzerland", self.feed)
        assert result == dirs.raw / "switzerland.gtfs.zip"
        assert ckan == [("https://e/new.zip", dirs.raw / "switzerland.gtfs.zip")]

    def test_existing_file_is_kept(self, dirs, ckan):
        dirs.raw.mkdir(parents=True)
        (dirs.raw / "switzerland.gtfs.zip").write_bytes(b"old")
        result = g.PROVIDERS["opentransportdata_ckan"]("switzerland", self.feed)
        assert result == dirs.raw / "switzerland.gtfs.zip"
        assert ckan == []

    def test_overwrite(self, dirs, ckan):
        dirs.raw.mkdir(parents=True)
        (dirs.raw / "switzerland.gtfs.zip").write_bytes(b"old")
        g.PROVIDERS["opentransportdata_ckan"]("switzerland", self.feed, overwrite=True)
        assert len(ckan) == 1


# ---------------------------------------------------------------------------
# clip_all_feeds
# ---------------------------------------------------------------------------
class TestClipAllFeeds:
    @pytest.fixture
    def raw_files(self, tmp_path):
        files = {}
        for name in ("switzerland", "france"):
            p = tmp_path / f"{name}.pre.zip"
            p.write_bytes(name.encode())
            files[name] = p
        return files

    def test_without_bbox_copies_files(self, dirs, raw_files):
        result = g.clip_all_feeds(raw_files)
        assert result == {
            "switzerland": dirs.ready / "switzerland.ready.gtfs.zip",
            "france": dirs.ready / "france.ready.gtfs.zip",
        }
        assert result["france"].read_bytes() == b"france"

    def test_with_bbox_calls_filter(self, dirs, raw_files, monkeypatch):
        calls = []

        def fake_filter(src, bbox, out):
            calls.append((src, bbox, out))
            Path(out).write_bytes(b"clipped")

        monkeypatch.setattr(g, "filter_gtfs_per_bbox", fake_filter)
        bbox = (6.0, 46.0, 7.0, 47.0)

        result = g.clip_all_feeds(raw_files, bbox=bbox)

        assert calls == [
            (raw_files["switzerland"], bbox, dirs.ready / "switzerland.ready.gtfs.zip"),
            (raw_files["france"], bbox, dirs.ready / "france.ready.gtfs.zip"),
        ]
        assert result["switzerland"].read_bytes() == b"clipped"

    def test_existing_output_is_skipped(self, dirs, raw_files, monkeypatch):
        dirs.ready.mkdir(parents=True)
        (dirs.ready / "switzerland.ready.gtfs.zip").write_bytes(b"already")
        calls = []
        monkeypatch.setattr(g, "filter_gtfs_per_bbox", lambda *a: calls.append(a))

        result = g.clip_all_feeds({"switzerland": raw_files["switzerland"]}, bbox=(1, 2, 3, 4))

        assert calls == []
        assert result["switzerland"].read_bytes() == b"already"

    def test_overwrite_reprocesses(self, dirs, raw_files):
        dirs.ready.mkdir(parents=True)
        (dirs.ready / "switzerland.ready.gtfs.zip").write_bytes(b"already")

        result = g.clip_all_feeds({"switzerland": raw_files["switzerland"]}, overwrite=True)

        assert result["switzerland"].read_bytes() == b"switzerland"

    def test_empty_input(self, dirs):
        assert g.clip_all_feeds({}) == {}
        assert dirs.ready.is_dir()


# ---------------------------------------------------------------------------
# Country preprocessors
# ---------------------------------------------------------------------------
@pytest.fixture
def recorder(monkeypatch):
    """Replace the gtfsmith helpers imported in gtfs.py with call recorders."""
    calls = []

    def make(name):
        def fn(gtfs, *args):
            calls.append((name, *args))
            return gtfs

        return fn

    for name in (
        "normalize_swiss_routes",
        "merge_duplicated_physical_stops",
        "prefix_stop_ids",
        "add_missing_parent_station",
        "create_parent_stations",
    ):
        monkeypatch.setattr(g, name, make(name))
    return calls


@pytest.mark.parametrize(
    "func,expected",
    [
        ("preprocess_switzerland", [("normalize_swiss_routes",)]),
        ("preprocess_france", [("merge_duplicated_physical_stops",)]),
        ("preprocess_germany", [("prefix_stop_ids", "DE")]),
        ("preprocess_belgium", [("prefix_stop_ids", "BE")]),
        ("preprocess_italy", [("create_parent_stations",)]),
        ("preprocess_spain", [("create_parent_stations",), ("prefix_stop_ids", "ES")]),
        (
            "preprocess_austria",
            [("add_missing_parent_station",), ("merge_duplicated_physical_stops",)],
        ),
    ],
)
def test_country_preprocessors_apply_expected_steps(recorder, func, expected):
    gtfs_obj = object()
    result = getattr(g, func)(gtfs_obj)
    assert result is gtfs_obj
    assert recorder == expected


class TestPreprocessNetherlands:
    @pytest.fixture
    def pd(self):
        return pytest.importorskip("pandas")

    @pytest.fixture
    def fake_gtfs(self, pd):
        stops = pd.DataFrame(
            {
                "stop_id": ["1", "2", "3"],
                "stop_name": ["Amsterdam", "Nowhere", "Utrecht"],
                "stop_lat": [0.0, 0.0, 52.09],
                "stop_lon": [0.0, 0.0, 5.12],
            }
        )
        return SimpleNamespace(files={"stops.txt": stops})

    def test_geocodes_only_stops_without_coordinates(self, fake_gtfs, recorder, monkeypatch):
        lookups = []

        def fake_coords(name):
            lookups.append(name)
            return {"Amsterdam": (52.37, 4.89)}.get(name, (None, None))

        monkeypatch.setattr(g, "get_coordinates", fake_coords)
        monkeypatch.setattr(g.time, "sleep", lambda s: None)

        g.preprocess_netherlands(fake_gtfs)

        stops = fake_gtfs.files["stops.txt"]
        assert lookups == ["Amsterdam", "Nowhere"]
        assert (stops.loc[0, "stop_lat"], stops.loc[0, "stop_lon"]) == (52.37, 4.89)
        # Geocoding failed -> coordinates stay at 0,0
        assert (stops.loc[1, "stop_lat"], stops.loc[1, "stop_lon"]) == (0.0, 0.0)
        # Already had coordinates -> untouched
        assert (stops.loc[2, "stop_lat"], stops.loc[2, "stop_lon"]) == (52.09, 5.12)

    def test_prefixes_then_merges(self, fake_gtfs, recorder, monkeypatch):
        monkeypatch.setattr(g, "get_coordinates", lambda name: (None, None))
        monkeypatch.setattr(g.time, "sleep", lambda s: None)

        result = g.preprocess_netherlands(fake_gtfs)

        assert result is fake_gtfs
        assert recorder == [("prefix_stop_ids", "NL"), ("merge_duplicated_physical_stops",)]

    def test_rate_limits_geocoding(self, fake_gtfs, recorder, monkeypatch):
        sleeps = []
        monkeypatch.setattr(g, "get_coordinates", lambda name: (None, None))
        monkeypatch.setattr(g.time, "sleep", lambda s: sleeps.append(s))

        g.preprocess_netherlands(fake_gtfs)

        assert sleeps == [1, 1]  # one per geocoded stop


# ---------------------------------------------------------------------------
# preprocess_all_feeds
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_gtfs_class(monkeypatch):
    log = SimpleNamespace(loaded=[], exported=[])

    class FakeGTFS:
        @classmethod
        def from_zip(cls, path):
            inst = cls()
            inst.source = path
            inst.steps = []
            log.loaded.append(path)
            return inst

        def export_gtfs(self, path):
            log.exported.append((self, path))
            Path(path).write_bytes(b"exported")

    monkeypatch.setattr(g, "GTFS", FakeGTFS)
    return log


class TestPreprocessAllFeeds:
    @pytest.fixture
    def inputs(self, tmp_path):
        files = {}
        for name in ("switzerland", "eurostar"):
            p = tmp_path / f"{name}.zip"
            p.write_bytes(b"x")
            files[name] = p
        return files

    def test_applies_known_preprocessor_and_exports(self, dirs, inputs, fake_gtfs_class, monkeypatch):
        def fake_ch(gtfs):
            gtfs.steps.append("ch")
            return gtfs

        monkeypatch.setattr(g, "preprocess_switzerland", fake_ch)

        result = g.preprocess_all_feeds({"switzerland": inputs["switzerland"]})

        out = dirs.pre / "switzerland.preprocessed.gtfs.zip"
        assert result == {"switzerland": out}
        assert out.exists()
        (gtfs_obj, exported_path), = fake_gtfs_class.exported
        assert gtfs_obj.steps == ["ch"]
        assert exported_path == out

    def test_feed_without_preprocessor_is_still_exported(self, dirs, inputs, fake_gtfs_class):
        result = g.preprocess_all_feeds({"eurostar": inputs["eurostar"]})
        assert result["eurostar"] == dirs.pre / "eurostar.preprocessed.gtfs.zip"
        assert result["eurostar"].exists()
        (gtfs_obj, _), = fake_gtfs_class.exported
        assert gtfs_obj.steps == []

    def test_preprocessor_lookup_is_case_insensitive(self, dirs, tmp_path, fake_gtfs_class, monkeypatch):
        called = []
        monkeypatch.setattr(g, "preprocess_france", lambda gtfs: called.append(1) or gtfs)
        p = tmp_path / "f.zip"
        p.write_bytes(b"x")

        g.preprocess_all_feeds({"France": p})

        assert called == [1]

    def test_existing_output_is_skipped(self, dirs, inputs, fake_gtfs_class):
        dirs.pre.mkdir(parents=True)
        out = dirs.pre / "eurostar.preprocessed.gtfs.zip"
        out.write_bytes(b"already")

        result = g.preprocess_all_feeds({"eurostar": inputs["eurostar"]})

        assert result == {"eurostar": out}
        assert fake_gtfs_class.loaded == []
        assert out.read_bytes() == b"already"

    def test_overwrite_reprocesses(self, dirs, inputs, fake_gtfs_class):
        dirs.pre.mkdir(parents=True)
        out = dirs.pre / "eurostar.preprocessed.gtfs.zip"
        out.write_bytes(b"already")

        g.preprocess_all_feeds({"eurostar": inputs["eurostar"]}, overwrite=True)

        assert out.read_bytes() == b"exported"

    def test_empty_input(self, dirs, fake_gtfs_class):
        assert g.preprocess_all_feeds({}) == {}
        assert dirs.pre.is_dir()


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------
@pytest.fixture
def stages(dirs, monkeypatch):
    """Replace the three pipeline stages with recorders."""
    order = []
    raw = {"switzerland": Path("raw/ch.zip")}
    pre = {"switzerland": Path("pre/ch.zip")}
    clipped = {"switzerland": Path("ready/ch.zip")}
    args = {}

    def fake_download(feeds, overwrite=False):
        order.append("download")
        args["download"] = (feeds, overwrite)
        return raw

    def fake_preprocess(files, overwrite=False):
        order.append("preprocess")
        args["preprocess"] = (files, overwrite)
        return pre

    def fake_clip(files, bbox=None, overwrite=False):
        order.append("clip")
        args["clip"] = (files, bbox, overwrite)
        return clipped

    monkeypatch.setattr(g, "download_all_feed", fake_download)
    monkeypatch.setattr(g, "preprocess_all_feeds", fake_preprocess)
    monkeypatch.setattr(g, "clip_all_feeds", fake_clip)
    return SimpleNamespace(order=order, raw=raw, pre=pre, clipped=clipped, args=args)


class TestProcess:
    feeds = {"switzerland": GtfsFeedConfig("static_url", "switzerland.gtfs.zip", {"url": "u"})}

    def test_runs_download_preprocess_clip_in_order(self, stages):
        g.process(self.feeds)
        assert stages.order == ["download", "preprocess", "clip"]

    def test_each_stage_receives_previous_stage_output(self, stages):
        bbox = (1.0, 2.0, 3.0, 4.0)

        g.process(self.feeds, bbox=bbox, overwrite=True)

        assert stages.args["download"] == (self.feeds, True)
        assert stages.args["preprocess"] == (stages.raw, True)
        assert stages.args["clip"] == (stages.pre, bbox, True)

    def test_creates_ready_dir(self, stages, dirs):
        g.process(self.feeds)
        assert dirs.ready.is_dir()

    def test_no_feeds_raises(self, stages):
        stages.clipped.clear()
        with pytest.raises(ValueError, match="No GTFS feeds were provided"):
            g.process({})

    def test_returns_dict_of_clipped_paths(self, stages):
        result = g.process(self.feeds)
        assert isinstance(result, dict)
        assert result == stages.clipped
        assert all(isinstance(p, Path) for p in result.values())