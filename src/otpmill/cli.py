import typer
from .config import load_config
from .gtfs import prepare_switzerland
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
def gtfs():
    """Download the newest Swiss GTFS feed for the active timetable."""
    config = load_config()
    path = prepare_switzerland(query=config.gtfs_dataset_search_query)
    typer.echo(f"Raw GTFS: {path}")


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
    path = prepare_switzerland(query=config.gtfs_dataset_search_query)
    typer.echo(f"Raw GTFS: {path}")


if __name__ == "__main__":
    app()