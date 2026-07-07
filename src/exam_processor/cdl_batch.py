"""Description extraction via real-time chat completions.

Reads OCR result JSON, crops each figure image, sends CDL + NL requests
to N models in parallel via the Together AI real-time API.
"""

import base64
import json
import os
import tempfile
import threading
import concurrent.futures
from pathlib import Path
from typing import Any, Optional

from PIL import Image
from tqdm import tqdm

from exam_processor.client import TogetherClient
from exam_processor.models import DEFAULT_DESCRIPTION_MODELS, DEFAULT_OUTER_PADDING
from exam_processor.schemas import CdlDescription, NlDescription, ConsistencyVerdict


def _write_atomic(path: Path, text: str) -> None:
    """Write text to path via a temp file + atomic rename (crash-safe)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _cdl_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "CdlDescription",
            "schema": CdlDescription.model_json_schema(),
        },
    }


def _is_normalized(coords: list[float]) -> bool:
    return len(coords) == 4 and all(0.0 <= v <= 1.0 for v in coords)


def apply_outer_padding(
    coords: list[float],
    padding: float = DEFAULT_OUTER_PADDING,
    max_w: float = 0,
    max_h: float = 0,
) -> list[float]:
    """Expand a bounding box by `padding` fraction of its own size on each side.

    Args:
        coords: [x0, y0, x1, y1].  Can be pixels or 0-1 normalized.
        padding: fraction of box width/height to add to each side (e.g. 0.15).
        max_w, max_h: if >0, clamp result to [0, max_w] / [0, max_h].
    Returns:
        New padded coords in the same units as input.
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


def _nl_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "NlDescription",
            "schema": NlDescription.model_json_schema(),
        },
    }


def _crop_image(
    page_img_path: str | Path,
    coordinates: list[float],
    output_dir: Path,
    suffix: str,
    quality: int = 90,
    outer_padding: float = DEFAULT_OUTER_PADDING,
) -> Path:
    """Crop a figure from a page image with optional outer padding."""
    img = Image.open(page_img_path)
    x0, y0, x1, y1 = coordinates
    w, h = img.size
    if _is_normalized(coordinates):
        x0 *= w
        y0 *= h
        x1 *= w
        y1 *= h
    padded = apply_outer_padding([x0, y0, x1, y1], padding=outer_padding, max_w=w, max_h=h)
    x0, y0, x1, y1 = [int(v) for v in padded]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0:
        x1 = min(x0 + 1, w)
    if y1 <= y0:
        y1 = min(y0 + 1, h)
    cropped = img.crop((x0, y0, x1, y1))
    out = output_dir / f"{Path(page_img_path).stem}_{suffix}.jpg"
    cropped.convert("RGB").save(out, format="JPEG", quality=quality)
    return out


def _image_to_base64(image_path: str | Path) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _build_prompt(template: str, cerinta: str, barem_explicatie: str | None) -> str:
    prompt = template.replace("{{PROBLEM_TASK}}", cerinta)
    if barem_explicatie:
        prompt = prompt.replace("{{CONTEXT}}", barem_explicatie, 1)
        prompt = prompt.replace("{{CONTEXT}}", "", 1)
    else:
        prompt = prompt.replace("{{CONTEXT}}", "", 2)
    return prompt


def _extra_model_kwargs(model: str) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if "kimi" in model.lower():
        extra["reasoning"] = {"enabled": False}
    return extra


def _call_model(
    client: TogetherClient,
    model: str,
    messages: list[dict],
    response_format: dict,
    max_tokens: int = 32768,
    temperature: float = 0.2,
) -> tuple[Optional[dict], int, int]:
    """Send a real-time chat completion and return (parsed_dict, in_tokens, out_tokens)."""
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }
        kwargs.update(_extra_model_kwargs(model))
        response = client.chat_completion(**kwargs)
        content = response.choices[0].message.content
        obj = json.loads(content)
        usage = response.usage
        return obj, usage.prompt_tokens, usage.completion_tokens
    except Exception as e:
        print(f"[ERROR] {model} failed: {e}")
        return None, 0, 0


def _resolve_page_image(
    source_pdf: str,
    page_number: int,
    fig: dict,
    image_base: Path,
) -> Path | None:
    """Find the already-saved full-page JPEG for (source_pdf, page_number).

    The OCR stage stored each figure entry's ``image_path`` (relative to
    IMAGE_BASE_DIR).  Multiple figures on the same page share that JPEG, so we
    resolve it from the first figure we find on that page instead of rendering
    the source PDF again.  Returns None if no suitable saved image exists.
    """
    rel = fig.get("image_path")
    if not rel:
        return None
    p = Path(rel)
    candidate = p if p.is_absolute() else image_base / p
    if candidate.exists():
        return candidate
    return None


