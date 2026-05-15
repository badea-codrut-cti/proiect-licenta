"""Figure filtering step using PP-StructureV3-like layout detection.

Uses PyMuPDF to detect images/figures in documents, filtering out decorative
header/footer images. Documents with geometric figures are kept.
"""

import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from tqdm import tqdm

# Try importing PyMuPDF for image detection
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None

# Headers typically occupy the top ~15% of the page
HEADER_RATIO_THRESHOLD = 0.15
# Minimum image area to be considered a meaningful figure (not a tiny icon/decoration)
MIN_FIGURE_AREA = 5000  # square pixels


def get_page_image_count_fitz(doc_path: str) -> tuple[int, int, list[tuple[int, list[dict]]]]:
    """Analyze a document using PyMuPDF to detect image blocks per page.
    
    Returns:
        tuple of (total_pages_count, pages_with_figures, per_page_image_info)
        where per_page_image_info is [(page_num, [image_info_dicts, ...]), ...]
    """
    doc = fitz.open(doc_path)
    total_pages = len(doc)
    pages_with_figures = 0
    per_page_info = []
    
    for page_num in range(total_pages):
        page = doc[page_num]
        page_height = page.rect.height
        page_width = page.rect.width
        header_threshold_y = page_height * HEADER_RATIO_THRESHOLD
        
        # Get text dict which includes image blocks (type=1)
        text_dict = page.get_text("dict")
        image_blocks = []
        
        for block in text_dict.get("blocks", []):
            if block.get("type") != 1:  # type 1 = image block
                continue
            
            bbox = block.get("bbox")
            if not bbox:
                continue
            
            x0, y0, x1, y1 = bbox
            area = (x1 - x0) * (y1 - y0)
            
            # Skip tiny images (decorative elements)
            if area < MIN_FIGURE_AREA:
                continue
            
            # Check if the image is in the header zone (near top of page)
            is_header = y0 < header_threshold_y and y1 < header_threshold_y * 2
            
            image_info = {
                "bbox": [x0, y0, x1, y1],
                "area": area,
                "is_header": is_header,
                "width": x1 - x0,
                "height": y1 - y0,
            }
            image_blocks.append(image_info)
        
        per_page_info.append((page_num, image_blocks))
        
        # A page has a "figure" if it has at least one non-header image
        has_figure = any(not img["is_header"] for img in image_blocks)
        if has_figure:
            pages_with_figures += 1
    
    doc.close()
    return total_pages, pages_with_figures, per_page_info


def detect_figures_in_pdf(
    pdf_path: str,
    use_temp_images: bool = False,
    temp_dir: Optional[str] = None,
) -> dict:
    """Detect figures in a PDF document.
    
    Args:
        pdf_path: Path to the PDF file.
        use_temp_images: If True, also convert pages to images in temp dir.
        temp_dir: Optional temp directory for page images.
        
    Returns:
        dict with detection results.
    """
    result = {
        "has_figures": False,
        "total_pages": 0,
        "pages_with_figures": 0,
        "pages_with_images": 0,
        "total_image_blocks": 0,
        "page_images_created": 0,
    }
    
    # Convert PDF to images if requested (for the model pipeline)
    if use_temp_images and convert_from_path:
        try:
            page_images = convert_from_path(pdf_path, dpi=150)
            result["page_images_created"] = len(page_images)
            
            if temp_dir:
                base_name = Path(pdf_path).stem
                for i, img in enumerate(page_images):
                    img_path = Path(temp_dir) / f"{base_name}_page_{i:04d}.png"
                    img.save(str(img_path), "PNG")
        except Exception as e:
            print(f"  Warning: Could not convert {pdf_path} to images: {e}")
    
    # Analyze PDF with PyMuPDF
    if fitz:
        try:
            total_pages, pages_with_figures, per_page_info = get_page_image_count_fitz(pdf_path)
            result["total_pages"] = total_pages
            result["pages_with_figures"] = pages_with_figures
            
            all_image_blocks = []
            has_non_header = False
            for page_num, image_blocks in per_page_info:
                all_image_blocks.extend(image_blocks)
                for img in image_blocks:
                    if not img["is_header"]:
                        has_non_header = True
            
            result["total_image_blocks"] = len(all_image_blocks)
            result["has_figures"] = has_non_header
            result["pages_with_any_image"] = sum(1 for _, imgs in per_page_info if imgs)
        except Exception as e:
            print(f"  Warning: PyMuPDF analysis failed for {pdf_path}: {e}")
            # Fall back: assume no figures
            result["has_figures"] = False
    else:
        # Without PyMuPDF, fall back to using pdf2image count
        print("  Warning: PyMuPDF not installed, falling back to basic page count")
    
    return result


