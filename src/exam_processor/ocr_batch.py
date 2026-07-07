"""OCR extraction via real-time chat completions."""

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import concurrent.futures
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from exam_processor.client import TogetherClient
from exam_processor.models import (
    DEFAULT_OCR_MODEL,

    get_model,
    format_usage_info,
)
from exam_processor.schemas import OcrResult

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None

try:
    from PIL import Image
except ImportError:
    Image = None


def format_ocr_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "OcrResult",
            "schema": OcrResult.model_json_schema(),
        },
    }


class DocumentTuple:
    """A document tuple: (exam_subject_pdf_path, optional_barem_pdf_path)."""

    def __init__(self, subject_pdf: str, barem_pdf: Optional[str] = None):
        self.subject_pdf = subject_pdf
        self.barem_pdf = barem_pdf

    @property
    def stem(self) -> str:
        return Path(self.subject_pdf).stem


def _image_to_base64(image_path: str | Path, fmt: str = "JPEG") -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _ensure_pdf(path: str, temp_base: Path) -> str:
    """If path is a DOC/DOCX, convert to PDF via LibreOffice and return the PDF path."""
    p = Path(path)
    if p.suffix.lower() in (".doc", ".docx"):
        out_dir = temp_base / "conversions"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = os.system(
            f'libreoffice --headless --convert-to pdf "{p.resolve()}" --outdir "{out_dir}"'
        )
        if result != 0:
            raise RuntimeError(f"LibreOffice failed to convert {path}")
        candidates = list(out_dir.glob(f"{p.stem}*.pdf"))
        if not candidates:
            raise FileNotFoundError(f"LibreOffice conversion did not produce PDF for {path}")
        return str(candidates[0])
    return path


def _pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 200) -> list[str]:
    if convert_from_path is None:
        raise ImportError("pdf2image is required. Install with: pip install pdf2image")
    os.makedirs(output_dir, exist_ok=True)
    images = convert_from_path(pdf_path, dpi=dpi)
    paths: list[str] = []
    for i, img in enumerate(images):
        p = os.path.join(output_dir, f"page_{i + 1:04d}.jpg")
        img.save(p, "JPEG", quality=90)
        paths.append(p)
    return paths


def _make_safe_name(name: str, max_len: int = 40) -> str:
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe or "doc"


