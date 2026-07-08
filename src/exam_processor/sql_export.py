import json
from pathlib import Path
from typing import Optional


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def _count_cdl_instructions(cdl_description: str | None) -> int:
    if not cdl_description:
        return 0
    return len([line for line in cdl_description.strip().split("\n") if line.strip()])


def generate_sql(
    ocr_result_file: str,
    output_sql: str,
    image_base_url: Optional[str] = None,
    instruction_count: int = 0,
) -> tuple[int, int]:
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

            def _geometric_imgs(container: list) -> list:
                out = []
                for img in container:
                    if img.get("cdl_is_geometric") is False:
                        continue
                    link = _resolve_link(img, image_base_url)
                    if not link:
                        continue
                    out.append(img)
                return out

            prob_images = _geometric_imgs(prob.get("imagini", []))
            barem_images = _geometric_imgs(barem.get("imagini", [])) if barem else []

            if not prob_images and not barem_images:
                continue

            total_instructions = sum(
                _count_cdl_instructions(img.get("cdl_description", ""))
                for img in prob_images + barem_images
            ) or instruction_count

            lines.append(
                f"INSERT INTO problems (cerinta, explicatie, instruction_count) "
                f"VALUES ('{cerinta}', '{explicatie}', {total_instructions});"
            )
            problems_count += 1

            def _img_sql(img: dict, link: str) -> str:
                ai_desc = _escape_sql(img.get("cdl_description", img.get("description", "")))
                x0, y0, x1, y1 = img.get("coordinates", [0, 0, 0, 0])
                return (
                    f"INSERT INTO images (problem_id, link, ai_description, "
                    f"crop_top, crop_left, crop_width, crop_height) "
                    f"VALUES (last_insert_rowid(), '{link}', '{ai_desc}', "
                    f"{int(y0)}, {int(x0)}, {int(x1-x0)}, {int(y1-y0)});"
                )

            for img in prob_images:
                lines.append(_img_sql(img, _resolve_link(img, image_base_url)))
                images_count += 1
            for img in barem_images:
                lines.append(_img_sql(img, _resolve_link(img, image_base_url)))
                images_count += 1

    lines.extend(["", "COMMIT;", ""])
    with open(output_sql, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return problems_count, images_count


def _resolve_link(img_entry: dict, image_base_url: Optional[str]) -> str | None:
    url = img_entry.get("image_url")
    if url:
        return url
    rel = img_entry.get("image_path")
    if not rel:
        return None
    if image_base_url:
        return f"{image_base_url.rstrip('/')}/{rel.lstrip('/')}"
    return rel

