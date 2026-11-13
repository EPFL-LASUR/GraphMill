import typer
from pathlib import Path
from functools import reduce

from transitforge import TransitRouteNetwork
from gtfsmith import filter_gtfs_per_mode

from .config import load_config, READY_DIR, RAW_OSM_DIR, NETWORK_DIR
from .gtfs import process as process_gtfs, add_shapes as add_gtfs_shapes
from .osm import SUPPORTED_COUNTRIES, process as process_osm, extract_pt_network

app = typer.Typer(help="Download and preprocess data for OpenTripPlanner.")

BBOX_OPTION = typer.Option(None, "--bbox", help="Clip to min_lon,min_lat,max_lon,max_lat. Overrides osm.bbox in config.toml.")


def _parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    parts = value.split(",")
    if len(parts) != 4:
        raise typer.BadParameter("bbox must be 'min_lon,min_lat,max_lon,max_lat'")
    try:
        return tuple(float(p) for p in parts)  # type: ignore[return-value]
    except ValueError:
        raise typer.BadParameter("bbox values must be numbers")


def _load_one_network(
    country: str,
) -> TransitRouteNetwork:

    pkl_path = NETWORK_DIR / f"{country}.pkl"
    osm_path = RAW_OSM_DIR / f"{country}.osm.pbf"

    # Reuse an existing extracted network.
    if pkl_path.is_file():
        typer.echo(f"[{country}] Loading cached PT network.")
        return TransitRouteNetwork.load(pkl_path)

    # Otherwise, extract it from the original OSM file.
    if not osm_path.is_file():
        raise typer.BadParameter(
            f"OSM extract not found: {osm_path}. "
            "Run the OSM download command first."
        )

    typer.echo(f"[{country}] Extracting PT network from OSM.")
    network = extract_pt_network(osm_path)

    NETWORK_DIR.mkdir(parents=True, exist_ok=True)

    typer.echo(f"[{country}] Saving PT network: {pkl_path}")
    network.save(pkl_path)

    return network


def _load_networks(
    countries: str | list[str],
    cache: dict[str, TransitRouteNetwork],
) -> TransitRouteNetwork:

    if isinstance(countries, str):
        countries = [countries]

    networks = []

    for country in countries:
        if country not in cache:
            cache[country] = _load_one_network(country)

        networks.append(cache[country])

    if len(networks) == 1:
        return networks[0]

    return reduce(
        lambda merged, net: merged.merge(net, verbose=False),
        networks,
    )


@app.command()
def osm(
    country: list[str] = typer.Option(None, "--country", "-c", help="Country to download. Can be specified multiple times."),
    overwrite: bool = typer.Option(False, "--overwrite", "-o", help="Re-download even if the raw file already exists."),
    bbox: str = BBOX_OPTION,
):
    """Download and preprocess OSM data."""
    config = load_config()
    countries = country or config.osm_countries
    invalid = set(countries) - SUPPORTED_COUNTRIES
    if invalid:
        raise typer.BadParameter("Unsupported country(s): " + ", ".join(sorted(invalid)) + ". Supported: " + ", ".join(sorted(SUPPORTED_COUNTRIES)))
    typer.echo("Processing countries: " + ", ".join(countries))
    process_osm(countries, bbox=_parse_bbox(bbox) or config.bbox, overwrite=overwrite)


