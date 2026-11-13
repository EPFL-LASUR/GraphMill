# GraphMill

Downloads and preprocesses GTFS and OSM data that routing machines such as [OpenTripPlanner](https://www.opentripplanner.org/) (OTP2), [OSRM](https://project-osrm.org/) or [MOTIS](https://github.com/motis-project/motis) need to build a routing graph:

- **OSM**: Downloads per-country `.osm.pbf` extracts from Geofabrik, optionally clips them to a bounding box, and filters them to retain the data needed by OTP (streets, platforms, park & ride, restrictions and route relations).
- **GTFS**: Downloads public transport feeds from several sources, preprocesses them per country (stop ID prefixes, parent stations, duplicate stop merging, etc.), and optionally clips them to a bounding box.
- **Public transport networks**: Extracts the public transport network (routes, stops, geometries) from OSM extracts with [TransitForge](https://github.com/EPFL-LASUR/transitforge).
- **GTFS shapes**: Generates missing GTFS shapes using one or more of these public transport networks.

The processed data is stored in `data/ready/`, ready to be used by a routing engine.

## Requirements

- Python 3.11+ (uses `tomllib`) and [uv](https://docs.astral.sh/uv/).
- [`osmium-tool`](https://osmcode.org/osmium-tool/) available on your `PATH` (used for clipping and filtering OSM data).
- The `gtfsmith` package (GTFS filtering and preprocessing).
- The `transitforge` package (public transport network extraction and GTFS shape reconstruction).

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
| `shapes_network` | Optional. A non-empty network name, or a non-empty list of names, used to generate GTFS shapes. |
| Other properties | Passed to the provider as options (e.g. `url`). |

Available providers:

- `opentransportdata_ckan`
- `static_url`

Country-specific GTFS preprocessing is selected using the feed name.

The configuration is validated when it is loaded: a missing `provider`, an `osm.bbox` that doesn't have exactly four values, or an invalid `shapes_network` (empty list, empty string, non-string items, wrong type) raises a `ValueError` that names the offending entry, for example `gtfs.france.shapes_network must be a non-empty string or list of strings`.

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

1. Checks whether the corresponding extracted network already exists in `data/network/` as a `.pkl` file.
2. If it exists, loads the cached network.
3. Otherwise, extracts the public transport network from the original OSM file in `data/raw/osm/` and saves it as a `.pkl` file in `data/network/`.

For example, the first extraction of France creates:

```text
data/network/france.pkl
```

Subsequent shape-generation runs reuse this file instead of extracting the network again. To extract networks ahead of time, use the [`extract-network`](#extract-network) command.

If necessary multiple networks are merged before generating shapes. This is useful for feeds whose routes cross country borders, such as Eurostar. The merge works as follows:

- Networks are merged in the order they are listed.
- Routes are identified by their OSM relation id. When the same relation exists in several networks (typically lines crossing an extract boundary), exactly one version is kept: the one with the most stops, then the longest geometry. On a complete tie, the network listed first wins.
- The cached networks are never modified, so the same network can safely be combined differently for different feeds.

**Important:** Shape generation uses the original OSM extracts in `data/raw/osm/`. It does not use the filtered OSM files in `data/ready/`, and it does not copy or move OSM extracts into `data/network/`.

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

#### Extract networks

`extract-network` extracts the public transport network of downloaded OSM extracts and saves it as a pickle. These networks are what `gtfs --shapes` and `add-shapes` use to reconstruct GTFS shapes.

```bash
# All countries listed in osm.countries (config.toml)
uv run graphmill extract-network

# Specific extracts only
uv run graphmill extract-network -c switzerland -c france

# Re-extract even if the pickle already exists
uv run graphmill extract-network -c switzerland --overwrite
```

| Option | Description |
|---|---|
| `--country`, `-c` | OSM extract to process. Can be given several times. Defaults to `osm.countries` in `config.toml`. |
| `--overwrite`, `-o` | Re-extract even if `data/network/<name>.pkl` already exists. |

- The input is `data/raw/osm/<name>.osm.pbf` and the output is `data/network/<name>.pkl`. Run the `osm` command first to download the extracts.
- Existing pickles are skipped unless `--overwrite` is given. A skipped country doesn't need its `.osm.pbf` any more.
- The command isn't limited to the countries supported by `osm`: any `.osm.pbf` in `data/raw/osm/` works, for example a custom extract such as `grand_lausanne.osm.pbf`.
- All required `.osm.pbf` files are checked before extraction starts, so a missing file fails immediately instead of after a long extraction.
- Extraction reads the whole PBF several times and can take a while for large extracts.

The same extraction runs automatically the first time a network is needed by `gtfs --shapes` or `add-shapes`. Running `extract-network` beforehand avoids that wait.

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

Rules for the shape options:

- `--shape-feed` and `--verbose` only have an effect together with `--shapes`.
- Every `--shape-feed` must be among the selected feeds, and must have a `shapes_network` configured. Otherwise the command fails.
- The `--shape-feed` check happens after the GTFS feeds have been processed, not before.
- If no selected feed has a `shapes_network`, the command prints `No feeds configured for shape generation.` and stops.

Add `--verbose` (`-v`) to print a shape-generation report, see [Shape reports](#shape-reports).

### Generate shapes for already-processed feeds

The standalone `add-shapes` command generates shapes for GTFS feeds that are already available in `data/ready/`.

```bash
uv run graphmill add-shapes -f france
```

Multiple feeds can be specified:

```bash
uv run graphmill add-shapes -f france -f eurostar
```

Print the shape-generation report:

```bash
uv run graphmill add-shapes -f france --verbose
```

- `--feed` is required.
- The command uses the `shapes_network` configuration for each feed. It does not download or preprocess GTFS data.
- Each feed needs a `shapes_network` and an existing `data/ready/<feed>.ready.gtfs.zip`, otherwise the command fails.
- Feeds are checked and processed one at a time. If a later feed fails its checks, the earlier feeds have already been processed.
- Networks are loaded once and shared between the feeds of the same run.

The result is written to `data/preprocess/<feed>_with_shapes.gtfs.zip`.

### Shape reports

With `--verbose`, both `gtfs --shapes` and `add-shapes` print:

1. A summary table counting the routes without shapes per failure reason.
2. A detailed table with one row per route without a shape (route id, GTFS ref, route type, reason, and the stop index where matching failed, when known).

The same report is also written to `data/report/<feed>_shapes_report.csv`.

The reasons come from TransitForge's diagnostics, for example `NO_CANDIDATES`, `NO_ROUTE_NEAR_EITHER_END`, `STOP_TOO_FAR` or `STOP_ORDER_MISMATCH`. Each reason is documented in the TransitForge README.

### Filter a ready GTFS feed by route type

`filter-gtfs` keeps only the given GTFS route types of feeds that are available in `data/ready/`.

```bash
# Keep only rail (2) and bus (3) routes of France
uv run graphmill filter-gtfs -f france -m 2 -m 3
```

| Option | Description |
|---|---|
| `--feed`, `-f` | Required. GTFS feed name. Can be given several times. |
| `--mode`, `-m` | Required. GTFS `route_type` to keep (integer). Can be given several times. |

The input is `data/ready/<feed>.ready.gtfs.zip` and the result is saved next to it as `data/ready/<feed>_filtered.gtfs.zip`. The ready file is not modified.

Feed names are validated first. A missing ready file stops the command.

### Everything

Download and process all configured OSM and GTFS data:

```bash
uv run graphmill all
```

`all` runs the OSM step and then the GTFS step. It doesn't generate shapes: use `gtfs --shapes`, or `add-shapes` afterwards.

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

### Networks and shapes

```python
from transitforge import TransitRouteNetwork
from graphmill import gtfs, osm

# Extract a network from an OSM extract. It is also saved to
# data/network/<name>.pkl, named after the PBF file without ".osm.pbf".
network = osm.extract_pt_network("data/raw/osm/switzerland.osm.pbf")

# Merge several networks (the earlier network wins ties). Neither input is modified.
france = TransitRouteNetwork.load("data/network/france.pkl")
combined = network.merge(france, verbose=False)

# Add shapes to a feed (a path to a GTFS zip, or a loaded GTFS object).
# The result is written to data/preprocess/<name>_with_shapes.gtfs.zip.
# With verbose=True, a report is printed and saved to data/report/.
gtfs_with_shapes = gtfs.add_shapes(
    "data/ready/france.ready.gtfs.zip", combined, verbose=True, name="france"
)
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
├── network/
│   ├── <country>.pkl
│   └── ...
├── preprocess/
│   ├── <feed>.preprocessed.gtfs.zip
│   └── <feed>_with_shapes.gtfs.zip
├── report/
│   └── <feed>_shapes_report.csv
└── ready/
    ├── <country>.osm.pbf
    ├── <feed>.ready.gtfs.zip
    └── <feed>_filtered.gtfs.zip
```

- `data/raw/osm/` contains the original OSM extracts downloaded from Geofabrik. These are used as input when extracting public transport networks for shape generation.
- `data/raw/gtfs/` contains the original downloaded GTFS feeds.
- `data/network/` contains the extracted public transport networks serialized as `.pkl` files. They are written by `extract-network`, or generated on demand when shapes are created, and reused on subsequent runs.
- `data/preprocess/` contains intermediate GTFS files and GTFS files exported after shape generation.
- `data/report/` contains the shape-generation reports written with `--verbose`.
- `data/ready/` contains the processed OSM and GTFS files, and the route-type-filtered feeds written by `filter-gtfs`.

Existing files are reused rather than rebuilt.

For GTFS, `--overwrite` forces every stage (download, preprocess and clip) to run again.

For OSM, `--overwrite` only forces a fresh download; clipping and filtering always run again.

For network extraction, `--overwrite` forces the pickles to be extracted again.

The original OSM extracts are never copied into `data/network/`.

## Tests

```bash
uv run pytest
```

The tests use no network access and do not need `osmium`. Downloads, the OSM and GTFS processing steps, the shape builder and the network extraction are replaced by fakes, so only GraphMill's own logic is tested.

What is covered:

- **`config`**
  - the data directory layout, including `data/network/` and `data/report/`;
  - `[osm]` parsing: countries, `bbox` conversion and validation, missing sections and keys, missing or invalid files;
  - `[gtfs.<name>]` parsing: provider, `output_filename` default, extra options, copied tables;
  - `shapes_network`: default, string and list values, order, not leaking into `options`, per-feed values, rejection of invalid values with a message naming the feed;
  - the dataclasses are frozen and can be copied with `dataclasses.replace`;
  - the real `config/config.toml`: known providers, URLs, unique output filenames, supported OSM countries, valid `shapes_network` entries.

- **`gtfs`**
  - CKAN discovery, localized names, resource dates and picking the latest ZIP;
  - downloads: headers, redirects, temporary files and error cleanup;
  - the provider registry, `static_url` and CKAN providers, overwrite behavior;
  - clipping, the per-country preprocessors, `preprocess_all_feeds`, and `process` stage ordering;
  - `add_shapes`:
    - input as a GTFS object, a `Path` or a `str`, and how the feed name is derived;
    - the builder receives the network and `verbose` as diagnostics flag;
    - the built feed is exported to `data/preprocess/` and returned;
    - quiet mode prints nothing and writes no report;
    - verbose mode prints the summary and detail tables, shows missing fail stops as `-`, and writes the CSV (also for an empty report);
    - the report directory is created when missing.

- **`osm`**
  - `extract_pt_network`:
    - the builder receives the PBF path (`str` or `Path`) with default route types;
    - the network is built once and saved in `data/network/` under the PBF's name without `.osm.pbf`;
    - failures during build or save propagate, and nothing is saved if the build fails;
    - a real network round-trips through the saved pickle.

- **`cli`**
  - `_parse_bbox`, and the `osm`, `gtfs` and `all` commands: option forwarding, config defaults, validation and error exit codes;
  - `_load_one_network`: cached pickle, extraction from the PBF, missing files, directory creation;
  - `_load_networks`: single and merged networks, cache reuse, cached networks left unmodified, tie order, duplicates;
  - `gtfs --shapes`: feed selection with `--shape-feed`, shared network cache, validation errors, processing order;
  - `add-shapes`: required options, ready file and `shapes_network` checks, one-by-one processing, shared cache;
  - `filter-gtfs`: required options, multiple feeds and modes, output name, unknown feeds and missing ready files;
  - `extract-network`:
    - country selection (config defaults, `--country`, duplicates, order, custom extracts);
    - skipping existing pickles and `--overwrite`;
    - missing PBF files are rejected before any extraction starts;
    - progress messages, and extraction failures stopping the run.

Tests for merging networks and the TransitForge matching and diagnostics live in the TransitForge repository.

## Git

Large downloaded and generated files are ignored with `data/` in `.gitignore`.