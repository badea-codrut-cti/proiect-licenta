import json
from pathlib import Path

import typer
from PIL import Image as PILImage

from exam_processor.utils.client import TogetherClient
from exam_processor.utils.images import crop_image
from exam_processor.utils.models import (
    get_model,
    format_usage_info,
    DEFAULT_CONSISTENCY_JUDGE_MODEL,
    DEFAULT_IMAGE_QUALITY,
    DEFAULT_OCR_MODEL,
    DEFAULT_DESCRIPTION_MODELS,
    DEFAULT_OUTER_PADDING,
)
from exam_processor.figure_filter import run_figure_filter
from exam_processor.ocr_batch import DocumentTuple, OcrExtractor
from exam_processor.description_extraction import DescriptionExtraction
from exam_processor.consistency import ConsistencyAssessment

app = typer.Typer(help="Exam Processor CLI")


@app.callback()
def global_options(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug output"),
    model: str = typer.Option(None, "--model", "-m", help="Override model for all commands"),
):
    ctx.meta["verbose"] = verbose
    ctx.meta["model"] = model


def get_verbose(ctx: typer.Context) -> bool:
    return ctx.meta.get("verbose", False)


def get_model_override(ctx: typer.Context) -> str | None:
    return ctx.meta.get("model")


def get_client() -> TogetherClient:
    try:
        return TogetherClient()
    except ValueError as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def _build_documents(input_file: Path, data: list) -> list[DocumentTuple]:
    documents = []
    input_dir = input_file.parent
    for item in data:
        if isinstance(item, list):
            subject_pdf = item[0]
            barem_pdf = item[1] if len(item) > 1 and item[1] else None
        elif isinstance(item, dict):
            subject_pdf = item.get("subject", item.get("subject_pdf", ""))
            barem_pdf = item.get("barem", item.get("barem_pdf"))
        else:
            typer.secho(f"Error: Invalid entry format: {item}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

        if not subject_pdf:
            typer.secho("Error: subject_pdf is required", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

        def _resolve(pdf):
            if not pdf:
                return None
            if Path(pdf).is_absolute():
                return str(Path(pdf).resolve())
            if Path(pdf).exists():
                return str(Path(pdf).resolve())
            return str((input_dir / pdf).resolve())

        subject_path = _resolve(subject_pdf)
        barem_path = _resolve(barem_pdf)

        if not subject_path or not Path(subject_path).exists():
            typer.secho(f"Error: Subject PDF not found: {subject_pdf}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)

        if barem_pdf and (not barem_path or not Path(barem_path).exists()):
            typer.secho(f"[WARNING] Barem PDF not found: {barem_pdf}, skipping barem", fg=typer.colors.YELLOW)
            barem_path = None

        documents.append(DocumentTuple(subject_pdf=subject_path, barem_pdf=barem_path))
    return documents


@app.command()
def extract(
    ctx: typer.Context,
    input_file: Path = typer.Argument(
        ..., exists=True,
        help="JSON file with document tuples: [subject_pdf, barem_pdf_or_null][]",
    ),
    output_file: Path = typer.Option(..., help="Path to save the result JSON"),
    image_output_dir: Path = typer.Option(
        None, "--image-output-dir",
        help="Directory to save extracted page images",
    ),
    image_base_url: str = typer.Option(
        None, "--image-base-url",
        help="Base URL for saved images (e.g. R2 bucket URL)",
    ),
    model: str = typer.Option(None, help="Model for OCR extraction (default: google/gemma-4-31B-it)"),
    dpi: int = typer.Option(200, "--dpi", help="DPI for PDF -> image conversion"),
    max_workers: int = typer.Option(
        1, "--max-workers", "-j",
        help="Thread-pool size for concurrent API requests",
    ),
    image_format: str = typer.Option(
        "jpeg", "--image-format", help="Image format: jpeg, png, webp",
    ),
    image_quality: int = typer.Option(
        DEFAULT_IMAGE_QUALITY, "--image-quality", help="JPEG/WebP quality (1-100)",
    ),
):
    """Run OCR + problem extraction on the input documents via the real-time API.

    INPUT_FILE is a JSON array of document tuples:
    [
      ["/path/to/subject.pdf", "/path/to/barem.pdf"],
      ["/path/to/subject2.pdf", null],
      ["/path/to/subject3.pdf"]
    ]

    Each document's pages are converted to images and sent to the model
    for OCR and problem extraction. Only problems WITH images are extracted.
    """
    verbose = get_verbose(ctx)
    model_override = get_model_override(ctx)

    actual_model = model or model_override or DEFAULT_OCR_MODEL

    if verbose:
        typer.echo(f"[DEBUG] Using model: {actual_model}")
        typer.echo(f"[DEBUG] DPI: {dpi}")

    model_info = get_model(actual_model)
    if verbose:
        typer.echo(
            f"[DEBUG] Model info: input=${model_info.input_price_per_million}/1M, "
            f"output=${model_info.output_price_per_million}/1M, "
            f"context={model_info.max_context_length}"
        )

    client = get_client()

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    documents = _build_documents(input_file, data)

    if not documents:
        typer.secho("Error: No valid document tuples found", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if verbose:
        typer.echo(f"[DEBUG] Processing {len(documents)} document tuple(s)")
        for doc in documents:
            barem_info = f" (barem: {doc.barem_pdf})" if doc.barem_pdf else ""
            typer.echo(f"  - {doc.subject_pdf}{barem_info}")

    extractor = OcrExtractor(client)
    summary = extractor.run(
        documents=documents,
        output_file=str(output_file),
        model=actual_model,
        dpi=dpi,
        image_output_dir=str(image_output_dir) if image_output_dir else None,
        base_url=image_base_url,
        image_format=image_format,
        image_quality=image_quality,
        max_workers=max_workers,
        verbose=verbose,
    )

    result = summary["results"]
    in_tokens, out_tokens = summary["total_tokens"]
    model_name = summary["model"]
    total_problems = sum(len(v) for v in result.values())
    typer.secho(
        f"Saved {total_problems} problems from {len(result)} files to {output_file}",
        fg=typer.colors.GREEN,
    )
    model_info = get_model(model_name)
    typer.echo(format_usage_info(in_tokens, out_tokens, model_info))


@app.command()
def extract_descriptions(
    ctx: typer.Context,
    ocr_result_file: Path = typer.Argument(
        ..., exists=True,
        help="OCR result JSON from extract with image paths",
    ),
    image_base_dir: Path = typer.Argument(
        ..., exists=True,
        help="Directory that contains the saved page images",
    ),
    output_file: Path = typer.Option(..., help="Path to save the enriched JSON"),
    models: list[str] = typer.Option(
        DEFAULT_DESCRIPTION_MODELS, "--model", "-m",
        help="Model(s) to use for extraction. Repeat flag for multiple models.",
    ),
    max_workers: int = typer.Option(
        10, "--max-workers", "-j",
        help="Thread-pool size for concurrent API requests",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug output"),
):
    """Run CDL + NL description extraction over all figures using real-time API.

    Each figure is sent to every specified model in parallel for both CDL
    and natural-language tasks. Results are merged directly into the OCR
    JSON and written to OUTPUT_FILE.
    """
    if verbose:
        typer.echo(f"[DEBUG] Using models: {models}")

    client = get_client()
    extractor = DescriptionExtraction(client, max_workers=max_workers)

    summary = extractor.run(
        ocr_result_file=str(ocr_result_file),
        image_base_dir=str(image_base_dir),
        output_file=str(output_file),
        models=models,
        verbose=verbose,
    )

    typer.secho(
        f"Saved enriched JSON → {output_file}", fg=typer.colors.GREEN
    )
    for m in summary["models"]:
        mi = get_model(m)
        typer.echo(
            f"  {mi.name}: {mi.max_context_length} ctx | "
            f"${mi.input_price_per_million}/1M in | ${mi.output_price_per_million}/1M out"
        )


@app.command()
def assess_consistency(
    ctx: typer.Context,
    enriched_file: Path = typer.Argument(
        ..., exists=True,
        help="Enriched JSON from extract-descriptions (with CDL + NL under model_results)",
    ),
    image_base_dir: Path = typer.Argument(
        ..., exists=True,
        help="Directory that contains the saved full-page images (same one used for extract-descriptions)",
    ),
    output_file: Path = typer.Option(..., help="Path to save the JSON with consistency verdicts merged in"),
    model: str = typer.Option(
        None, "--model", "-m",
        help=f"Judge model (default: {DEFAULT_CONSISTENCY_JUDGE_MODEL}; pass e.g. moonshotai/Kimi-K2.6 for a stronger judge)",
    ),
    max_workers: int = typer.Option(
        10, "--max-workers", "-j",
        help="Thread-pool size for concurrent API requests",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug output"),
):
    """Judge whether each model's CDL and NL descriptions of a figure agree.

    Every (image, model) pair that already has BOTH a CDL and an NL description
    is sent to a judge model together with the cropped figure image. The verdict
    (consistent, severity, issues, optional corrected CDL) is merged into
    ``model_results[model].consistency`` in the output JSON.
    """
    verbose = get_verbose(ctx)
    client = get_client()
    assessor = ConsistencyAssessment(client, max_workers=max_workers)

    summary = assessor.run(
        enriched_file=str(enriched_file),
        image_base_dir=str(image_base_dir),
        output_file=str(output_file),
        model=model,
        verbose=verbose,
    )

    typer.secho(
        f"Judged {summary['pairs']} (image,model) pairs with {summary['model']} → {output_file}",
        fg=typer.colors.GREEN,
    )
    mi = get_model(summary["model"])
    typer.echo(format_usage_info(summary["total_input_tokens"], summary["total_output_tokens"], mi))


@app.command()
def export_crops(
    ocr_result_file: Path = typer.Argument(..., exists=True, help="OCR result JSON with image paths and coordinates"),
    image_base_dir: Path = typer.Argument(..., exists=True, help="Directory containing full-page images referenced by image_path"),
    output_dir: Path = typer.Option(..., help="Directory to save cropped figure boxes"),
    scale_coords: float = typer.Option(
        1.0, "--scale",
        help="Scale factor for coordinates (e.g. 0.01 if the model returned 0-1 decimals, 1.0 if pixel values)",
    ),
    padding: float = typer.Option(
        DEFAULT_OUTER_PADDING, "--padding", "-p",
        help="Fractional outer padding to apply to each crop (default from DEFAULT_OUTER_PADDING)",
    ),
):
    if PILImage is None:
        typer.secho("Pillow is required. Install with: pip install Pillow", fg=typer.colors.RED)
        raise typer.Exit(1)

    with open(ocr_result_file, "r", encoding="utf-8") as f:
        ocr_data: dict[str, list] = json.load(f)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_dir = Path(image_base_dir)
    ok = skip = bad_coords = 0

    def _crop_one(src: Path, coords: list, dest: Path, label: str) -> None:
        nonlocal ok, bad_coords, skip
        try:
            im = PILImage.open(src)
        except Exception as e:
            typer.secho(f"  Failed to open {label} {src}: {e}", fg=typer.colors.RED)
            skip += 1
            return
        try:
            crop = crop_image(im, coords, outer_padding=padding, scale=scale_coords)
            if crop is None:
                bad_coords += 1
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            crop.save(dest, format="JPEG", quality=DEFAULT_IMAGE_QUALITY)
            ok += 1
        except Exception as e:
            typer.secho(f"  Failed to crop {label} {src}: {e}", fg=typer.colors.RED)
            skip += 1
        finally:
            im.close()

    for source_pdf, problems in ocr_data.items():
        doc_safe = Path(source_pdf).stem.replace(" ", "_")[:40]
        for prob_idx, prob in enumerate(problems):
            for img_idx, img_entry in enumerate(prob.get("imagini", [])):
                rel = img_entry.get("image_path")
                coords = img_entry.get("coordinates")
                page = img_entry.get("page_number", 0)

                if not rel or not coords or len(coords) != 4:
                    bad_coords += 1
                    continue

                src = base_dir / rel if not Path(rel).is_absolute() else Path(rel)
                if not src.exists():
                    skip += 1
                    continue

                fname = f"{doc_safe}_p{page:04d}_pr{prob_idx:03d}_f{img_idx:03d}.jpg"
                _crop_one(src, coords, out_dir / fname, "subject")

            barem = prob.get("barem")
            if barem:
                for img_idx, img_entry in enumerate(barem.get("imagini", [])):
                    rel = img_entry.get("image_path")
                    coords = img_entry.get("coordinates")
                    page = img_entry.get("page_number", 0)

                    if not rel or not coords or len(coords) != 4:
                        bad_coords += 1
                        continue

                    src = base_dir / rel if not Path(rel).is_absolute() else Path(rel)
                    if not src.exists():
                        skip += 1
                        continue

                    fname = f"{doc_safe}_p{page:04d}_pr{prob_idx:03d}_b{img_idx:03d}.jpg"
                    _crop_one(src, coords, out_dir / fname, "barem")

    typer.secho(f"Cropped {ok} images → {output_dir}", fg=typer.colors.GREEN)
    if skip:
        typer.secho(f"  Skipped (missing source): {skip}", fg=typer.colors.YELLOW)
    if bad_coords:
        typer.secho(f"  Skipped (bad/missing coords): {bad_coords}", fg=typer.colors.YELLOW)


@app.command()
def export_sql(
    ocr_result_file: Path = typer.Argument(..., exists=True, help="OCR result JSON with image URLs"),
    output_sql: Path = typer.Argument(..., help="Path to write the .sql script"),
    image_base_url: str = typer.Option(
        None, "--image-base-url",
        help="Base URL for images (optional override)",
    ),
):
    """Export OCR results to D1-compatible SQLite INSERT statements.

    Reads the OCR JSON and emits SQL for the validator frontend schema:
    - problems  (cerinta, explicatie, instruction_count)
    - images    (problem_id, link, ai_description, crop_*)

    Use this to populate the D1 database after uploading images to R2.
    """
    from exam_processor.sql_export import generate_sql

    pb, im = generate_sql(
        str(ocr_result_file),
        str(output_sql),
        image_base_url=image_base_url,
    )
    typer.secho(
        f"Wrote {pb} problems + {im} images → {output_sql}",
        fg=typer.colors.GREEN,
    )



@app.command()
def figure_filter(
    ctx: typer.Context,
    input_folder: Path = typer.Argument(
        ..., exists=True, help="Input folder containing documents (scanned recursively)",
    ),
    output_txt: Path = typer.Option(
        ..., "--output-txt", "-o", help="Path to save the results txt file",
    ),
    extensions: str = typer.Option(
        "pdf,docx,doc,pptx,ppt",
        "--extensions", "-e",
        help="Comma-separated list of file extensions to process",
    ),
    use_temp_images: bool = typer.Option(
        False, "--temp-images", "-i",
        help="Convert pages to images in a temp dir (useful for pipeline debugging)",
    ),
):
    """
    Filter documents that contain figures using layout detection.

    Analyzes all documents in INPUT_FOLDER, using PP-StructureV3-like detection
    (via PyMuPDF) to find pages with geometric figures. Images near the top of
    the page (headers, decorative elements) are excluded. The result is a txt
    file listing docs with figures and docs without.

    This helps narrow down the dataset to only documents likely to have
    geometric figures for further processing.
    """
    run_figure_filter(
        str(input_folder),
        str(output_txt),
        extensions=extensions,
        use_temp_images=use_temp_images,
        verbose=get_verbose(ctx),
    )


if __name__ == "__main__":
    app()

