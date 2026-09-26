from pathlib import Path
import subprocess
import httpx
from tqdm import tqdm

from .config import RAW_OSM_DIR, OTP_DIR

GEOFABRIK_BASE_URL = "https://download.geofabrik.de/europe"
SUPPORTED_COUNTRIES = {"switzerland", "france", "germany", "italy", "liechtenstein", "austria"}

# Core filter: matches OTP2's own documented recommendation
# (https://docs.opentripplanner.org/Preparing-OSM/) — streets, transit
# platforms, park & ride, turn restrictions and route relations. Everything
# else (buildings, landuse, POIs, ...) is dropped.
OTP_FILTERS = [
    "w/highway",
    "w/public_transport=platform",
    "w/railway=platform",
    "w/park_ride=yes",
    "r/type=restriction",
    "r/type=route",
]


def geofabrik_url(country: str) -> str:
    """
    Build the URL to download the .osm.pbf of the specified country.
    """
    if country not in SUPPORTED_COUNTRIES:
        raise ValueError(f"Unsupported country '{country}'. Supported countries: {', '.join(sorted(SUPPORTED_COUNTRIES))}")
    return f"{GEOFABRIK_BASE_URL}/{country}-latest.osm.pbf"


def raw_path(country: str) -> Path:
    """
    Build the path to the raw/ directory for download.
    """
    return RAW_OSM_DIR / f"{country}.osm.pbf"


def download_country(country: str, overwrite: bool = False) -> Path:
    """
    Download an .osm.pbf of the specified country.

    If overwrite is False (default) and the file already exists, the
    existing file is reused instead of re-downloading. If overwrite is
    True, it is re-downloaded regardless (still via a temp file, so a
    failed download never clobbers the existing good copy).
    """
    url = geofabrik_url(country)
    destination = raw_path(country)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not overwrite:
        print(f"Already downloaded: {destination}")
        return destination
    
    temporary = destination.with_name(destination.name + ".tmp")
    print(f"Downloading {country}...\n  {url}")

    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=None) as response:
            response.raise_for_status()

            total_size = int(response.headers.get("Content-Length", 0))

            with temporary.open("wb") as file:
                with tqdm(
                    total=total_size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=country,
                    dynamic_ncols=True,
                ) as progress:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
                        progress.update(len(chunk))

        temporary.replace(destination)

    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    print(f"  Saved to {destination}")
    return destination


def download(countries: list[str], overwrite: bool = False) -> list[Path]:
    """
    Downloads the .osm.pbf for all the countries.
    """
    return [download_country(c, overwrite=overwrite) for c in countries]


def clip_to_bbox(input_file: Path, output_file: Path, bbox: tuple[float, float, float, float]) -> Path:
    """
    Clip an .osm.pbf to a bounding box (min_lon, min_lat, max_lon, max_lat)
    using `osmium extract`. Keeps every node of any way crossing the
    boundary, so ways aren't left dangling at the edge.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    bbox_str = ",".join(str(v) for v in bbox)
    subprocess.run(
        [
            "osmium", "extract",
            "--strategy", "complete_ways",
            "--bbox", bbox_str,
            str(input_file),
            "-o", str(output_file),
            "-O",
        ],
        check=True,
    )
    return output_file


def filter_for_otp(input_file: Path, output_file: Path) -> Path:
    """
    Filter an .osm.pbf down to what OTP2 needs for routing (streets, transit
    platforms, park & ride, turn restrictions, route relations), stripping
    metadata too, so the file is as light as possible for the graph build.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "osmium", "tags-filter",
            str(input_file),
            *OTP_FILTERS,
            "-o", str(output_file),
            "-f", "pbf,add_metadata=false",
            "-O",
        ],
        check=True,
    )
    return output_file


def process(
    countries: list[str],
    bbox: tuple[float, float, float, float] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """
    Wrapper to download and filter the OSM files for all countries, writing
    one filtered .osm.pbf per country straight into OTP_DIR.

    OTP2 scans its data directory and loads every .osm.pbf file it finds, so
    there is no need to merge the per-country files into one — as long as
    they don't geographically overlap.

    If bbox (min_lon, min_lat, max_lon, max_lat) is given, each country file
    is clipped to it before filtering — useful when you only need a small
    area out of a large country extract.

    overwrite forces a fresh download even if a raw file already exists (see
    download_country); the filter/clip steps always re-run regardless, since
    they're cheap relative to the download.
    """
    OTP_DIR.mkdir(parents=True, exist_ok=True)
    raw_files = download(countries, overwrite=overwrite)

    outputs = []
    for country, raw_file in zip(countries, raw_files):
        working_file = raw_file

        if bbox is not None:
            clipped_file = raw_file.with_name(f"{country}.clipped.osm.pbf")
            working_file = clip_to_bbox(raw_file, clipped_file, bbox)

        output_file = OTP_DIR / f"{country}.osm.pbf"
        filter_for_otp(working_file, output_file)
        outputs.append(output_file)

        if bbox is not None:
            working_file.unlink(missing_ok=True)

    print(f"Final OSM files: {outputs}")
    return outputs