"""Atomic filesystem helpers used by all batch stages for crash-safe flushes.

Every batch stage (OCR / CDL-NL / consistency) writes its work-in-progress
JSON to disk after each API call so a crash or Ctrl-C is recoverable, and the
flush must be crash-safe: a half-written file must never be observable by a
later reader.  Hence the write-then-``os.replace`` pattern encapsulated here.
"""

import os
from pathlib import Path


def write_atomic(path: str | Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp sibling + atomic ``os.replace``.

    Crash-safe semantics: a crash mid-write leaves the previous content of
    ``path`` intact (the temp sibling either gets fully materialised and
    atomically renamed over ``path``, or it doesn't).  The temp file is placed
    next to ``path`` so the rename stays on the same filesystem --
    ``os.replace`` is only atomic within a single filesystem.
    """
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)