def _render_and_crop(
    pdf_path: str,
    page_number: int,
    coordinates: list[float],
    output_dir: Path,
    suffix: str,
    dpi: int = 200,
    quality: int = 90,
    outer_padding: float = DEFAULT_OUTER_PADDING,
    page_image_path: str | Path | None = None,
) -> Path:
    """Crop a figure from a rendered page image, with optional outer padding.

    Coordinate resolution order (coordinates are 0-1 page-relative decimals):
      1. If ``page_image_path`` is given, crop from it (preferred: matches what
         the OCR model saw, works for .doc sources, faster).
      2. Otherwise render the source PDF page via PyMuPDF and crop from that.

    The crop is saved as ``crop_{suffix}.jpg`` under ``output_dir``.
    """
    if page_image_path is not None and Path(page_image_path).exists():
        img = Image.open(page_image_path)
        w, h = img.size
        x0, y0, x1, y1 = coordinates
        # coordinates are 0-1 page-relative -> multiply by page-pixel dims
        padded = apply_outer_padding([x0, y0, x1, y1], padding=outer_padding, max_w=1.0, max_h=1.0)
        px0, py0, px1, py1 = [int(padded[i] * (w if i % 2 == 0 else h)) for i in range(4)]
        px0, py0 = max(0, px0), max(0, py0)
        px1, py1 = min(w, px1), min(h, py1)
        if px1 <= px0:
            px1 = min(px0 + 1, w)
        if py1 <= py0:
            py1 = min(py0 + 1, h)
        cropped = img.crop((px0, py0, px1, py1))
        out = output_dir / f"crop_{suffix}.jpg"
        cropped.convert("RGB").save(out, format="JPEG", quality=quality)
        img.close()
        return out

    # Fallback: render the source PDF page directly.
    import fitz

    doc = fitz.open(pdf_path)
    if page_number < 1 or page_number > doc.page_count:
        doc.close()
        raise ValueError(f"Page {page_number} out of range for {pdf_path}")

    page = doc[page_number - 1]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)

    with Image.frombytes("RGB", [pix.width, pix.height], pix.samples) as img:
        w, h = img.size
        x0, y0, x1, y1 = coordinates
        # coordinates are 0-1 page-relative -> multiply by rendered pixel dims
        padded = apply_outer_padding([x0, y0, x1, y1], padding=outer_padding, max_w=1.0, max_h=1.0)
        px0, py0, px1, py1 = [int(padded[i] * (w if i % 2 == 0 else h)) for i in range(4)]
        px0, py0 = max(0, px0), max(0, py0)
        px1, py1 = min(w, px1), min(h, py1)
        if px1 <= px0:
            px1 = min(px0 + 1, w)
        if py1 <= py0:
            py1 = min(py0 + 1, h)
        cropped = img.crop((px0, py0, px1, py1))
        out = output_dir / f"crop_{suffix}.jpg"
        cropped.convert("RGB").save(out, format="JPEG", quality=quality)

    doc.close()
    return out



