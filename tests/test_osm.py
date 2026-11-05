"""Tests for the osm module (Geofabrik download + osmium clip/filter).

No network access and no osmium binary are needed: httpx.stream and
subprocess.run are replaced with fakes.
"""
from __future__ import annotations

import httpx
import pytest

from graphmill import osm


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
class FakeResponse:
    """Minimal stand-in for the context manager returned by httpx.stream()."""

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


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    raw = tmp_path / "raw" / "osm"
    ready = tmp_path / "ready"
    monkeypatch.setattr(osm, "RAW_OSM_DIR", raw)
    monkeypatch.setattr(osm, "OTP_DIR", ready)
    return raw, ready


@pytest.fixture
def stream(monkeypatch):
    """Install a fake httpx.stream. Returns a (calls, set_response) pair."""
    calls = []
    state = {"response": FakeResponse()}

    def fake_stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return state["response"]

    monkeypatch.setattr(osm.httpx, "stream", fake_stream)

    def set_response(resp):
        state["response"] = resp

    return calls, set_response


@pytest.fixture
def run(monkeypatch):
    """Replace subprocess.run in osm and record the calls."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

    monkeypatch.setattr(osm.subprocess, "run", fake_run)
    return calls


# ---------------------------------------------------------------------------
# geofabrik_url / raw_path
# ---------------------------------------------------------------------------
class TestUrlsAndPaths:
    @pytest.mark.parametrize("country", sorted(osm.SUPPORTED_COUNTRIES))
    def test_url_for_supported_countries(self, country):
        assert osm.geofabrik_url(country) == f"https://download.geofabrik.de/europe/{country}-latest.osm.pbf"

    @pytest.mark.parametrize("country", ["atlantis", "", "Switzerland"])
    def test_unsupported_country_raises(self, country):
        with pytest.raises(ValueError, match="Unsupported country"):
            osm.geofabrik_url(country)

    def test_error_message_lists_supported_countries(self):
        with pytest.raises(ValueError) as exc:
            osm.geofabrik_url("atlantis")
        for country in osm.SUPPORTED_COUNTRIES:
            assert country in str(exc.value)

    def test_raw_path(self, dirs):
        raw, _ = dirs
        assert osm.raw_path("france") == raw / "france.osm.pbf"


class TestOtpFilters:
    def test_filters_cover_what_otp_needs(self):
        assert "w/highway" in osm.OTP_FILTERS
        assert "w/public_transport=platform" in osm.OTP_FILTERS
        assert "w/railway=platform" in osm.OTP_FILTERS
        assert "r/type=restriction" in osm.OTP_FILTERS
        assert "r/type=route" in osm.OTP_FILTERS


# ---------------------------------------------------------------------------
# download_country
# ---------------------------------------------------------------------------
class TestDownloadCountry:
    def test_downloads_file_and_creates_directories(self, dirs, stream):
        raw, _ = dirs
        calls, _ = stream

        result = osm.download_country("switzerland")

        assert result == raw / "switzerland.osm.pbf"
        assert result.read_bytes() == b"abcdef"
        assert len(calls) == 1
        method, url, kwargs = calls[0]
        assert method == "GET"
        assert url == "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf"
        assert kwargs["follow_redirects"] is True

    def test_no_temporary_file_left_behind(self, dirs, stream):
        raw, _ = dirs
        osm.download_country("switzerland")
        assert not list(raw.glob("*.tmp"))

    def test_works_without_content_length_header(self, dirs, stream):
        _, set_response = stream
        set_response(FakeResponse(headers={}))
        result = osm.download_country("austria")
        assert result.read_bytes() == b"abcdef"

    def test_existing_file_is_reused(self, dirs, stream):
        raw, _ = dirs
        calls, _ = stream
        raw.mkdir(parents=True)
        existing = raw / "france.osm.pbf"
        existing.write_bytes(b"old")

        result = osm.download_country("france")

        assert result == existing
        assert existing.read_bytes() == b"old"
        assert calls == []

    def test_overwrite_redownloads(self, dirs, stream):
        raw, _ = dirs
        calls, _ = stream
        raw.mkdir(parents=True)
        (raw / "france.osm.pbf").write_bytes(b"old")

        result = osm.download_country("france", overwrite=True)

        assert result.read_bytes() == b"abcdef"
        assert len(calls) == 1

    def test_unsupported_country_raises_before_any_request(self, dirs, stream):
        calls, _ = stream
        with pytest.raises(ValueError):
            osm.download_country("atlantis")
        assert calls == []

    def test_http_error_propagates_and_leaves_nothing(self, dirs, stream):
        raw, _ = dirs
        _, set_response = stream
        set_response(FakeResponse(status_error=httpx.HTTPError("404")))

        with pytest.raises(httpx.HTTPError):
            osm.download_country("germany")

        assert not (raw / "germany.osm.pbf").exists()
        assert not list(raw.glob("*.tmp"))

    def test_failed_download_cleans_tmp_and_keeps_existing_good_copy(self, dirs, stream):
        raw, _ = dirs
        _, set_response = stream
        raw.mkdir(parents=True)
        good = raw / "italy.osm.pbf"
        good.write_bytes(b"good copy")
        set_response(FakeResponse(chunks=[b"aaa", b"bbb"], fail_after=1))

        with pytest.raises(RuntimeError, match="connection dropped"):
            osm.download_country("italy", overwrite=True)

        assert good.read_bytes() == b"good copy"
        assert not list(raw.glob("*.tmp"))

    def test_failed_first_download_leaves_no_destination(self, dirs, stream):
        raw, _ = dirs
        _, set_response = stream
        set_response(FakeResponse(chunks=[b"aaa", b"bbb"], fail_after=1))

        with pytest.raises(RuntimeError):
            osm.download_country("italy")

        assert not (raw / "italy.osm.pbf").exists()
        assert not list(raw.glob("*.tmp"))


class TestDownload:
    def test_downloads_every_country_in_order(self, dirs, stream):
        raw, _ = dirs
        calls, _ = stream

        paths = osm.download(["switzerland", "france"])

        assert paths == [raw / "switzerland.osm.pbf", raw / "france.osm.pbf"]
        assert [c[1] for c in calls] == [
            "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf",
            "https://download.geofabrik.de/europe/france-latest.osm.pbf",
        ]

    def test_overwrite_is_forwarded(self, dirs, stream):
        raw, _ = dirs
        calls, _ = stream
        raw.mkdir(parents=True)
        (raw / "france.osm.pbf").write_bytes(b"old")

        osm.download(["france"], overwrite=True)
        assert len(calls) == 1

    def test_empty_list(self, dirs, stream):
        assert osm.download([]) == []


# ---------------------------------------------------------------------------
# clip_to_bbox / filter_for_otp
# ---------------------------------------------------------------------------
class TestClipToBbox:
    def test_builds_osmium_extract_command(self, tmp_path, run):
        src = tmp_path / "in.osm.pbf"
        out = tmp_path / "sub" / "out.osm.pbf"

        result = osm.clip_to_bbox(src, out, (5.0, 45.0, 11.0, 48.0))

        assert result == out
        assert out.parent.is_dir()
        (cmd, kwargs), = run
        assert cmd[:2] == ["osmium", "extract"]
        assert cmd[cmd.index("--strategy") + 1] == "complete_ways"
        assert cmd[cmd.index("--bbox") + 1] == "5.0,45.0,11.0,48.0"
        assert str(src) in cmd
        assert cmd[cmd.index("-o") + 1] == str(out)
        assert "-O" in cmd
        assert kwargs.get("check") is True

    def test_osmium_failure_propagates(self, tmp_path, monkeypatch):
        import subprocess

        def boom(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(osm.subprocess, "run", boom)
        with pytest.raises(subprocess.CalledProcessError):
            osm.clip_to_bbox(tmp_path / "a", tmp_path / "b", (1, 2, 3, 4))


class TestFilterForOtp:
    def test_builds_osmium_tags_filter_command(self, tmp_path, run):
        src = tmp_path / "in.osm.pbf"
        out = tmp_path / "nested" / "out.osm.pbf"

        result = osm.filter_for_otp(src, out)

        assert result == out
        assert out.parent.is_dir()
        (cmd, kwargs), = run
        assert cmd[:2] == ["osmium", "tags-filter"]
        assert cmd[2] == str(src)
        for f in osm.OTP_FILTERS:
            assert f in cmd
        assert cmd[cmd.index("-o") + 1] == str(out)
        assert cmd[cmd.index("-f") + 1] == "pbf,add_metadata=false"
        assert "-O" in cmd
        assert kwargs.get("check") is True


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------
@pytest.fixture
def pipeline(dirs, monkeypatch):
    """Fake out download / clip / filter so process() can be tested in isolation."""
    raw, ready = dirs
    log = {"download": [], "clip": [], "filter": []}

    def fake_download(countries, overwrite=False):
        log["download"].append((list(countries), overwrite))
        raw.mkdir(parents=True, exist_ok=True)
        paths = []
        for c in countries:
            p = raw / f"{c}.osm.pbf"
            p.write_bytes(b"raw")
            paths.append(p)
        return paths

    def fake_clip(inp, out, bbox):
        log["clip"].append((inp, out, bbox))
        out.write_bytes(b"clipped")
        return out

    def fake_filter(inp, out):
        log["filter"].append((inp, out))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"filtered")
        return out

    monkeypatch.setattr(osm, "download", fake_download)
    monkeypatch.setattr(osm, "clip_to_bbox", fake_clip)
    monkeypatch.setattr(osm, "filter_for_otp", fake_filter)
    return raw, ready, log


class TestProcess:
    def test_without_bbox_filters_raw_files_directly(self, pipeline):
        raw, ready, log = pipeline

        outputs = osm.process(["switzerland", "france"])

        assert outputs == [ready / "switzerland.osm.pbf", ready / "france.osm.pbf"]
        assert log["clip"] == []
        assert [i for i, _ in log["filter"]] == [raw / "switzerland.osm.pbf", raw / "france.osm.pbf"]
        for out in outputs:
            assert out.exists()

    def test_with_bbox_clips_then_filters_and_removes_clipped_file(self, pipeline):
        raw, ready, log = pipeline
        bbox = (6.0, 46.0, 7.0, 47.0)

        outputs = osm.process(["switzerland"], bbox=bbox)

        clipped = raw / "switzerland.clipped.osm.pbf"
        assert log["clip"] == [(raw / "switzerland.osm.pbf", clipped, bbox)]
        assert log["filter"] == [(clipped, ready / "switzerland.osm.pbf")]
        assert outputs == [ready / "switzerland.osm.pbf"]
        assert not clipped.exists()
        # The original download must be kept.
        assert (raw / "switzerland.osm.pbf").exists()

    def test_overwrite_is_forwarded_to_download(self, pipeline):
        _, _, log = pipeline
        osm.process(["austria"], overwrite=True)
        assert log["download"] == [(["austria"], True)]

    def test_creates_ready_dir(self, pipeline):
        _, ready, _ = pipeline
        assert not ready.exists()
        osm.process(["austria"])
        assert ready.is_dir()

    def test_empty_country_list(self, pipeline):
        assert osm.process([]) == []