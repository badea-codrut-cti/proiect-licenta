"""Export OCR results to D1-compatible SQL for the validator frontend."""

import json
import sqlite3
from pathlib import Path
from typing import Optional


def _escape_sql(value: str) -> str:
    """Escape a string for SQLite insertion."""
    return value.replace("'", "''")


def _count_cdl_instructions(cdl_description: str | None) -> int:
    """Count CDL predicates (one per line) in a description."""
    if not cdl_description:
        return 0
    return len([line for line in cdl_description.strip().split("\n") if line.strip()])


def generate_sql(
    ocr_result_file: str,
    output_sql: str,
    image_base_url: Optional[str] = None,
    instruction_count: int = 0,  # deprecated: computed from CDL per-problem now
) -> tuple[int, int]:
    """Generate SQLite INSERT statements for the validator frontend schema.

    Reads the OCR+CDL result JSON and emits SQL for:
        - problems  (cerinta, explicatie, instruction_count)
        - images    (problem_id, link, ai_description, crop_top, crop_left, crop_width, crop_height)

    Only exports images that have cdl_is_geometric=True.  ai_description comes from
    cdl_description (the structured CDL output), not the old OCR 'description' blurb.

    Args:
        ocr_result_file: Path to the JSON produced by retrieve_ocr_extraction **after**
            retrieve_cdl_extraction has merged CDL fields back in.
        output_sql: Path to write the generated .sql script.
        image_base_url: Optional base URL to prepend to relative image_path values.
        instruction_count: Deprecated fallback; now derived from CDL description line count.

    Returns:
        (number_of_problems, number_of_images)
    """
    with open(ocr_result_file, "r", encoding="utf-8") as f:
        ocr_data: dict[str, list] = json.load(f)

    lines: list[str] = ["BEGIN TRANSACTION;", ""]
    problems_count = 0
    images_count = 0

    for source_pdf, problems in ocr_data.items():
        for prob in problems:
            cerinta = _escape_sql(prob.get("cerinta", ""))
            barem = prob.get("barem")
            explicatie = _escape_sql(barem["explicatie"] if barem else "")

            # Gather subject images that are geometric and have CDL
            prob_images = []
            for img in prob.get("imagini", []):
                if img.get("cdl_is_geometric") is False:
                    continue
                link = _resolve_link(img, image_base_url)
                if not link:
                    continue
                prob_images.append(img)

            # Gather barem images that are geometric and have CDL
            barem_images = []
            if barem:
                for img in barem.get("imagini", []):
                    if img.get("cdl_is_geometric") is False:
                        continue
                    link = _resolve_link(img, image_base_url)
                    if not link:
                        continue
                    barem_images.append(img)

            if not prob_images and not barem_images:
                continue  # skip problems with no geometric figures

            # Compute instruction_count from CDL descriptions (number of predicates)
            all_cdl_descs = [
                img.get("cdl_description", "")
                for img in prob_images + barem_images
            ]
            total_instructions = sum(_count_cdl_instructions(d) for d in all_cdl_descs)
            if total_instructions == 0:
                total_instructions = instruction_count  # fallback

            lines.append(
                f"INSERT INTO problems (cerinta, explicatie, instruction_count) "
                f"VALUES ('{cerinta}', '{explicatie}', {total_instructions});"
            )
            problems_count += 1

            def _img_sql(img: dict) -> str:
                ai_desc = _escape_sql(img.get("cdl_description", img.get("description", "")))
                x0, y0, x1, y1 = img.get("coordinates", [0, 0, 0, 0])
                return (
                    f"INSERT INTO images (problem_id, link, ai_description, "
                    f"crop_top, crop_left, crop_width, crop_height) "
                    f"VALUES (last_insert_rowid(), '{link}', '{ai_desc}', "
                    f"{int(y0)}, {int(x0)}, {int(x1-x0)}, {int(y1-y0)});"
                )

            for img in prob_images:
                lines.append(_img_sql(img))
                images_count += 1

            for img in barem_images:
                lines.append(_img_sql(img))
                images_count += 1

    lines.extend(["", "COMMIT;", ""])

    with open(output_sql, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return problems_count, images_count


def _resolve_link(img_entry: dict, image_base_url: Optional[str]) -> str | None:
    """Pick the best URL/path for an image entry."""
    # Prefer already-resolved public URL
    url = img_entry.get("image_url")
    if url:
        return url

    # Fallback: relative path + optional base prefix
    rel = img_entry.get("image_path")
    if not rel:
        return None

    if image_base_url:
        return f"{image_base_url.rstrip('/')}/{rel.lstrip('/')}"
    return rel
