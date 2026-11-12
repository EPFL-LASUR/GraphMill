# GraphMill

Downloads and preprocesses GTFS and OSM data that routing machines such as [OpenTripPlanner](https://www.opentripplanner.org/) (OTP2), [OSRM](https://project-osrm.org/) or [MOTIS](https://github.com/motis-project/motis) need to build a routing graph:

- **OSM**: Downloads per-country `.osm.pbf` extracts from Geofabrik, optionally clips them to a bounding box, and filters them to retain the data needed by OTP (streets, platforms, park & ride, restrictions and route relations).
- **GTFS**: Downloads public transport feeds from several sources, preprocesses them per country (stop ID prefixes, parent stations, duplicate stop merging, etc.), and optionally clips them to a bounding box.
- **GTFS shapes**: Generates missing GTFS shapes using one or more public transport networks extracted from OSM.

The processed data is stored in `data/ready/`, ready to be used by a routing engine.

## Requirements

- Python 3.11+ (uses `tomllib`) and [uv](https://docs.astral.sh/uv/).
- [`osmium-tool`](https://osmcode.org/osmium-tool/) available on your `PATH` (used for clipping and filtering OSM data).
- The `gtfsmith` package (GTFS filtering and preprocessing).

Install dependencies:

```bash
uv sync
```

## Configuration

Everything is configured in `config/config.toml`.

### OSM

```toml
[osm]
countries = ["switzerland", "france", "germany"]
bbox = [5.9, 45.8, 10.5, 47.8]  # optional: min_lon, min_lat, max_lon, max_lat
```

- `osm.countries` specifies the countries to download. Names must be supported by `osm.SUPPORTED_COUNTRIES`.
- `osm.bbox` is optional and is applied to both OSM and GTFS data. If omitted, data is not clipped.
- The command-line `--bbox` option overrides the configured bounding box.

### GTFS

Each GTFS feed is configured in its own `[gtfs.<name>]` section.

```toml
# Swiss timetable: the latest ZIP of the current year's dataset is discovered
# automatically on opentransportdata.swiss.
[gtfs.switzerland]
provider = "opentransportdata_ckan"
output_filename = "switzerland.gtfs.zip"
shapes_network = "switzerland"

# A feed downloaded from a fixed URL.
[gtfs.france]
provider = "static_url"
url = "https://example.com/france.zip"
output_filename = "france.gtfs.zip"
shapes_network = "france"

# Eurostar requires several OSM networks to cover its routes.
[gtfs.eurostar]
provider = "static_url"
url = "https://example.com/eurostar.zip"
output_filename = "eurostar.gtfs.zip"
shapes_network = ["france", "belgium", "netherlands"]

# A feed without shape generation.
[gtfs.germany]
provider = "static_url"
url = "https://example.com/germany.zip"
output_filename = "germany.gtfs.zip"
```

Each GTFS feed supports the following properties:

| Property | Description |
|---|---|
| `provider` | Required. The provider used to download the feed. |
| `output_filename` | Optional. Raw GTFS filename. Defaults to `<name>.gtfs.zip`. |
| `shapes_network` | Optional. One country/network name or a list of names used to generate GTFS shapes. |
| Other properties | Passed to the provider as options (e.g. `url`). |

Available providers:

- `opentransportdata_ckan`
- `static_url`

Country-specific GTFS preprocessing is selected using the feed name.

### Shape networks

The optional `shapes_network` property specifies which OSM extracts are used to generate GTFS shapes.

It accepts either a single country/network name:

```toml
shapes_network = "france"
```

Or a list of names:

```toml
shapes_network = ["france", "belgium", "netherlands"]
```

The names correspond to OSM extracts stored in `data/raw/osm/`. For example, `france` refers to:

```text
data/raw/osm/france.osm.pbf
```

When generating shapes, GraphMill:

1. Checks whether the corresponding extracted network already exists in `data/networks/` as a `.pkl` file.
2. If it exists, loads the cached network.
3. Otherwise, extracts the public transport network from the original OSM file in `data/raw/osm/` and saves it as a `.pkl` file in `data/networks/`.

For example, the first extraction of France creates:

```text
data/networks/france.pkl
```

Subsequent shape-generation runs reuse this file instead of extracting the network again.

Multiple networks are combined before generating shapes. This is useful for feeds whose routes cross country borders, such as Eurostar.

**Important:** Shape generation uses the original OSM extracts in `data/raw/osm/`. It does not use the filtered OSM files in `data/ready/`, and it does not copy or move OSM extracts into `data/networks/`.

A feed without `shapes_network` is processed normally, without automatic shape generation.

### Supported OSM extracts

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

### Supported GTFS feeds

- Switzerland
- France (Network SNCF TGV, Intercités et TER)
- Germany
- Austria
- Italy
- Renfe (long-distance)
- Belgium
- Netherlands
- Eurostar

## Command Line

### OSM

Download and process all configured countries:

```bash
uv run graphmill osm
```

Only selected countries, clipped to a bounding box:

```bash
uv run graphmill osm -c switzerland -c france --bbox 5.9,45.8,10.5,47.8
```

Raw extracts are kept in `data/raw/osm/`.

Each country is optionally clipped and filtered to retain the data needed by OTP (streets, platforms, park & ride, restrictions and route relations). The resulting files are written to `data/ready/<country>.osm.pbf`.

The files are not merged, since OTP loads every `.osm.pbf` in its data folder.

Existing raw files are reused. `--overwrite` forces a fresh download. Clipping and filtering always run again.

### GTFS

Download and preprocess all configured feeds:

```bash
uv run graphmill gtfs
```

Only selected feeds (names as in `config.toml`):

```bash
uv run graphmill gtfs -f switzerland -f germany
```

Clip the selected feeds to a bounding box:

```bash
uv run graphmill gtfs -f switzerland -f germany --bbox 5.9,45.8,10.5,47.8
```

#### Generate shapes during GTFS processing

Add `--shapes` to generate shapes after the normal GTFS download, preprocessing and clipping steps.

```bash
uv run graphmill gtfs --shapes
```

By default, shapes are generated for all selected feeds that have `shapes_network` configured.

Select specific feeds:

```bash
uv run graphmill gtfs -f france -f eurostar --shapes
```

Or explicitly specify which selected feeds should receive shapes:

```bash
uv run graphmill gtfs \
    -f france \
    -f eurostar \
    -f germany \
    --shapes \
    --shape-feed france \
    --shape-feed eurostar
```

In this example, all three feeds are downloaded and preprocessed, but shapes are generated only for France and Eurostar.

Each feed uses the network or networks configured in its `shapes_network` property.

### Generate shapes for already-processed feeds

The standalone `add-shapes` command generates shapes for GTFS feeds that are already available in `data/ready/`.

```bash
uv run graphmill add-shapes -f france
```

Multiple feeds can be specified:

```bash
uv run graphmill add-shapes -f france -f eurostar
```

The command uses the `shapes_network` configuration for each feed. It does not download or preprocess GTFS data.

### Everything

Download and process all configured OSM and GTFS data:

```bash
uv run graphmill all
```

## Python

The main processing functions can also be used directly.

```python
from graphmill.config import load_config
from graphmill.osm import process as process_osm
from graphmill.gtfs import process as process_gtfs

config = load_config()  # reads config/config.toml

# OSM: returns the list of filtered .osm.pbf files in data/ready/
osm_files = process_osm(
    ["switzerland", "france"],
    bbox=(5.9, 45.8, 10.5, 47.8),  # or None
    overwrite=False,
)

# GTFS: returns {feed name: path of the final GTFS file}
gtfs_files = process_gtfs(
    {
        name: feed
        for name, feed in config.gtfs_feeds.items()
        if name in {"switzerland", "france"}
    },
    bbox=config.bbox,
    overwrite=False,
)
```

### Individual processing steps

Individual steps can also be used on their own:

```python
from pathlib import Path
from graphmill import osm, gtfs

raw = osm.download_country("austria")
# data/raw/osm/austria.osm.pbf

osm.clip_to_bbox(
    raw,
    Path("clipped.osm.pbf"),
    (9.5, 46.3, 17.2, 49.0),
)

osm.filter_for_otp(
    raw,
    Path("filtered.osm.pbf"),
)

raw_gtfs = gtfs.download_all_feed(config.gtfs_feeds)
# {name: data/raw/gtfs/<file>}

preprocessed = gtfs.preprocess_all_feeds(raw_gtfs)
# {name: data/preprocess/<name>.preprocessed.gtfs.zip}

ready = gtfs.clip_all_feeds(preprocessed, bbox=config.bbox)
# {name: data/ready/<name>.ready.gtfs.zip}
```

## Adding a new GTFS source

Write a function that downloads the feed and returns its path, then register it under a provider name. Nothing else needs to change.

```python
from graphmill.gtfs import register_provider

@register_provider("my_portal")
def my_portal(feed_name, feed, overwrite=False):
    url = feed.options["url"]
    ...
    return destination_path
```

Then use `provider = "my_portal"` in `config.toml`.

## Adding country-specific preprocessing

Add a `preprocess_<country>(gtfs: GTFS) -> GTFS` function in `gtfs.py` and register it in the `PREPROCESSORS` dictionary inside `preprocess_all_feeds`.

The key must match the feed name in `config.toml` (case-insensitive).

## Output layout

```text
data/
├── raw/
│   ├── osm/
│   │   ├── <country>.osm.pbf
│   │   └── ...
│   └── gtfs/
│       └── <feed>.gtfs.zip
├── networks/
│   ├── <country>.pkl
│   └── ...
├── preprocess/
│   ├── <feed>.preprocessed.gtfs.zip
│   └── <feed>_with_shapes.gtfs.zip
└── ready/
    ├── <country>.osm.pbf
    └── <feed>.ready.gtfs.zip
```

- `data/raw/osm/` contains the original OSM extracts downloaded from Geofabrik. These are used as input when extracting public transport networks for shape generation.
- `data/raw/gtfs/` contains the original downloaded GTFS feeds.
- `data/networks/` contains cached, extracted public transport networks serialized as `.pkl` files. These are generated on demand when shapes are created and reused on subsequent runs.
- `data/preprocess/` contains intermediate GTFS files and GTFS files exported after shape generation.
- `data/ready/` contains the processed OSM and GTFS files.

Existing files are reused rather than rebuilt.

For GTFS, `--overwrite` forces every stage (download, preprocess and clip) to run again.

For OSM, `--overwrite` only forces a fresh download; clipping and filtering always run again.

The original OSM extracts are never copied into `data/networks/`.

## Tests

```bash
uv run pytest
```

The tests use no network access and do not need `osmium`.

## Git

Large downloaded and generated files are ignored with `data/` in `.gitignore`.