class DescriptionExtraction:
    """Multi-model CDL + NL description extraction via real-time API."""

    def __init__(self, client: TogetherClient, prompts_dir: str = "prompts", max_workers: int = 10):
        self.client = client
        self.prompts_dir = prompts_dir
        self.max_workers = max_workers

    def run(
        self,
        ocr_result_file: str,
        image_base_dir: str,
        output_file: str,
        models: Optional[list[str]] = None,
        verbose: bool = False,
        crop_work_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run CDL + NL extraction over all figures.

        Resumable: every completed (base_id, model, task) result is flushed to
        `output_file` and a `.done` set next to it after each response, so a
        crash or Ctrl-C loses at most the in-flight requests. Re-running picks up
        where it left off.

        Args:
            crop_work_dir: where to stash cropped figure JPEGs. Defaults to a
                temp dir (cleaned up at the end); pass a real path to keep them.
        """
        models = models or DEFAULT_DESCRIPTION_MODELS
        cdl_tpl = (Path(self.prompts_dir) / "image_to_cdl.txt").read_text(encoding="utf-8")
        nl_tpl  = (Path(self.prompts_dir) / "image_to_nl.txt").read_text(encoding="utf-8")

        with open(ocr_result_file, "r", encoding="utf-8") as f:
            ocr_data: dict[str, list] = json.load(f)

        image_base = Path(image_base_dir)
        out_path = Path(output_file).parent
        out_path.mkdir(parents=True, exist_ok=True)

        # Crop work dir: explicit path kept, or temp dir auto-cleaned.
        own_temp = False
        if crop_work_dir:
            crops_dir = Path(crop_work_dir)
            crops_dir.mkdir(parents=True, exist_ok=True)
        else:
            crops_dir = Path(tempfile.mkdtemp(prefix="exam_crops_"))
            own_temp = True

        # Flatten all figures
        figures: list[dict] = []
        for doc_idx, (source_pdf, problems) in enumerate(ocr_data.items()):
            for prob_idx, prob in enumerate(problems):
                cerinta = prob.get("cerinta", "")
                barem = prob.get("barem")
                barem_text = barem.get("explicatie", "") if barem else None
                def _add(kind: str, entry: dict, img_idx: int) -> None:
                    base_id = f"d{doc_idx:04d}-p{prob_idx:04d}-{kind[0]}{img_idx:04d}"
                    page_img = _resolve_page_image(source_pdf, entry.get("page_number", 0), entry, image_base)
                    try:
                        crop = _render_and_crop(
                            source_pdf,
                            entry["page_number"],
                            entry["coordinates"],
                            crops_dir,
                            base_id,
                            page_image_path=page_img,
                        )
                    except Exception as e:
                        print(f"[WARNING] Failed to render/crop {source_pdf} page {entry['page_number']}: {e}")
                        return
                    figures.append({
                        "source_pdf": source_pdf, "doc_idx": doc_idx, "prob_idx": prob_idx,
                        "image_kind": kind, "img_idx": img_idx, "base_id": base_id,
                        "crop_path": str(crop), "cerinta": cerinta, "barem_text": barem_text,
                    })
                for i, e in enumerate(prob.get("imagini", [])):
                    _add("imagini", e, i)
                if barem:
                    for i, e in enumerate(barem.get("imagini", [])):
                        _add("barem", e, i)

        if not figures:
            raise ValueError("No figure images found.")
        if verbose:
            print(f"[DEBUG] {len(figures)} figures × {len(models)} models × 2 tasks = {len(figures)*len(models)*2} requests")

        # Resume: a `.done` file (written atomically alongside the output JSON)
        # records every (base_id, model, task) we've already completed. The merged
        # model_results in a pre-existing output JSON is also borrowed so re-runs
        # don't overwrite completed work nor waste an API call re-fetching it.
        out_json_path = Path(output_file)
        done_path = out_json_path.with_suffix(out_json_path.suffix + ".done")
        done: set[tuple[str, str, str]] = set()
        if out_json_path.exists():
            try:
                with open(out_json_path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                doc_index_of = {pdf: i for i, pdf in enumerate(ocr_data.keys())}
                for src_key, probs in prev.items():
                    if src_key not in doc_index_of:
                        continue
                    di = doc_index_of[src_key]
                    cur_probs = ocr_data[src_key]
                    for pi, p_pro in enumerate(probs):
                        p_cur = cur_probs[pi] if pi < len(cur_probs) else None
                        if not p_cur:
                            continue
                        def _scan(kind: str, c_pro: list, c_cur: list) -> None:
                            for ii, e_pro in enumerate(c_pro):
                                e_cur = c_cur[ii] if ii < len(c_cur) else None
                                if not e_cur:
                                    continue
                                mrs = e_pro.get("model_results", {})
                                if not mrs:
                                    continue
                                e_cur.setdefault("model_results", {}).update(mrs)
                                bid = f"d{di:04d}-p{pi:04d}-{kind[0]}{ii:04d}"
                                for m, res in mrs.items():
                                    if "cdl" in res:
                                        done.add((bid, m, "cdl"))
                                    if "nl" in res:
                                        done.add((bid, m, "nl"))
                        _scan("imagini", p_pro.get("imagini", []), p_cur.get("imagini", []))
                        if p_pro.get("barem") and p_cur.get("barem"):
                            _scan("barem", p_pro["barem"].get("imagini", []), p_cur["barem"].get("imagini", []))
            except Exception as e:
                print(f"[WARNING] Could not read previous output ({e}); starting fresh")
        if done_path.exists():
            try:
                with open(done_path, "r", encoding="utf-8") as f:
                    done |= {tuple(x) for x in json.load(f)}
            except Exception:
                pass

        # Build all tasks that are NOT already in `done`.
        tasks: list[tuple] = []
        for fig in figures:
            b64 = _image_to_base64(fig["crop_path"])
            prompt_cdl = _build_prompt(cdl_tpl, fig["cerinta"], fig["barem_text"])
            prompt_nl = _build_prompt(nl_tpl, fig["cerinta"], fig["barem_text"])
            for model in models:
                key_cdl = (fig["base_id"], model, "cdl")
                key_nl  = (fig["base_id"], model, "nl")
                if key_cdl not in done:
                    tasks.append((fig, model, "cdl", prompt_cdl, b64, _cdl_response_format()))
                if key_nl not in done:
                    tasks.append((fig, model, "nl", prompt_nl, b64, _nl_response_format()))

        if verbose and done:
            print(f"[DEBUG] Resuming: {len(done)} task(s) already done; {len(tasks)} remaining")
        if not tasks:
            print("[INFO] All tasks already complete — nothing to do.")
            # Still merge whatever is in ocr_data already and flush.
            _write_atomic(out_json_path, json.dumps(ocr_data, indent=2, ensure_ascii=False))
            return {"figures": len(figures), "models": models, "ok": 0, "fail": 0,
                    "total_input_tokens": 0, "total_output_tokens": 0, "resumed": len(done)}

        _write_lock = threading.Lock()

        def _flush() -> None:
            with _write_lock:
                try:
                    _write_atomic(out_json_path, json.dumps(ocr_data, indent=2, ensure_ascii=False))
                    _write_atomic(done_path, json.dumps(sorted([list(x) for x in done]), ensure_ascii=False))
                except Exception as e:
                    print(f"[WARNING] Failed to write incremental output: {e}")

        def _execute(t: tuple):
            fig, model, task, prompt, b64, resp_fmt = t
            messages = [
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}
            ]
            result, in_tok, out_tok = _call_model(self.client, model, messages, resp_fmt)
            return (fig["base_id"], model, task), (result, in_tok, out_tok)

        primary = models[0]
        ok = fail = total_in = total_out = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(_execute, t): t for t in tasks}
            for future in tqdm(concurrent.futures.as_completed(futs), total=len(futs), desc="Extracting"):
                key, val = future.result()
                base_id, model, task = key
                result, in_tok, out_tok = val
                total_in += in_tok; total_out += out_tok

                # Locate the image entry this task belongs to.
                fig = next(f for f in figures if f["base_id"] == base_id)
                probs = ocr_data[fig["source_pdf"]]
                imgs = (probs[fig["prob_idx"]].get("barem") or {}).get("imagini", []) if fig["image_kind"] == "barem" else probs[fig["prob_idx"]].get("imagini", [])
                img = imgs[fig["img_idx"]]
                mrs = img.setdefault("model_results", {})
                mr = mrs.setdefault(model, {})

                if result:
                    ok += 1
                    if task == "cdl":
                        mr["cdl"] = {
                            "is_geometric": result.get("is_geometric", True),
                            "description": result.get("description", ""),
                            "is_complete": result.get("is_complete", True),
                        }
                    else:
                        mr["nl"] = {
                            "is_geometric": result.get("is_geometric", True),
                            "natural_language": result.get("natural_language", ""),
                        }
                    # Legacy flat fields from primary model
                    if model == primary:
                        if task == "cdl":
                            img["cdl_is_geometric"] = result.get("is_geometric", True)
                            img["cdl_description"] = result.get("description", "")
                            img["cdl_is_complete"] = result.get("is_complete", True)
                        else:
                            img["nl_is_geometric"] = result.get("is_geometric", True)
                            img["natural_language"] = result.get("natural_language", "")
                else:
                    fail += 1

                done.add((base_id, model, task))
                _flush()

        # Final flush
        _flush()
        if own_temp:
            try:
                import shutil
                shutil.rmtree(crops_dir, ignore_errors=True)
            except Exception:
                pass
        print(f"[INFO] {ok} ok / {fail} fail — {total_in:,} in + {total_out:,} out tokens")
        return {"figures": len(figures), "models": models, "ok": ok, "fail": fail,
                "total_input_tokens": total_in, "total_output_tokens": total_out, "resumed": len(done) - ok - fail if 'done' in dir() else 0}


def _consistency_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ConsistencyVerdict",
            "schema": ConsistencyVerdict.model_json_schema(),
        },
    }


class ConsistencyAssessment:
    """Judge whether a model's CDL and NL descriptions of each figure agree."""

    DEFAULT_JUDGE_MODEL = "Qwen/Qwen3.5-9B"

    def __init__(self, client: TogetherClient, prompts_dir: str = "prompts", max_workers: int = 10):
        self.client = client
        self.prompts_dir = prompts_dir
        self.max_workers = max_workers

    def run(
        self,
        enriched_file: str,
        image_base_dir: str,
        output_file: str,
        model: Optional[str] = None,
        verbose: bool = False,
        crop_work_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run consistency assessment over every (image, model) CDL+NL pair.

        Resumable, exactly like DescriptionExtraction.run: each verdict is
        flushed atomically to `output_file` plus a `.done` set keyed by
        (base_id, model) so a crash/Ctrl-C is recoverable.
        """
        model = model or self.DEFAULT_JUDGE_MODEL
        tpl = (Path(self.prompts_dir) / "cdl_nl_consistency.txt").read_text(encoding="utf-8")

        with open(enriched_file, "r", encoding="utf-8") as f:
            ocr_data: dict[str, list] = json.load(f)

        image_base = Path(image_base_dir)
        out_path = Path(output_file).parent
        out_path.mkdir(parents=True, exist_ok=True)

        own_temp = False
        if crop_work_dir:
            crops_dir = Path(crop_work_dir)
            crops_dir.mkdir(parents=True, exist_ok=True)
        else:
            crops_dir = Path(tempfile.mkdtemp(prefix="exam_judge_crops_"))
            own_temp = True

        # Flatten figures that already have both cdl + nl for at least one model.
        pairs: list[dict] = []
        for doc_idx, (source_pdf, problems) in enumerate(ocr_data.items()):
            for prob_idx, prob in enumerate(problems):
                cerinta = prob.get("cerinta", "")
                barem = prob.get("barem")
                barem_text = barem.get("explicatie", "") if barem else None

                def _add(kind: str, container: list, img_idx: int, entry: dict) -> None:
                    mrs = entry.get("model_results", {})
                    if not mrs:
                        return
                    base_id = f"d{doc_idx:04d}-p{prob_idx:04d}-{kind[0]}{img_idx:04d}"
                    for m, res in mrs.items():
                        cdl = (res.get("cdl") or {}).get("description")
                        nl = (res.get("nl") or {}).get("natural_language")
                        if not cdl or not nl:
                            continue
                        page_img = _resolve_page_image(source_pdf, entry.get("page_number", 0), entry, image_base)
                        if not page_img:
                            if verbose:
                                print(f"[SKIP] {base_id} {m}: no saved page image")
                            continue
                        try:
                            crop = _render_and_crop(
                                source_pdf,
                                entry["page_number"],
                                entry["coordinates"],
                                crops_dir,
                                f"{base_id}_{m.replace('/', '_')}",
                                page_image_path=page_img,
                            )
                        except Exception as e:
                            print(f"[WARNING] Failed to crop {base_id} for judge: {e}")
                            continue
                        pairs.append({
                            "source_pdf": source_pdf, "doc_idx": doc_idx, "prob_idx": prob_idx,
                            "image_kind": kind, "img_idx": img_idx, "base_id": base_id, "model": m,
                            "crop_path": str(crop), "cerinta": cerinta, "barem_text": barem_text,
                            "cdl": cdl, "nl": nl,
                        })

                for i, e in enumerate(prob.get("imagini", [])):
                    _add("imagini", prob.get("imagini", []), i, e)
                if barem:
                    for i, e in enumerate(barem.get("imagini", [])):
                        _add("barem", barem.get("imagini", []), i, e)

        if not pairs:
            raise ValueError(
                "No (image, model) pairs with BOTH CDL and NL found. "
                "Run extract-descriptions first."
            )
        if verbose:
            print(f"[DEBUG] {len(pairs)} (image,model) pairs to judge with {model}")

        # Resume: a `.done` file records completed (base_id, model) judge calls.
        # An interrupted re-run picks up where it left off; the prior verdicts
        # already merged in the output JSON are reused as-is.
        out_json_path = Path(output_file)
        done_path = out_json_path.with_suffix(out_json_path.suffix + ".done")
        done: set[tuple[str, str]] = set()
        if out_json_path.exists():
            try:
                with open(out_json_path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                doc_index_of = {pdf: i for i, pdf in enumerate(ocr_data.keys())}
                for src_key, probs in prev.items():
                    if src_key not in doc_index_of:
                        continue
                    di = doc_index_of[src_key]
                    cur_probs = ocr_data[src_key]
                    for pi, p_pro in enumerate(probs):
                        p_cur = cur_probs[pi] if pi < len(cur_probs) else None
                        if not p_cur:
                            continue
                        def _scan(kind: str, c_pro: list, c_cur: list) -> None:
                            for ii, e_pro in enumerate(c_pro):
                                e_cur = c_cur[ii] if ii < len(c_cur) else None
                                if not e_cur:
                                    continue
                                mrs = e_pro.get("model_results", {})
                                if not mrs:
                                    continue
                                e_cur.setdefault("model_results", {}).update(mrs)
                                bid = f"d{di:04d}-p{pi:04d}-{kind[0]}{ii:04d}"
                                for m, res in mrs.items():
                                    if res.get("consistency"):
                                        done.add((bid, m))
                        _scan("imagini", p_pro.get("imagini", []), p_cur.get("imagini", []))
                        if p_pro.get("barem") and p_cur.get("barem"):
                            _scan("barem", p_pro["barem"].get("imagini", []), p_cur["barem"].get("imagini", []))
            except Exception as e:
                print(f"[WARNING] Could not read previous output ({e}); starting fresh")
        if done_path.exists():
            try:
                with open(done_path, "r", encoding="utf-8") as f:
                    done |= {tuple(x) for x in json.load(f)}
            except Exception:
                pass

        todo = [p for p in pairs if (p["base_id"], p["model"]) not in done]
        if verbose and done:
            print(f"[DEBUG] Resuming: {len(done)} judge task(s) done; {len(todo)} remaining")
        if not todo:
            print("[INFO] All judge tasks already complete — nothing to do.")
            _write_atomic(out_json_path, json.dumps(ocr_data, indent=2, ensure_ascii=False))
            return {"pairs": len(pairs), "model": model, "ok": 0, "fail": 0,
                    "total_input_tokens": 0, "total_output_tokens": 0, "resumed": len(done)}

        _write_lock = threading.Lock()

        def _flush() -> None:
            with _write_lock:
                try:
                    _write_atomic(out_json_path, json.dumps(ocr_data, indent=2, ensure_ascii=False))
                    _write_atomic(done_path, json.dumps(sorted([list(x) for x in done]), ensure_ascii=False))
                except Exception as e:
                    print(f"[WARNING] Failed to write incremental output: {e}")

        def _execute(p: dict):
            b64 = _image_to_base64(p["crop_path"])
            prompt = _build_prompt(tpl, p["cerinta"], p["barem_text"])
            prompt = prompt.replace("{{CDL_DESCRIPTION}}", p["cdl"])
            prompt = prompt.replace("{{NL_DESCRIPTION}}", p["nl"])
            messages = [
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}
            ]
            result, in_tok, out_tok = _call_model(
                self.client, model, messages, _consistency_response_format(),
            )
            return p, (result, in_tok, out_tok)

        ok = fail = total_in = total_out = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(_execute, p): p for p in todo}
            for future in tqdm(concurrent.futures.as_completed(futs), total=len(futs), desc="Judging"):
                p, (result, in_tok, out_tok) = future.result()
                total_in += in_tok
                total_out += out_tok

                probs = ocr_data[p["source_pdf"]]
                imgs = (probs[p["prob_idx"]].get("barem") or {}).get("imagini", []) if p["image_kind"] == "barem" else probs[p["prob_idx"]].get("imagini", [])
                img = imgs[p["img_idx"]]
                mrs = img.setdefault("model_results", {})
                m = mrs.setdefault(p["model"], {})
                if result:
                    ok += 1
                    m["consistency"] = {
                        "is_geometric": result.get("is_geometric", True),
                        "consistent": result.get("consistent", False),
                        "severity": result.get("severity", "major"),
                        "issues": result.get("issues", []),
                        "suggested_cdl": result.get("suggested_cdl"),
                    }
                else:
                    fail += 1
                    m["consistency"] = {"consistent": False, "severity": "major", "issues": ["API call failed"]}

                done.add((p["base_id"], p["model"]))
                _flush()

        _flush()
        if own_temp:
            try:
                import shutil
                shutil.rmtree(crops_dir, ignore_errors=True)
            except Exception:
                pass
        print(f"[INFO] {ok} ok / {fail} fail — {total_in:,} in + {total_out:,} out tokens")
        return {"pairs": len(pairs), "model": model, "ok": ok, "fail": fail,
                "total_input_tokens": total_in, "total_output_tokens": total_out, "resumed": len(done) - ok - fail if done else 0}

