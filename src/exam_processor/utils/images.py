import base64
import io
from pathlib import Path
from typing import Union

from PIL import Image as PILImage

from exam_processor.utils.models import DEFAULT_IMAGE_QUALITY, DEFAULT_OUTER_PADDING

PathLike = Union[str, Path]


def image_to_base64(image_path: str | Path) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


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
    source,
    coordinates: list[float],
    *,
    outer_padding: float = DEFAULT_OUTER_PADDING,
    scale: float = 1.0,
    allow_degenerate: bool = True,
):
    if not coordinates or len(coordinates) != 4:
        return None
    try:
        c = [float(v) for v in coordinates]
    except (TypeError, ValueError):
        return None

    owns = isinstance(source, PILImage.Image)
    img = source if owns else None
    if img is None:
        try:
            img = PILImage.open(source)
        except Exception:
            return None
    try:
        w, h = img.size
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
            if not allow_degenerate:
                return None
            x1 = min(x0 + 1, w)
            y1 = min(y0 + 1, h)
            if x1 <= x0 or y1 <= y0:
                return None
        return img.crop((x0, y0, x1, y1)).convert("RGB")
    finally:
        if not owns:
            img.close()


def image_to_base64_bytes(img: PILImage.Image, fmt: str = "JPEG", quality: int = DEFAULT_IMAGE_QUALITY) -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def render_page_image(pdf_or_path, page_number: int, *, dpi: int = 200) -> PILImage.Image | None:
    if isinstance(pdf_or_path, PILImage.Image):
        return pdf_or_path.copy()
    p = Path(pdf_or_path)
    if p.exists() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return PILImage.open(p).convert("RGB")
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(str(pdf_or_path))
        if page_number < 1 or page_number > doc.page_count:
            doc.close()
            return None
        page = doc[page_number - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
        return img
    except Exception:
        return None


def crop_and_save(
    source,
    coordinates: list[float],
    output_path: PathLike,
    *,
    outer_padding: float = DEFAULT_OUTER_PADDING,
    scale: float = 1.0,
    quality: int = DEFAULT_IMAGE_QUALITY,
) -> bool:
    crop = crop_image(source, coordinates, outer_padding=outer_padding, scale=scale)
    if crop is None:
        return False
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out, format="JPEG", quality=quality)
    crop.close()
    return True