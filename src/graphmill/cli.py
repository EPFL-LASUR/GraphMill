import typer
from .config import load_config
from .gtfs import process as process_gtfs
from .osm import SUPPORTED_COUNTRIES, process as process_osm

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
    feed: list[str] = typer.Option(None, "--feed", "-f", help="GTFS feed to download, as named in config.toml. Can be specified multiple times."),
    overwrite: bool = typer.Option(False, "--overwrite", "-o", help="Re-download even if the raw file already exists."),
    bbox: str = BBOX_OPTION
):
    """Download configured GTFS feed(s)."""
    config = load_config()
    names = feed or list(config.gtfs_feeds)
    invalid = set(names) - set(config.gtfs_feeds)
    if invalid:
        raise typer.BadParameter("Unknown GTFS feed(s): " + ", ".join(sorted(invalid)) + ". Configured: " + ", ".join(sorted(config.gtfs_feeds)))

    selected_feeds = {
        name: config.gtfs_feeds[name]
        for name in names
    }
    
    typer.echo("Processing GTFS feeds: " + ", ".join(names))
    process_gtfs(selected_feeds, bbox=_parse_bbox(bbox) or config.bbox, overwrite=overwrite,)



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


if __name__ == "__main__":
    app()