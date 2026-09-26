from dataclasses import dataclass
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
class Config:
    osm_countries: list[str]
    osm_bbox: tuple[float, float, float, float] | None
    gtfs_dataset_search_query: str
    gtfs_output_filename: str


def load_config() -> Config:
    with CONFIG_FILE.open("rb") as file:
        data = tomllib.load(file)
    osm = data["osm"]
    gtfs = data["gtfs"]["switzerland"]

    bbox = osm.get("bbox")
    if bbox is not None:
        if len(bbox) != 4:
            raise ValueError(f"osm.bbox must have 4 values (min_lon, min_lat, max_lon, max_lat), got {bbox!r}")
        bbox = tuple(float(v) for v in bbox)

    return Config(
        osm_countries=osm["countries"],
        osm_bbox=bbox,
        gtfs_dataset_search_query=gtfs.get("dataset_search_query", "GTFS2020"),
        gtfs_output_filename=gtfs.get("output_filename", "switzerland.gtfs.zip"),
    )