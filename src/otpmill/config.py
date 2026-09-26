from dataclasses import dataclass, field
from pathlib import Path
import tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT_ROOT / "config" / "config.toml"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
OTP_DIR = DATA_DIR / "otp"
RAW_OSM_DIR = RAW_DIR / "osm"
RAW_GTFS_DIR = RAW_DIR / "gtfs"


@dataclass(frozen=True)
class GtfsFeedConfig:
    """
    Config for one [gtfs.<name>] entry. `provider` selects which downloader
    in gtfs.py handles it (see gtfs.PROVIDERS); `options` carries whatever
    other keys that provider needs (e.g. dataset_search_query, or a static
    url), so adding a new provider never requires changing this dataclass.
    """
    provider: str
    output_filename: str
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    osm_countries: list[str]
    osm_bbox: tuple[float, float, float, float] | None
    gtfs_feeds: dict[str, GtfsFeedConfig]


def load_config() -> Config:
    with CONFIG_FILE.open("rb") as file:
        data = tomllib.load(file)
    osm = data["osm"]

    bbox = osm.get("bbox")
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError(f"osm.bbox must have 4 values (min_lon, min_lat, max_lon, max_lat), got {bbox!r}")
        bbox = tuple(float(v) for v in bbox)

    gtfs_feeds = {}
    for name, values in data.get("gtfs", {}).items():
        values = dict(values)
        provider = values.pop("provider", None)
        if not provider:
            raise ValueError(f"gtfs.{name} is missing a 'provider' key")
        output_filename = values.pop("output_filename", f"{name}.gtfs.zip")
        gtfs_feeds[name] = GtfsFeedConfig(provider=provider, output_filename=output_filename, options=values)

    return Config(
        osm_countries=osm["countries"],
        osm_bbox=bbox,
        gtfs_feeds=gtfs_feeds,
    )