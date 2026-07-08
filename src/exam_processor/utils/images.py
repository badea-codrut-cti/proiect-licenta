from typing import TYPE_CHECKING

from PIL import Image as PILImage

from exam_processor.utils.models import DEFAULT_OUTER_PADDING

if TYPE_CHECKING:
    import fitz


def is_normalized(coords: list[float]) -> bool:
    return len(coords) == 4 and all(0.0 <= v <= 1.0 for v in coords)


def apply_outer_padding(
    coords: list[float],
    padding: float = DEFAULT_OUTER_PADDING,
    max_w: float = 0,
    max_h: float = 0,
) -> list[float]:
    x0, y0, x1, y1 = coords
    dx = (x1 - x0) * padding
    dy = (y1 - y0) * padding
    x0, y0, x1, y1 = x0 - dx, y0 - dy, x1 + dx, y1 + dy
    if max_w > 0:
        x0, x1 = max(0.0, x0), min(max_w, x1)
    if max_h > 0:
        y0, y1 = max(0.0, y0), min(max_h, y1)
    return [x0, y0, x1, y1]


def crop_image(
    source: PILImage.Image,
    coordinates: list[float],
    *,
    outer_padding: float = DEFAULT_OUTER_PADDING,
    scale: float = 1.0,
) -> PILImage.Image | None:
    if not coordinates or len(coordinates) != 4:
        return None
    try:
        c = [float(v) for v in coordinates]
    except (TypeError, ValueError):
        return None

    w, h = source.size
    if is_normalized(c) and scale == 1.0:
        padded = apply_outer_padding(c, padding=outer_padding, max_w=1.0, max_h=1.0)
        px = [padded[i] * (w if i % 2 == 0 else h) for i in range(4)]
    else:
        scaled = [v * scale for v in c]
        px = apply_outer_padding(scaled, padding=outer_padding, max_w=w, max_h=h)

    x0, y0, x1, y1 = (int(v) for v in px)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return source.crop((x0, y0, x1, y1)).convert("RGB")


def render_pdf_page(doc: "fitz.Document", page_idx: int, *, dpi: int = 200) -> PILImage.Image:
    if page_idx < 0 or page_idx >= doc.page_count:
        raise IndexError(f"page_idx {page_idx} out of range [0, {doc.page_count})")
    import fitz
    page = doc[page_idx]
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
    return PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)

