from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from tqdm import tqdm
import re
import httpx
import shutil
import time


from gtfsmith import (
    GTFS, filter_gtfs_per_bbox, merge_multiple_gtfs, normalize_swiss_routes,
    merge_duplicated_physical_stops, prefix_stop_ids, add_missing_parent_station, 
    create_parent_stations
    )

from .config import RAW_GTFS_DIR, OTP_DIR, PREPROCESS_DIR, GtfsFeedConfig
from .helpers import get_coordinates

CKAN_API = "https://ckan.opendata.swiss/api/3/action"

RESOURCE_DATE_RE = re.compile(r"(?<!\d)(\d{4})-?(\d{2})-?(\d{2})(?!\d)")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; otp-data-pipeline/1.0)",
    "Accept": "application/json, */*;q=0.8",
}


@dataclass(frozen=True)
class Dataset:
    name: str
    slug: str
    temporal_start: date
    temporal_end: date
    resources: list[dict]


def _parse_temporal_coverage(value: str) -> tuple[date, date]:
    start, end = value.split("/", 1)
    return date.fromisoformat(start), date.fromisoformat(end)


def package_show(slug: str) -> dict:
    response = httpx.get(
        f"{CKAN_API}/package_show",
        params={"id": slug},
        headers=DEFAULT_HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    if not payload.get("success"):
        raise RuntimeError(f"CKAN package_show failed: {payload!r}")

    return payload["result"]

def discover_current_dataset(today: date | None = None) -> Dataset:
    today = today or date.today()

    # The Swiss timetable dataset is named fahrplan-YYYY-gtfs2020.
    slug = f"fahrplan-{today.year}-gtfs2020"

    package = package_show(slug)

    temporal = package.get("temporal_coverage")

    if temporal:
        start, end = _parse_temporal_coverage(temporal)
    else:
        # If CKAN doesn't provide temporal coverage, don't reject
        # an otherwise valid current-year dataset.
        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)

    return Dataset(
        package.get("title", slug),
        slug,
        start,
        end,
        package.get("resources", []),
    )


def _localized_value(value: object, language: str = "en") -> str:
    if isinstance(value, dict):
        return str(
            value.get(language)
            or value.get("de")
            or next(iter(value.values()), "")
        )
    return str(value or "")

def _resource_date(resource: dict) -> date:
    name = _localized_value(resource.get("name"))

    match = RESOURCE_DATE_RE.search(name)
    if not match:
        raise ValueError(f"No date found in resource name: {name!r}")

    year, month, day = match.groups()
    return date(int(year), int(month), int(day))


def find_latest_resource(dataset: Dataset) -> dict:
    resources = [
        r
        for r in dataset.resources
        if str(r.get("format", "")).upper() == "ZIP"
        and r.get("url")
    ]

    if not resources:
        raise RuntimeError(
            f"No downloadable ZIP resources found in {dataset.slug!r}"
        )

    return max(resources, key=_resource_date)


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")

    try:
        with httpx.stream(
            "GET",
            url,
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=None,
        ) as response:
            response.raise_for_status()

            total_size = int(response.headers.get("Content-Length", 0))

            with temporary.open("wb") as file, tqdm(
                total=total_size or None,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc=destination.name,
                dynamic_ncols=True,
            ) as progress:
                for chunk in response.iter_bytes():
                    file.write(chunk)
                    progress.update(len(chunk))

        temporary.replace(destination)

    except Exception:
        temporary.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Providers: each entry resolves one [gtfs.<name>] config block into a
# downloaded file. To support a new kind of source later (a different
# country's open-data portal, another CKAN instance, a plain static URL,
# ...), write a function with this signature and register it -- nothing
# else in this file or in config.py needs to change.
# ---------------------------------------------------------------------------
PROVIDERS: dict[str, Callable[[str, GtfsFeedConfig, bool], Path]] = {}


def register_provider(name: str):
    def decorator(func: Callable[[str, GtfsFeedConfig, bool], Path]) -> Callable[[str, GtfsFeedConfig, bool], Path]:
        PROVIDERS[name] = func
        return func
    return decorator


@register_provider("opentransportdata_ckan")
def _provider_opentransportdata_ckan(
    feed_name: str,
    feed: GtfsFeedConfig,
    overwrite: bool = False
) -> Path:
    """Discover and download the latest matching feed from opentransportdata.swiss's CKAN catalogue."""

    dataset = discover_current_dataset()
    resource = find_latest_resource(dataset)

    resource_name = _localized_value(resource["name"])
    destination = RAW_GTFS_DIR / feed.output_filename

    print(f"[{feed_name}] Timetable dataset: {dataset.slug}")
    print(
        f"[{feed_name}] Coverage: "
        f"{dataset.temporal_start} to {dataset.temporal_end}"
    )
    print(f"[{feed_name}] Latest GTFS resource: {resource_name}")

    if destination.exists() and not overwrite:
        print(f"[{feed_name}] Already downloaded: {destination}")
        return destination

    print(f"[{feed_name}] Downloading: {resource['url']}")
    _download(resource["url"], destination)

    print(f"[{feed_name}] Saved to: {destination}")
    return destination