def _call_model(
    client: TogetherClient,
    model: str,
    messages: list[dict],
    response_format: dict,
    max_tokens: int = 32768,
    temperature: float = 0.2,
    label: str = "",
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
        response = client.chat_completion(**kwargs)
        content = response.choices[0].message.content
        obj = json.loads(content)
        usage = response.usage
        return obj, usage.prompt_tokens, usage.completion_tokens
    except Exception as e:
        doc_info = f" ({label})" if label else ""
        print(f"[ERROR] Model {model} failed{doc_info}: {e}")
        return None, 0, 0


class OcrExtractor:
    """Real-time OCR + problem extraction from PDFs via multimodal LLM."""

    def __init__(self, client: TogetherClient, prompts_dir: str = "prompts"):
        self.client = client
        self.prompts_dir = prompts_dir

    def _build_messages(
        self,
        doc: DocumentTuple,
        prompt_template: str,
        dpi: int,
        temp_base: Path,
    ) -> tuple[list[dict], dict[str, list[str]], Path]:
        """Convert a document to a multimodal message list and temp dirs.

        Returns:
            (messages, temp_paths_dict, doc_temp_dir)
        """
        stem = doc.stem
        doc_dir = temp_base / f"{stem}_{hashlib.sha256(doc.subject_pdf.encode()).hexdigest()[:8]}"
        doc_dir.mkdir(parents=True, exist_ok=True)

        # Convert DOC/DOCX to PDF on-the-fly if needed
        subject_pdf_path = _ensure_pdf(doc.subject_pdf, temp_base)
        barem_pdf_path = _ensure_pdf(doc.barem_pdf, temp_base) if doc.barem_pdf else None

        subject_temp = doc_dir / "subject"
        subject_temp.mkdir(exist_ok=True)
        subject_page_images = _pdf_to_images(subject_pdf_path, str(subject_temp), dpi)

        barem_page_images: list[str] = []
        if barem_pdf_path:
            barem_temp = doc_dir / "barem"
            barem_temp.mkdir(exist_ok=True)
            barem_page_images = _pdf_to_images(barem_pdf_path, str(barem_temp), dpi)

        prompt = prompt_template + "\n"
        prompt += f"--- BEGIN DOCUMENT: {Path(doc.subject_pdf).name} ---\n"
        content: list[dict] = [{"type": "text", "text": prompt}]

        for pg_idx, img_path in enumerate(subject_page_images):
            content.append({"type": "text", "text": f"[PAGE {pg_idx + 1} - SUBJECT]"})
            b64 = _image_to_base64(img_path, fmt="JPEG")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

        if barem_page_images:
            content.append({"type": "text", "text": "\n--- BEGIN BAREM DOCUMENT ---\n"})
            for pg_idx, img_path in enumerate(barem_page_images):
                content.append({"type": "text", "text": f"[BAREM PAGE {pg_idx + 1}]"})
                b64 = _image_to_base64(img_path, fmt="JPEG")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })

        temp_paths = {"subject": subject_page_images, "barem": barem_page_images}
        return content, temp_paths, doc_dir

    def _process_doc(
        self,
        doc: DocumentTuple,
        model: str,
        dpi: int,
        prompt_template: str,
        image_output_dir: Optional[Path],
        base_url: Optional[str],
        image_format: str,
        image_quality: int,
        verbose: bool,
        flat_r2_keys: bool,
    ) -> tuple[str, list[dict], int, int]:
        """Process a single document and return (source_pdf, problems, in_tok, out_tok)."""
        if verbose:
            print(f"[DEBUG] Processing {doc.subject_pdf}")

        with tempfile.TemporaryDirectory(prefix="ocr_") as tdir:
            temp_base = Path(tdir)
            messages, temp_paths, _ = self._build_messages(
                doc, prompt_template, dpi, temp_base
            )

            result, in_tok, out_tok = _call_model(
                self.client,
                model,
                [{"role": "user", "content": messages}],
                format_ocr_response_format(),
                label=doc.stem,
            )

            if result is None:
                print(f"[WARNING] No result for {doc.subject_pdf}")
                return doc.subject_pdf, [], in_tok, out_tok

            # Normalize format
            if isinstance(result, dict) and "problems" in result:
                problems = result["problems"]
            elif isinstance(result, list):
                problems = result
            else:
                print(f"[WARNING] Unexpected format for {doc.subject_pdf}")
                problems = []

            img_dir = image_output_dir
            img_fmt = image_format.lower()
            ext_map = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}
            img_ext = ext_map.get(img_fmt, img_fmt)
            saved_images: dict[str, str] = {}

            def _save_page_image(pdf_path: str, page_number: int) -> dict:
                pdf_file = Path(pdf_path)
                key = f"{str(pdf_file)}:{page_number}"
                if key not in saved_images:
                    if not img_dir:
                        return {}
                    doc_hash = hashlib.sha256(str(pdf_file).encode()).hexdigest()[:8]
                    if flat_r2_keys:
                        out_name = f"{doc_hash}_p{page_number:04d}.{img_ext}"
                        out_path = img_dir / out_name
                    else:
                        safe_name = _make_safe_name(pdf_file.stem)
                        sub = img_dir / f"{safe_name}_{doc_hash}"
                        sub.mkdir(parents=True, exist_ok=True)
                        out_name = f"{safe_name}_p{page_number:04d}.{img_ext}"
                        out_path = sub / out_name

                    # Find the temp page image from the lists we built earlier
                    temp_img = None
                    if str(pdf_file) == doc.subject_pdf:
                        idx = page_number - 1
                        if 0 <= idx < len(temp_paths["subject"]):
                            temp_img = temp_paths["subject"][idx]
                    elif doc.barem_pdf and str(pdf_file) == doc.barem_pdf:
                        idx = page_number - 1
                        if 0 <= idx < len(temp_paths.get("barem", [])):
                            temp_img = temp_paths["barem"][idx]

                    if not temp_img:
                        # Fallback: render directly
                        temp_img = _render_pdf_page(pdf_path, page_number, out_path.parent, dpi=200)

                    if temp_img and Path(temp_img).exists():
                        if str(temp_img).endswith(".png") and img_fmt in ("jpeg", "jpg"):
                            if Image is None:
                                shutil.copy2(temp_img, out_path)
                            else:
                                Image.open(temp_img).convert("RGB").save(
                                    out_path, format="JPEG", quality=image_quality
                                )
                        else:
                            shutil.copy2(temp_img, out_path)
                        saved_images[key] = str(out_path)
                    else:
                        return {}

                full_path = saved_images[key]
                if flat_r2_keys and img_dir:
                    rel = Path(full_path).name
                elif img_dir:
                    rel = str(Path(full_path).relative_to(img_dir)).replace("\\", "/")
                else:
                    rel = full_path
                ret: dict = {"image_path": rel}
                if base_url:
                    ret["image_url"] = f"{base_url.rstrip('/')}/{rel.lstrip('/')}"
                return ret

            def _process_imagini(imagini: list[dict]) -> list[dict]:
                processed: list[dict] = []
                for img in imagini:
                    entry: dict = {
                        "page_number": img.get("page_number", 0),
                        "coordinates": img.get("coordinates", [0, 0, 0, 0]),
                        "description": img.get("description", ""),
                    }
                    if img_dir:
                        entry.update(_save_page_image(doc.subject_pdf, entry["page_number"]))
                    processed.append(entry)
                return processed

            validated_problems: list[dict] = []
            for prob in problems:
                imagini = prob.get("imagini", [])
                if not imagini:
                    continue  # only keep problems with images (same as old behaviour)
                barem_raw = prob.get("barem")
                validated_barem = None
                if barem_raw:
                    barem_imgs = barem_raw.get("imagini", [])
                    validated_barem = {
                        "explicatie": barem_raw.get("explicatie", ""),
                        "imagini": _process_imagini(barem_imgs) if img_dir else [
                            {
                                "page_number": img.get("page_number", 0),
                                "coordinates": img.get("coordinates", [0, 0, 0, 0]),
                                "description": img.get("description", ""),
                            }
                            for img in barem_imgs
                        ],
                    }
                validated_problems.append({
                    "cerinta": prob.get("cerinta", ""),
                    "imagini": _process_imagini(imagini) if img_dir else [
                        {
                            "page_number": img.get("page_number", 0),
                            "coordinates": img.get("coordinates", [0, 0, 0, 0]),
                            "description": img.get("description", ""),
                        }
                        for img in imagini
                    ],
                    "barem": validated_barem,
                })

            return doc.subject_pdf, validated_problems, in_tok, out_tok

    def run(
        self,
        documents: list[DocumentTuple],
        output_file: str,
        model: str = DEFAULT_OCR_MODEL,
        dpi: int = 200,
        image_output_dir: Optional[str] = None,
        base_url: Optional[str] = None,
        image_format: str = "jpeg",
        image_quality: int = 90,
        max_workers: int = 1,
        verbose: bool = False,
        flat_r2_keys: bool = True,
    ) -> tuple[dict[str, list[dict]], tuple[int, int], str]:
        """Run OCR extraction over all documents via the real-time API.

        Supports incremental save + resume via a `.done` set JSON.
        Bad PDFs / conversion failures are caught per-doc so one bad apple
        never kills the whole batch.

        Returns:
            (results_by_source, (in_tokens, out_tokens), model_name)
        """
        if convert_from_path is None:
            raise ImportError("pdf2image is required. Install with: pip install pdf2image")

        prompt_template = self.client.load_prompt(
            os.path.join(self.prompts_dir, "gemma4_ocr_exam.txt")
        )

        img_dir = Path(image_output_dir) if image_output_dir else None
        if img_dir:
            img_dir.mkdir(parents=True, exist_ok=True)

        out_path = Path(output_file)
        done_path = out_path.with_suffix(out_path.suffix + ".done")

        # Load existing results (incremental resume)
        results_by_source: dict[str, list[dict]] = {}
        total_input = 0
        total_output = 0
        if out_path.exists():
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    results_by_source = json.load(f)
                print(f"[INFO] Resumed from existing output: {len(results_by_source)} files already done")
            except Exception as e:
                print(f"[WARNING] Could not read existing output ({e}), starting fresh")
                results_by_source = {}

        done_set: set[str] = set(results_by_source.keys())
        if done_path.exists():
            try:
                with open(done_path, "r", encoding="utf-8") as f:
                    done_set |= set(json.load(f))
            except Exception:
                pass

        # Skip already-completed entries
        todo = [doc for doc in documents if doc.subject_pdf not in done_set]
        if len(todo) != len(documents):
            print(f"[INFO] Skipping {len(documents) - len(todo)} already-processed documents")

        if not todo:
            print("[INFO] All documents already processed – nothing to do.")
            return results_by_source, (total_input, total_output), model

        # Lock for writing incremental output
        _write_lock = threading.Lock()

        def _task(doc):
            """Process one document; return (source_pdf, problems, in_tok, out_tok)."""
            try:
                return self._process_doc(
                    doc,
                    model=model,
                    dpi=dpi,
                    prompt_template=prompt_template,
                    image_output_dir=img_dir,
                    base_url=base_url,
                    image_format=image_format,
                    image_quality=image_quality,
                    verbose=verbose,
                    flat_r2_keys=flat_r2_keys,
                )
            except Exception as e:
                print(f"[ERROR] Skipping {doc.subject_pdf}: {e}")
                return doc.subject_pdf, [], 0, 0

        def _accept(source_pdf, problems, in_tok, out_tok):
            """Store result and flush to disk."""
            nonlocal total_input, total_output, results_by_source, done_set
            results_by_source[source_pdf] = problems
            total_input += in_tok
            total_output += out_tok
            done_set.add(source_pdf)
            with _write_lock:
                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(results_by_source, f, indent=2, ensure_ascii=False)
                    with open(done_path, "w", encoding="utf-8") as f:
                        json.dump(sorted(done_set), f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print(f"[WARNING] Failed to write incremental output: {e}")

        if max_workers > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(_task, doc): doc for doc in todo}
                for future in tqdm(
                    concurrent.futures.as_completed(futures),
                    total=len(todo),
                    desc="OCR extracting",
                ):
                    source_pdf, problems, in_tok, out_tok = future.result()
                    _accept(source_pdf, problems, in_tok, out_tok)
        else:
            for doc in tqdm(todo, desc="OCR extracting"):
                source_pdf, problems, in_tok, out_tok = _task(doc)
                _accept(source_pdf, problems, in_tok, out_tok)

        return results_by_source, (total_input, total_output), model


def _render_pdf_page(
    pdf_path: str, page_number: int, output_dir: Path, dpi: int = 200
) -> str | None:
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(pdf_path)
        if page_number < 1 or page_number > doc.page_count:
            doc.close()
            return None
        page = doc[page_number - 1]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        out_path = output_dir / f"rendered_p{page_number:04d}.jpg"
        pix.save(str(out_path))
        doc.close()
        return str(out_path)
    except Exception:
        return None


def parse_ocr_results(result: dict) -> list[OcrResult]:
    """Parse and validate OCR extraction results."""
    if isinstance(result, dict) and "problems" in result:
        problems_data = result["problems"]
    elif isinstance(result, list):
        problems_data = result
    else:
        raise ValueError(f"Unexpected result format: got {type(result)}")
    return [OcrResult(**prob) for prob in problems_data]