@app.command()
def gtfs(
    feed: list[str] = typer.Option(None, "--feed", "-f", help="GTFS feed name. Can be specified multiple times."),
    bbox: str | None = BBOX_OPTION,
    shapes: bool = typer.Option(False, "--shapes", help="Generate GTFS shapes after preprocessing."),
    shape_feed: list[str] | None = typer.Option(None, "--shape-feed", help="Selected feed to generate shapes for. Can be specified multiple times."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print the shape-generation report."),
    overwrite: bool = typer.Option(False, "--overwrite", "-o", help="Re-download and reprocess feeds."),
):
    config = load_config()
    bbox = _parse_bbox(bbox) if bbox is not None else config.bbox

    # Select GTFS feeds.
    if feed:
        unknown = set(feed) - set(config.gtfs_feeds)
        if unknown:
            raise typer.BadParameter(f"Unknown GTFS feeds: {', '.join(sorted(unknown))}")

        selected_feeds = {name: config.gtfs_feeds[name] for name in feed}
    else:
        selected_feeds = config.gtfs_feeds

    # Process feeds
    names = feed or list(config.gtfs_feeds)
    typer.echo("Processing GTFS feeds: " + ", ".join(names))
    gtfs_files = process_gtfs(selected_feeds, bbox=bbox, overwrite=overwrite)

    # Stop here if shapes are not requested.
    if not shapes:
        return

    # Determine which feeds should receive shapes.
    if shape_feed:
        unknown = set(shape_feed) - set(selected_feeds)
        if unknown:
            raise typer.BadParameter(f"Shape feeds must be among the selected GTFS feeds: {', '.join(sorted(unknown))}")

        feeds_to_shape = shape_feed
    else:
        feeds_to_shape = [name for name, cfg in selected_feeds.items() if cfg.shapes_network is not None]

    if not feeds_to_shape:
        typer.echo("No feeds configured for shape generation.")
        return

    # Reuse loaded networks.
    network_cache: dict[Path, TransitRouteNetwork] = {}

    for name in feeds_to_shape:
        cfg = selected_feeds[name]

        if cfg.shapes_network is None:
            raise typer.BadParameter(f"No shapes_network configured for GTFS feed '{name}'.")

        typer.echo(f"\n[{name}] Generating GTFS shapes.")

        network = _load_networks(cfg.shapes_network, network_cache)

        add_gtfs_shapes(gtfs_files[name], network, verbose=verbose, name=name)



@app.command(name="all")
def all_data(
    overwrite: bool = typer.Option(False, "--overwrite", "-o", help="Re-download OSM files even if they already exist."),
    bbox: str = BBOX_OPTION,
):
    """Download and preprocess all configured source data."""
    config = load_config()
    typer.echo("=== OSM ===")
    process_osm(config.osm_countries, bbox=_parse_bbox(bbox) or config.bbox, overwrite=overwrite)

    typer.echo("\n=== GTFS ===")
    process_gtfs(config.gtfs_feeds, bbox=_parse_bbox(bbox) or config.bbox, overwrite=overwrite)


@app.command(name="add-shapes")
def add_shapes_command(
    feed: list[str] = typer.Option(..., "--feed", "-f", help="GTFS feed name. Can be specified multiple times."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print the shape-generation report."),
):
    config = load_config()

    unknown = set(feed) - set(config.gtfs_feeds)
    if unknown:
        raise typer.BadParameter(f"Unknown GTFS feeds: {', '.join(sorted(unknown))}")

    network_cache: dict[Path, TransitRouteNetwork] = {}

    for name in feed:
        cfg = config.gtfs_feeds[name]

        if cfg.shapes_network is None:
            raise typer.BadParameter(f"No shapes_network configured for GTFS feed '{name}'.")

        ready_file = READY_DIR / cfg.output_filename.replace(".gtfs.zip", ".ready.gtfs.zip")

        if not ready_file.is_file():
            raise typer.BadParameter(f"Ready GTFS file not found for '{name}': {ready_file}")

        typer.echo(f"\n[{name}] Generating GTFS shapes.")

        network = _load_networks(cfg.shapes_network, network_cache)

        add_gtfs_shapes(ready_file, network, verbose=verbose, name=name)


@app.command("filter-gtfs")
def filter_gtfs_for_mode(
    feed: list[str] = typer.Option(..., "--feed", "-f", help="GTFS feed name. Can be specified multiple times.",),
    mode: list[int] = typer.Option(..., "--mode", "-m", help="GTFS route type to keep. Can be specified multiple times.",),
):
    """Filter ready GTFS feeds by one or more route types."""
    cfg = load_config()

    # Validate feed names
    unknown_feeds = set(feed) - set(cfg.gtfs_feeds)
    if unknown_feeds:
        raise typer.BadParameter(f"Unknown GTFS feed(s): {', '.join(sorted(unknown_feeds))}")

    # Validate route types
    if not mode:
        raise typer.BadParameter("At least one route type must be specified.")

    # Process each selected feed
    for feed_name in feed:
        typer.echo(f"Filtering GTFS feed '{feed_name}' for route type(s): {', '.join(map(str, mode))}")

        feed_config = cfg.gtfs_feeds[feed_name]

        # Resolve the ready GTFS file
        output_filename = feed_config.output_filename
        ready_filename = output_filename.removesuffix(".gtfs.zip") + ".ready.gtfs.zip"
        ready_path = READY_DIR / ready_filename

        if not ready_path.is_file():
            raise typer.BadParameter(f"Ready GTFS feed not found: {ready_path}")

        # Filter the feed
        output_path = READY_DIR / (
            ready_filename.removesuffix(".ready.gtfs.zip") + "_filtered.gtfs.zip"
        )

        filter_gtfs_per_mode(gtfs=ready_path, route_types=mode, output_path=output_path)

        typer.echo(f"Saved filtered feed to: {output_path}")

@app.command(name="extract-network")
def extract_network(
    country: list[str] = typer.Option(None, "--country", "-c", help="Country (OSM extract name) to extract. Can be specified multiple times. Defaults to osm.countries in config.toml."),
    overwrite: bool = typer.Option(False, "--overwrite", "-o", help="Re-extract even if the network pickle already exists.")
):
    """Extract the public transport network of OSM extracts and save it as a pickle."""
    config = load_config()

    # Drop duplicates, keep the given order.
    countries = list(dict.fromkeys(country or config.osm_countries))

    to_extract = [
        c for c in countries
        if overwrite or not (NETWORK_DIR / f"{c}.pkl").is_file()
    ]

    # Fail before any (slow) extraction starts.
    missing = [c for c in to_extract if not (RAW_OSM_DIR / f"{c}.osm.pbf").is_file()]
    if missing:
        raise typer.BadParameter(
            "OSM extract not found for: " + ", ".join(missing)
            + f" (expected in {RAW_OSM_DIR}). Run the OSM download command first."
        )

    for name in countries:
        pkl_path = NETWORK_DIR / f"{name}.pkl"

        if name not in to_extract:
            typer.echo(f"[{name}] Already extracted: {pkl_path}")
            continue

        typer.echo(f"[{name}] Extracting PT network from OSM.")
        extract_pt_network(RAW_OSM_DIR / f"{name}.osm.pbf")
        typer.echo(f"[{name}] Saved PT network: {pkl_path}")

if __name__ == "__main__":
    app()