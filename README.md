# otpmill

Downloads and preprocesses the data [OpenTripPlanner](https://www.opentripplanner.org/) (OTP2) needs to build a graph:

- **OSM**: per-country `.osm.pbf` extracts from Geofabrik, optionally clipped to a bounding box and filtered down to what OTP uses (streets, platforms, park & ride, restrictions, route relations).
- **GTFS**: public transport feeds from several sources, preprocessed per country (stop ID prefixes, parent stations, duplicate stop merging, ...) and optionally clipped to a bounding box.

Everything ends up in `data/otp/`, ready to be used as OTP's data directory.

## Requirements

- Python 3.11+ (uses `tomllib`) and [uv](https://docs.astral.sh/uv/)
- [`osmium-tool`](https://osmcode.org/osmium-tool/) available on your `PATH` (used for clipping and filtering OSM data)
- The `gtfsmith` package (GTFS filtering and preprocessing)

```bash
uv sync
```

## Configuration

Everything is configured in `config/config.toml`.

```toml
[osm]
countries = ["switzerland", "france", "germany"]
bbox = [5.9, 45.8, 10.5, 47.8]   # optional: min_lon, min_lat, max_lon, max_lat

# Swiss timetable: the latest ZIP of the current year's dataset is discovered
# automatically on opentransportdata.swiss.
[gtfs.switzerland]
provider = "opentransportdata_ckan"
output_filename = "switzerland.gtfs.zip"

# Any other feed: a fixed URL.
[gtfs.france]
provider = "static_url"
url = "https://example.com/france.zip"
output_filename = "france.gtfs.zip"
```

- `osm.countries` must be supported by `osm.SUPPORTED_COUNTRIES` (Geofabrik names).
- `osm.bbox` is optional. It is applied to both the OSM and the GTFS data. If omitted, data is not clipped.
- Each `[gtfs.<name>]` block needs a `provider`. `output_filename` defaults to `<name>.gtfs.zip`; any other keys are passed to the provider as options (e.g. `url`).
- Available providers: `opentransportdata_ckan` and `static_url`.
- Country-specific GTFS preprocessing is selected by the feed name.

Supported OSM extracts:

- Switzerland
- France
- Germany
- Austria
- Spain
- Italy
- Belgium
- Netherlands
- Luxembourg
- Liechtenstein

Supported GTFS feeds:

- Switzerland
- France (Network SNCF TGV, Intercités et TER)
- Germany
- Austria
- Italy
- Renfe (long-distance)
- Belgium
- Netherlands
- Eurostart

## Command Line

### OSM

Download and process all configured countries:

```bash
uv run otpmill osm
```

Only selected countries, clipped to a bounding box:

```bash
uv run otpmill osm -c switzerland -c france --bbox 5.9,45.8,10.5,47.8
```

Raw extracts are kept in `data/raw/osm/`. Each country is (optionally) clipped and filtered to what OTP needs (streets, platforms, park & ride, restrictions, route relations), and written to `data/otp/<country>.osm.pbf`. The files are not merged, since OTP loads every `.osm.pbf` in its data folder.

Existing raw files are reused; `--overwrite` forces a fresh download. Clipping and filtering always re-run.

### GTFS

```bash
uv run otpmill gtfs
```

Only selected feeds (names as in `config.toml`):

```bash
uv run otpmill gtfs -f switzerland -f germany
```

### Everything

```bash
uv run otpmill all
```

## Python

```python
from otpmill.config import load_config
from otpmill.osm import process as process_osm
from otpmill.gtfs import process as process_gtfs

config = load_config()   # reads config/config.toml

# OSM: returns the list of filtered .osm.pbf files in data/otp/
osm_files = process_osm(
    ["switzerland", "france"],
    bbox=(5.9, 45.8, 10.5, 47.8),   # or None
    overwrite=False,
)

# GTFS: returns {feed name: path of the final .gtfs.zip in data/otp/}
gtfs_files = process_gtfs(
    {name: feed for name, feed in config.gtfs_feeds.items() if name in {"switzerland", "france"}},
    bbox=config.bbox,
    overwrite=False,
)
```

Individual steps can also be used on their own:

```python
from otpmill import osm, gtfs

raw = osm.download_country("austria")                      # data/raw/osm/austria.osm.pbf
osm.clip_to_bbox(raw, Path("clipped.osm.pbf"), (9.5, 46.3, 17.2, 49.0))
osm.filter_for_otp(raw, Path("filtered.osm.pbf"))

raw_gtfs = gtfs.download_all_feed(config.gtfs_feeds)       # {name: data/raw/gtfs/<file>}
preprocessed = gtfs.preprocess_all_feeds(raw_gtfs)         # {name: data/preprocess/<name>.preprocessed.gtfs.zip}
ready = gtfs.clip_all_feeds(preprocessed, bbox=config.bbox)  # {name: data/otp/<name>.ready.gtfs.zip}
```

### Adding a new GTFS source

Write a function that downloads the feed and returns its path, and register it under a provider name. Nothing else needs to change.

```python
from otpmill.gtfs import register_provider

@register_provider("my_portal")
def my_portal(feed_name, feed, overwrite=False):
    url = feed.options["url"]   # extra keys of the [gtfs.<name>] block
    ...
    return destination_path
```

Then use `provider = "my_portal"` in `config.toml`.

### Adding country-specific preprocessing

Add a `preprocess_<country>(gtfs: GTFS) -> GTFS` function in `gtfs.py` and register it in the `PREPROCESSORS` dict inside `preprocess_all_feeds`. The key must match the feed name in `config.toml` (case-insensitive).

## Output layout

```
data/
├── raw/
│   ├── osm/<country>.osm.pbf               # original Geofabrik downloads
│   └── gtfs/<feed>.gtfs.zip                # original GTFS downloads
├── preprocess/
│   └── <feed>.preprocessed.gtfs.zip        # after country-specific preprocessing
└── otp/                                    # <- point OTP at this folder
    ├── <country>.osm.pbf                   # filtered (and clipped) OSM
    └── <feed>.ready.gtfs.zip               # preprocessed and clipped GTFS
```

Existing files are reused rather than rebuilt. For GTFS, `--overwrite` forces every stage (download, preprocess, clip) to run again. For OSM it only forces a fresh download; clipping and filtering always re-run.

## Tests

```bash
uv run pytest
```

The tests use no network access and do not need `osmium`.

## Git

Large downloaded/generated files are ignored with `data/` in `.gitignore`.