@register_provider("static_url")
def _provider_static_url(feed_name: str, feed: GtfsFeedConfig, overwrite: bool = False) -> Path:
    """Download a GTFS feed from a fixed URL given as `url` under [gtfs.<name>]."""
    url = feed.options.get("url")
    if not url:
        raise ValueError(f"gtfs.{feed_name} uses provider 'static_url' but has no 'url' set")
    destination = RAW_GTFS_DIR / feed.output_filename

    if destination.exists() and not overwrite:
        print(f"[{feed_name}] Already downloaded: {destination}")
        return destination

    print(f"[{feed_name}] Downloading: {url}")
    _download(url, destination)
    print(f"[{feed_name}] Saved to: {destination}")
    return destination


def download_feed(feed_name: str, feed: GtfsFeedConfig, overwrite: bool = False) -> Path:
    """Download a single GTFS feed as described in config.toml's [gtfs.<feed_name>]."""
    try:
        provider = PROVIDERS[feed.provider]
    except KeyError:
        raise ValueError(
            f"Unknown GTFS provider '{feed.provider}' for gtfs.{feed_name}. "
            f"Available: {', '.join(sorted(PROVIDERS))}"
        )
    return provider(feed_name, feed, overwrite)


def download_all_feed(feeds: dict[str, GtfsFeedConfig], overwrite: bool = False) -> dict[str, Path]:
    """Download every feed in `feeds` (as loaded from config.toml)."""
    return {name: download_feed(name, feed, overwrite=overwrite) for name, feed in feeds.items()}


def clip_all_feeds(
    raw_files: dict[str, Path],
    bbox: tuple[float, float, float, float] | None = None,
    overwrite: bool = False,
) -> dict[str, Path]:
    """Clip all downloaded GTFS feeds and save them in OTP_DIR."""

    OTP_DIR.mkdir(parents=True, exist_ok=True)

    clipped_files: dict[str, Path] = {}

    for name, raw_file in raw_files.items():
        output_file = OTP_DIR / f"{name}.ready.gtfs.zip"

        if output_file.exists() and not overwrite:
            print(f"[{name}] Already clipped: {output_file}")
            clipped_files[name] = output_file
            continue

        if bbox is None:
            print(f"[{name}] No bounding box provided; copying unchanged feed to otp folder")
            shutil.copy2(raw_file, output_file)
        else:
            print(f"[{name}] Clipping GTFS feed")

            filter_gtfs_per_bbox(
                raw_file,
                bbox,
                output_file,
            )

        print(f"[{name}] Saved clipped feed to: {output_file}")
        clipped_files[name] = output_file

    return clipped_files


def preprocess_netherlands(gtfs: GTFS) -> GTFS:
    """Preprocess the Netherlands GTFS feed."""

    print("[netherlands] Geocoding stops without coordinates")

    stops = gtfs.files["stops.txt"]

    for idx, row in tqdm(stops.iterrows(), total=len(stops), desc="Netherlands stops",):
        if row["stop_lat"] == 0.0 and row["stop_lon"] == 0.0:
            lat, lon = get_coordinates(row["stop_name"])

            if lat is not None and lon is not None:
                stops.at[idx, "stop_lat"] = lat
                stops.at[idx, "stop_lon"] = lon

            time.sleep(1)

    gtfs.files["stops.txt"] = stops

    print("[netherlands] Adding prefix to stop IDs")
    gtfs = prefix_stop_ids(gtfs, "NL")

    print("[netherlands] Merging duplicated physical stops")
    gtfs = merge_duplicated_physical_stops(gtfs)

    return gtfs


def preprocess_switzerland(gtfs: GTFS) -> GTFS:
    """Preprocess the Swiss GTFS feed."""

    print("[switzerland] Normalizing routes")
    gtfs = normalize_swiss_routes(gtfs)

    return gtfs


def preprocess_france(gtfs: GTFS) -> GTFS:
    """Preprocess the French GTFS feed."""

    print("[france] Merging duplicated physical stops")
    return merge_duplicated_physical_stops(gtfs)


