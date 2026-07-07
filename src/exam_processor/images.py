"""Image helpers shared across the OCR / CDL-NL / consistency stages.

These functions cover the two recurring concerns every batch stage has:
reading an image file into a base64 string for the multimodal API, and doing
arithmetic on figure bounding-box coordinates (normalised-vs-pixel mode
detection plus the outer-padding expansion that's applied before cropping).
"""

import base64
from pathlib import Path

from exam_processor.models import DEFAULT_OUTER_PADDING


def image_to_base64(image_path: str | Path) -> str:
    """Read ``image_path`` from disk and return its base64-encoded bytes.

    Encoding is format-agnostic: this is just raw-bytes -> base64.  The image
    happens to be JPEG in every current call site, but the function does not
    inspect or convert the bytes; whatever is on disk is what gets sent.
    """
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def is_normalized(coords: list[float]) -> bool:
    """True iff ``coords`` is a 4-element bounding box expressed as 0..1 page-relative decimals.

    The check is intentionally ``len(coords) == 4`` *and* ``all 0..1``: pixels
    can also happen to all fall in 0..1 for a very tiny image, so the length
    gate is what makes the test meaningful.  Used to decide whether to scale
    the box by page pixel dimensions or treat it as already-in-pixels.
    """
    return len(coords) == 4 and all(0.0 <= v <= 1.0 for v in coords)


def apply_outer_padding(
    coords: list[float],
    padding: float = DEFAULT_OUTER_PADDING,
    max_w: float = 0,
    max_h: float = 0,
) -> list[float]:
    """Expand a bounding box by ``padding`` fraction of its own size on each side.

    Used immediately before cropping a figure out of a page image so the model
    gets a little surrounding margin instead of a hard-clipped box.

    Works in BOTH pixel and 0..1-normalised coordinate systems.  The contract
    is: ``max_w`` and ``max_h`` are passed in the SAME units as ``coords`` so
    the out-of-page clamp behaves correctly in either mode.  The two call
    shapes actually in use in the codebase are:

      * CDL / consistency crop rectangles -- coords are 0..1,
        ``max_w = max_h = 1.0`` (clamp to the unit page).
      * ``cli.crop_boxes`` debug viewer -- coords are pixels (already multiplied
        by page width/height), ``max_w`` / ``max_h`` are the page's pixel
        dimensions.

    Args:
        coords: ``[x0, y0, x1, y1]`` in either pixels or 0..1 normalized form.
        padding: fraction of the box's own width/height to add on each side
            (e.g. ``0.15`` = +15% on left, top, right, bottom).
        max_w, max_h: if > 0, clamp the padded result to ``[0, max_w]`` /
            ``[0, max_h]``.  Must be in the same units as ``coords``.

    Returns:
        New padded ``[x0, y0, x1, y1]`` in the same units as the input.
    """
    x0, y0, x1, y1 = coords
    w = x1 - x0
    h = y1 - y0
    dx = w * padding
    dy = h * padding
    x0 -= dx
    y0 -= dy
    x1 += dx
    y1 += dy
    if max_w > 0:
        x0 = max(0.0, x0)
        x1 = min(max_w, x1)
    if max_h > 0:
        y0 = max(0.0, y0)
        y1 = min(max_h, y1)
    return [x0, y0, x1, y1]