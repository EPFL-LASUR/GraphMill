import typer
from .config import load_config
from .gtfs import download as download_gtfs, download_feed
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
    process_osm(countries, bbox=_parse_bbox(bbox) or config.osm_bbox, overwrite=overwrite)


@app.command()
def gtfs(
    feed: list[str] = typer.Option(None, "--feed", "-g", help="GTFS feed to download, as named in config.toml. Can be specified multiple times."),
):
    """Download configured GTFS feed(s)."""
    config = load_config()
    names = feed or list(config.gtfs_feeds)
    invalid = set(names) - set(config.gtfs_feeds)
    if invalid:
        raise typer.BadParameter("Unknown GTFS feed(s): " + ", ".join(sorted(invalid)) + ". Configured: " + ", ".join(sorted(config.gtfs_feeds)))
    for name in names:
        path = download_feed(name, config.gtfs_feeds[name])
        typer.echo(f"Raw GTFS ({name}): {path}")


@app.command(name="all")
def all_data(
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="Re-download OSM files even if they already exist."),
    bbox: str = BBOX_OPTION,
):
    """Download and preprocess all configured source data."""
    config = load_config()
    typer.echo("=== OSM ===")
    process_osm(config.osm_countries, bbox=_parse_bbox(bbox) or config.osm_bbox, overwrite=overwrite)

    typer.echo("\n=== GTFS ===")
    for name, path in download_gtfs(config.gtfs_feeds).items():
        typer.echo(f"Raw GTFS ({name}): {path}")


if __name__ == "__main__":
    app()