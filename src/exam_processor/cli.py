"""CLI commands for exam processor."""

import json
from pathlib import Path

import typer
from exam_processor.client import TogetherClient, MathpixClient
from exam_processor.batch import SubjectExtractionBatch, ImageDescriptionBatch, combine_results
from exam_processor.models import SubjectEntry

app = typer.Typer(help="Exam Processor CLI")

def get_client() -> TogetherClient:
    """Get a configured Together client."""
    try:
        return TogetherClient()
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def get_mathpix_client() -> MathpixClient:
    """Get a configured Mathpix client."""
    try:
        return MathpixClient()
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def create_extraction(
    input_file: Path = typer.Argument(..., exists=True, help="JSON file with subject entries"),
    output_dir: Path = typer.Option(".", help="Directory to save batch tracking files"),
    tracking_file: Path = typer.Option(..., help="Path to save the batch tracking JSON"),
    model: str = typer.Option("Qwen/Qwen3.5-9B", help="Model for extraction"),
):
    """Create a subject extraction batch from a JSON input file."""
    client = get_client()
    
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Handle both {"entries": [...]} and direct array [...]
    if isinstance(data, dict) and "entries" in data:
        entries = [SubjectEntry(**e) for e in data["entries"]]
    elif isinstance(data, list):
        entries = [SubjectEntry(**e) for e in data]
    else:
        typer.secho("Error: Invalid input format", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    
    batch = SubjectExtractionBatch(client)
    batch_id = batch.create(
        entries=entries,
        output_dir=str(output_dir),
        model=model
    )
    
    # Rename tracking file to user-specified path
    default_tracking = output_dir / "subject_extraction_tracking.json"
    if default_tracking.exists():
        tracking_file.parent.mkdir(parents=True, exist_ok=True)
        default_tracking.replace(tracking_file)
    
    typer.echo(batch_id)


@app.command()
def batch_status(batch_id: str):
    """Check the status of any batch."""
    client = get_client()
    status = client.get_batch_status(batch_id)
    
    typer.echo(f"Batch ID: {status['batch_id']}")
    typer.echo(f"Status: {status['status']}")
    typer.echo(f"Created: {status['created_at']}")
    if status["completed_at"]:
        typer.echo(f"Completed: {status['completed_at']}")
    if status.get("progress") is not None:
        typer.echo(f"Progress: {status['progress']}%")
    if status.get("error"):
        typer.secho(f"Error: {status['error']}", fg=typer.colors.RED)


@app.command()
def retrieve_extraction(
    batch_id: str,
    tracking_file: Path = typer.Option(..., exists=True, help="Tracking JSON from create-extraction"),
    output_file: Path = typer.Option(..., help="Path to save the result JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print raw JSONL responses for failed parsing attempts"),
):
    """Retrieve results from a subject extraction batch."""
    client = get_client()
    batch = SubjectExtractionBatch(client)
    
    result = batch.retrieve(
        batch_id=batch_id,
        tracking_file=str(tracking_file),
        output_file=str(output_file),
        verbose=verbose
    )
    
    total_problems = sum(len(v) for v in result.values())
    typer.secho(f"Saved {total_problems} problems from {len(result)} files to {output_file}", fg=typer.colors.GREEN)


@app.command()
def create_image_description(
    extraction_result: Path = typer.Argument(..., exists=True, help="JSON file from retrieve-extraction"),
    output_dir: Path = typer.Option(".", help="Directory to save batch tracking files"),
    tracking_file: Path = typer.Option(..., help="Path to save the batch tracking JSON"),
    model: str = typer.Option("Qwen/Qwen3.5-397B-A17B", help="Model for image description"),
):
    """Create an image description batch from extraction results."""
    client = get_client()
    
    with open(extraction_result, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Parse the grouped result
    from exam_processor.models import ProblemSchema
    problems_by_file: dict[str, list[ProblemSchema]] = {}
    for file_path, problems in data.items():
        problems_by_file[file_path] = [ProblemSchema(**p) for p in problems]
    
    batch = ImageDescriptionBatch(client)
    batch_id = batch.create_from_problems(
        problems_by_file=problems_by_file,
        output_dir=str(output_dir),
        model=model
    )
    
    # Rename tracking file to user-specified path
    default_tracking = output_dir / "image_description_tracking.json"
    if default_tracking.exists():
        tracking_file.parent.mkdir(parents=True, exist_ok=True)
        default_tracking.replace(tracking_file)
    
    typer.echo(batch_id)


@app.command()
def retrieve_image_description(
    batch_id: str,
    tracking_file: Path = typer.Option(..., exists=True, help="Tracking JSON from create-image-description"),
    output_file: Path = typer.Option(..., help="Path to save the result JSON"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print raw JSONL responses for failed CDL attempts"),
):
    """Retrieve CDL results from an image description batch."""
    client = get_client()
    batch = ImageDescriptionBatch(client)
    
    result = batch.retrieve(
        batch_id=batch_id,
        tracking_file=str(tracking_file),
        output_file=str(output_file),
        verbose=verbose
    )
    
    total_cdls = sum(len(v) for v in result.values())
    typer.secho(f"Saved CDL for {total_cdls} images to {output_file}", fg=typer.colors.GREEN)


@app.command()
def combine(
    extraction_result: Path = typer.Argument(..., exists=True, help="JSON file from retrieve-extraction"),
    cdl_result: Path = typer.Argument(..., exists=True, help="JSON file from retrieve-image-description"),
    output_file: Path = typer.Option(..., help="Path to save the final combined result"),
):
    """Combine extraction results with CDL descriptions into final output."""
    from exam_processor.models import ProblemSchema
    
    # Load extraction results
    with open(extraction_result, "r", encoding="utf-8") as f:
        problems_by_file: dict[str, list[ProblemSchema]] = {
            k: [ProblemSchema(**p) for p in v]
            for k, v in json.load(f).items()
        }
    
    # Load CDL results
    with open(cdl_result, "r", encoding="utf-8") as f:
        cdl_by_file: dict[str, list[dict]] = json.load(f)
    
    final = combine_results(problems_by_file, cdl_by_file, output_file=str(output_file))
    
    total_problems = sum(len(v) for v in final.values())
    total_images = sum(
        len(p.imagini) + len(p.barem_imagini) 
        for problems in final.values() 
        for p in problems
    )
    typer.secho(f"Combined {total_problems} problems with {total_images} CDL images to {output_file}", fg=typer.colors.GREEN)


@app.command()
def convert_documents(
    input_folder: Path = typer.Argument(..., exists=True, help="Input folder containing documents"),
    output_folder: Path = typer.Argument(..., help="Output folder for markdown files"),
    extensions: str = typer.Option(
        "pdf,docx,doc,pptx,ppt,epub",
        "--extensions", "-e",
        help="Comma-separated list of file extensions to process"
    ),
    skip_existing: bool = typer.Option(False, "--skip-existing", help="Skip files that already have markdown output"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress"),
):
    """
    Convert all documents in a folder (and subfolders) to markdown.
    
    Preserves the folder structure in the output directory.
    Supported formats: PDF, DOCX, DOC, PPTX, PPT, EPUB
    """
    ext_set = {f".{ext.strip().lower()}" for ext in extensions.split(",")}

    all_files = []
    for ext in ext_set:
        all_files.extend(input_folder.rglob(f"*{ext}"))

    all_files = list(set(all_files))
    all_files.sort()

    if not all_files:
        typer.secho("No supported files found.", fg=typer.colors.YELLOW)
        return

    typer.echo(f"Found {len(all_files)} file(s) to process.")
    output_folder.mkdir(parents=True, exist_ok=True)

    client = get_mathpix_client()

    success_count = 0
    error_count = 0
    skipped_count = 0

    for file_path in all_files:
        rel_path = file_path.relative_to(input_folder)
        md_path = output_folder / rel_path.with_suffix(".md")

        if verbose:
            typer.echo(f"\nProcessing: {rel_path}")

        if skip_existing and md_path.exists():
            if verbose:
                typer.secho(f"  Skipping (exists): {md_path}", fg=typer.colors.YELLOW)
            skipped_count += 1
            continue

        md_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            content = client.convert_to_markdown(str(file_path))
            md_path.write_text(content, encoding="utf-8")
            success_count += 1
            if verbose:
                typer.secho(f"  Saved: {md_path}", fg=typer.colors.GREEN)
        except Exception as e:
            error_count += 1
            typer.secho(f"  Error: {e}", fg=typer.colors.RED, err=True)

    typer.echo(f"\n{'='*50}")
    typer.echo(f"Completed: {success_count} successful, {error_count} errors, {skipped_count} skipped")
    if error_count > 0:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
