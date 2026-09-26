import typer
from .config import load_config
from .gtfs import prepare_switzerland
from .osm import SUPPORTED_COUNTRIES, process as process_osm

app = typer.Typer(help="Download and preprocess data for OpenTripPlanner.")


@app.command()
def osm(
    country: list[str] = typer.Option(None, "--country", "-c", help="Country to download. Can be specified multiple times."),
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="Re-download even if the raw file already exists."),
):
    """Download and preprocess OSM data."""
    config = load_config()
    countries = country or config.osm_countries
    invalid = set(countries) - SUPPORTED_COUNTRIES
    if invalid:
        raise typer.BadParameter("Unsupported country(s): " + ", ".join(sorted(invalid)) + ". Supported: " + ", ".join(sorted(SUPPORTED_COUNTRIES)))
    typer.echo("Processing countries: " + ", ".join(countries))
    process_osm(countries, bbox=config.osm_bbox, overwrite=overwrite)


@app.command()
def gtfs():
    """Download the newest Swiss GTFS feed for the active timetable."""
    config = load_config()
    path = prepare_switzerland(query=config.gtfs_dataset_search_query)
    typer.echo(f"Raw GTFS: {path}")


@app.command(name="all")
def all_data(
    overwrite: bool = typer.Option(False, "--overwrite", "-f", help="Re-download OSM files even if they already exist."),
):
    """Download and preprocess all configured source data."""
    config = load_config()
    typer.echo("=== OSM ===")
    process_osm(config.osm_countries, bbox=config.osm_bbox, overwrite=overwrite)

    typer.echo("\n=== GTFS ===")
    path = prepare_switzerland(query=config.gtfs_dataset_search_query)
    typer.echo(f"Raw GTFS: {path}")


if __name__ == "__main__":
    app()