def preprocess_germany(gtfs: GTFS) -> GTFS:
    """Preprocess the German GTFS feed."""

    print("[germany] Adding prefix to stop IDs")
    return prefix_stop_ids(gtfs, "DE")


def preprocess_belgium(gtfs: GTFS) -> GTFS:
    """Preprocess the Belgian GTFS feed."""

    print("[belgium] Sorting stop times")

    stop_times = gtfs.files["stop_times.txt"]
    gtfs.files["stop_times.txt"] = stop_times.sort_values(["trip_id", "stop_sequence"])

    return gtfs


def preprocess_spain(gtfs: GTFS) -> GTFS:
    """Preprocess the Spanish GTFS feed."""

    print("[spain] Creating parent stations")
    gtfs = create_parent_stations(gtfs)

    print("[spain] Adding prefix to stop IDs")
    gtfs = prefix_stop_ids(gtfs, "ES")

    return gtfs


def preprocess_italy(gtfs: GTFS) -> GTFS:
    """Preprocess the Italian GTFS feed."""

    print("[italy] Creating parent stations")
    return create_parent_stations(gtfs)


def preprocess_austria(gtfs: GTFS) -> GTFS:
    """Preprocess the Austrian GTFS feed."""

    print("[austria] Adding missing parent stations")
    gtfs = add_missing_parent_station(gtfs)

    print("[austria] Merging duplicated physical stops")
    gtfs = merge_duplicated_physical_stops(gtfs)

    return gtfs

def preprocess_belgium(gtfs: GTFS) -> GTFS:
    """Preprocess the Belgian GTFS feed."""

    print("[belguim] Adding prefix to stop IDs")
    gtfs = prefix_stop_ids(gtfs, "BE")

    return gtfs


def preprocess_all_feeds(
    files: dict[str, Path],
    overwrite: bool = False,
) -> dict[str, Path]:
    """Preprocess GTFS feeds and save the results in PREPROCESS_DIR."""

    PREPROCESS_DIR.mkdir(parents=True, exist_ok=True)

    PREPROCESSORS: dict[str, Callable[[GTFS], GTFS]] = {
        "netherlands": preprocess_netherlands,
        "switzerland": preprocess_switzerland,
        "france": preprocess_france,
        "germany": preprocess_germany,
        "belgium": preprocess_belgium,
        "spain": preprocess_spain,
        "italy": preprocess_italy,
        "austria": preprocess_austria,
        "belgium": preprocess_belgium,
    }

    ready_files: dict[str, Path] = {}

    for name, clipped_file in files.items():
        output_file = PREPROCESS_DIR / f"{name}.preprocessed.gtfs.zip"

        if output_file.exists() and not overwrite:
            print(f"[{name}] Already preprocessed: {output_file}")
            ready_files[name] = output_file
            continue

        print(f"[{name}] Loading GTFS feed")

        gtfs = GTFS.from_zip(clipped_file)

        preprocessor = PREPROCESSORS.get(name.lower())

        if preprocessor is not None:
            print(f"[{name}] Applying preprocessing steps")
            gtfs = preprocessor(gtfs)
        else:
            print(f"[{name}] No additional preprocessing required")

        print(f"[{name}] Saving preprocessed GTFS feed")

        gtfs.to_zip(output_file)

        print(f"[{name}] Saved ready feed to: {output_file}")

        ready_files[name] = output_file

    return ready_files



def process(
    feeds: dict[str, GtfsFeedConfig],
    bbox: tuple[float, float, float, float] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """
    Download, clip, preprocess, and merge GTFS feeds.

    Files are stored in the following locations:
    - RAW_GTFS_DIR: original downloads
    - PREPROCESS_DIR: clipped feeds
    - OTP_DIR: preprocessed feeds and the final merged feed
    """

    OTP_DIR.mkdir(parents=True, exist_ok=True)

    print("Downloading GTFS feeds")
    raw_files = download_all_feed(feeds, overwrite=overwrite)

    print("Preprocessing GTFS feeds")
    preprocessed_files = preprocess_all_feeds(raw_files, overwrite=overwrite,)

    print("Clipping GTFS feeds")
    clipped_files = clip_all_feeds(preprocessed_files, bbox=bbox, overwrite=overwrite)

    if not clipped_files:
        raise ValueError("No GTFS feeds were provided.")

    # print("Merging GTFS feeds")
    # output_file = OTP_DIR / "merged.gtfs.zip"

    # files_to_merge = list(ready_files.values())

    # merge_multiple_gtfs(
    #     files_to_merge[0],
    #     files_to_merge[1:],
    #     output_path=output_file,
    # )

    # print(f"Saved merged GTFS feed to: {output_file}")

    # return [output_file]

    return [clipped_files]