def figure_filter_pipeline(
    input_folder: str,
    output_txt: str,
    extensions: str = "pdf,docx,doc,pptx,ppt",
    use_temp_images: bool = False,
    verbose: bool = False,
) -> dict:
    """Run figure detection on all documents in a folder.
    
    Args:
        input_folder: Path to folder containing documents (recursive).
        output_txt: Path to save the txt file with doc paths.
        extensions: Comma-separated list of file extensions to process.
        use_temp_images: If True, also convert pages to images in temp dir.
        verbose: Enable verbose output.
        
    Returns:
        dict with summary stats.
    """
    ext_set = {f".{ext.strip().lower()}" for ext in extensions.split(",")}
    
    # Collect all documents, skipping macOS resource fork files (._ prefix, __MACOSX)
    all_files = []
    input_path = Path(input_folder)
    for ext in ext_set:
        for f in input_path.rglob(f"*{ext}"):
    # Skip macOS resource fork files and hidden files
            if f.name.startswith("._") or "__MACOSX" in str(f):
                continue
            if f.name.startswith("~$"):
                continue
            # Skip .doc files that can't be opened by PyMuPDF (they're in old binary format)
            if f.suffix.lower() == ".doc":
                continue
            # Skip files that are basically empty (macOS resource files, etc.)
            if f.stat().st_size == 0:
                continue
            all_files.append(f)
    
    all_files = sorted(set(all_files))
    total_docs = len(all_files)
    
    if total_docs == 0:
        typer.secho("No supported documents found.", fg=typer.colors.YELLOW)
        return {"total": 0, "with_figures": 0, "without_figures": 0}
    
    # Create temp dir for page images if needed
    temp_image_dir = None
    if use_temp_images:
        temp_image_dir = Path(tempfile.mkdtemp(prefix="figure_filter_pages_"))
        if verbose:
            typer.echo(f"Temp image dir: {temp_image_dir}")
    
    # Process each document with progress bar
    docs_with_figures = []
    docs_without_figures = []
    total_page_images = 0
    
    typer.echo(f"Processing {total_docs} document(s)...")
    for file_path in tqdm(all_files, desc="Detecting figures", unit="doc"):
        if verbose:
            typer.echo(f"\n  Analyzing: {file_path.relative_to(input_path)}")
        
        result = detect_figures_in_pdf(
            str(file_path),
            use_temp_images=use_temp_images,
            temp_dir=str(temp_image_dir) if temp_image_dir else None,
        )
        
        total_page_images += result.get("page_images_created", 0)
        
        if result["has_figures"]:
            docs_with_figures.append(str(file_path))
            if verbose:
                typer.secho(
                    f"  ✓ Figures detected ({result['pages_with_figures']}/{result['total_pages']} pages)",
                    fg=typer.colors.GREEN,
                )
        else:
            docs_without_figures.append(str(file_path))
            if verbose:
                has_any = result.get("pages_with_any_image", 0)
                if has_any > 0:
                    typer.secho(f"  ✗ Only header images ({has_any} pages)", fg=typer.colors.YELLOW)
                else:
                    typer.secho(f"  ✗ No images found", fg=typer.colors.BLUE)
    
    # Write results to txt file
    output_path = Path(output_txt)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Documents with figures detected in: {input_folder}\n")
        f.write(f"# Total documents: {total_docs}\n")
        f.write(f"# Docs with figures: {len(docs_with_figures)}\n")
        f.write(f"# Docs without figures: {len(docs_without_figures)}\n")
        f.write(f"# Generated: {datetime.now().isoformat()}\n\n")
        
        f.write("=== DOCS WITH FIGURES ===\n")
        for path in docs_with_figures:
            f.write(f"{path}\n")
        
        f.write("\n=== DOCS WITHOUT FIGURES ===\n")
        for path in docs_without_figures:
            f.write(f"{path}\n")
    
    # Clean up temp images
    if temp_image_dir and temp_image_dir.exists():
        if verbose:
            typer.echo(f"Cleaning up {total_page_images} temp page images...")
        shutil.rmtree(temp_image_dir)
    
    summary = {
        "total": total_docs,
        "with_figures": len(docs_with_figures),
        "without_figures": len(docs_without_figures),
    }
    
    return summary


def run_figure_filter(
    ctx: typer.Context,
    input_folder: str,
    output_txt: str,
    extensions: str = "pdf,docx,doc,pptx,ppt",
    use_temp_images: bool = False,
):
    """CLI entry point for figure filtering."""
    verbose = ctx.meta.get("verbose", False)
    
    if not fitz:
        typer.secho(
            "Warning: PyMuPDF (fitz) not installed. Install with: pip install PyMuPDF\n"
            "Falling back to basic detection.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    
    summary = figure_filter_pipeline(
        input_folder=input_folder,
        output_txt=output_txt,
        extensions=extensions,
        use_temp_images=use_temp_images,
        verbose=verbose,
    )
    
    # Print summary
    typer.echo()
    typer.secho("=" * 50, fg=typer.colors.CYAN)
    typer.secho("Figure Detection Summary", bold=True, fg=typer.colors.CYAN)
    typer.secho("=" * 50, fg=typer.colors.CYAN)
    typer.echo(f"  Total documents scanned: {summary['total']}")
    
    if summary["with_figures"] > 0:
        typer.secho(f"  With figures: {summary['with_figures']}", fg=typer.colors.GREEN, bold=True)
    else:
        typer.echo(f"  With figures: 0")
    
    if summary["without_figures"] > 0:
        typer.secho(f"  Without figures: {summary['without_figures']}", fg=typer.colors.BLUE)
    else:
        typer.echo(f"  Without figures: 0")
    
    ratio = summary["with_figures"] / summary["total"] * 100 if summary["total"] > 0 else 0
    typer.echo(f"  Ratio: {summary['with_figures']}/{summary['total']} ({ratio:.1f}%)")
    typer.echo()
    typer.secho(f"Results saved to: {output_txt}", fg=typer.colors.GREEN)
    
    if use_temp_images:
        typer.echo("  (Temp page images were created and cleaned up)")
