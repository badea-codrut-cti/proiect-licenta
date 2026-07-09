import hashlib
import json
import os
import re
import shutil
import tempfile
from typing import Any, Hashable, Optional

from pathlib import Path

from PIL import Image as PILImage

from exam_processor.batch_emulator import BatchEmulator
from exam_processor.utils.client import CompletionResult, TogetherClient
from exam_processor.utils.images import render_pdf_page
from exam_processor.utils.models import DEFAULT_IMAGE_QUALITY, DEFAULT_OCR_MODEL, get_model
from exam_processor.utils.prompt import Prompt
from exam_processor.utils.schemas import OcrResult

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None


class DocumentTuple:
    def __init__(self, subject_pdf: str, barem_pdf: Optional[str] = None):
        self.subject_pdf = subject_pdf
        self.barem_pdf = barem_pdf

    @property
    def stem(self) -> str:
        return Path(self.subject_pdf).stem


def _ensure_pdf(path: str, temp_base: Path) -> str:
    p = Path(path)
    if p.suffix.lower() not in (".doc", ".docx"):
        return path
    out_dir = temp_base / "conversions"
    out_dir.mkdir(parents=True, exist_ok=True)
    if os.system(f'libreoffice --headless --convert-to pdf "{p.resolve()}" --outdir "{out_dir}"') != 0:
        raise RuntimeError(f"LibreOffice failed to convert {path}")
    candidates = list(out_dir.glob(f"{p.stem}*.pdf"))
    if not candidates:
        raise FileNotFoundError(f"LibreOffice conversion did not produce PDF for {path}")
    return str(candidates[0])


def _pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 200) -> list[PILImage.Image]:
    if convert_from_path is None:
        raise ImportError("pdf2image is required. Install with: pip install pdf2image")
    os.makedirs(output_dir, exist_ok=True)
    imgs: list[PILImage.Image] = []
    for i, img in enumerate(convert_from_path(pdf_path, dpi=dpi)):
        rgb = img.convert("RGB")
        p = os.path.join(output_dir, f"page_{i + 1:04d}.jpg")
        rgb.save(p, "JPEG", quality=DEFAULT_IMAGE_QUALITY)
        imgs.append(rgb)
    return imgs


def _make_safe_name(name: str, max_len: int = 40) -> str:
    safe = re.sub(r"[^a-zA-Z0-9\-_]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe or "doc"


def _build_query(
    doc: DocumentTuple,
    prompt_template: str,
    dpi: int,
    temp_base: Path,
) -> tuple[list[Any], dict[str, list[PILImage.Image]], dict[str, list[str]], Path]:
    stem = doc.stem
    doc_dir = temp_base / f"{stem}_{hashlib.sha256(doc.subject_pdf.encode()).hexdigest()[:8]}"
    doc_dir.mkdir(parents=True, exist_ok=True)

    subject_pdf_path = _ensure_pdf(doc.subject_pdf, temp_base)
    barem_pdf_path = _ensure_pdf(doc.barem_pdf, temp_base) if doc.barem_pdf else None

    subject_temp = doc_dir / "subject"
    subject_temp.mkdir(exist_ok=True)
    subject_page_imgs = _pdf_to_images(subject_pdf_path, str(subject_temp), dpi)
    subject_page_paths = [os.path.join(str(subject_temp), f"page_{i + 1:04d}.jpg") for i in range(len(subject_page_imgs))]

    barem_page_imgs: list[PILImage.Image] = []
    barem_page_paths: list[str] = []
    if barem_pdf_path:
        barem_temp = doc_dir / "barem"
        barem_temp.mkdir(exist_ok=True)
        barem_page_imgs = _pdf_to_images(barem_pdf_path, str(barem_temp), dpi)
        barem_page_paths = [os.path.join(str(barem_temp), f"page_{i + 1:04d}.jpg") for i in range(len(barem_page_imgs))]

    prompt = prompt_template + f"\n--- BEGIN DOCUMENT: {Path(doc.subject_pdf).name} ---\n"
    query: list[Any] = [prompt]

    for pg_idx, img in enumerate(subject_page_imgs):
        query.append(f"[PAGE {pg_idx + 1} - SUBJECT]")
        query.append(img)

    if barem_page_imgs:
        query.append("\n--- BEGIN BAREM DOCUMENT ---\n")
        for pg_idx, img in enumerate(barem_page_imgs):
            query.append(f"[BAREM PAGE {pg_idx + 1}]")
            query.append(img)

    page_imgs = {"subject": subject_page_imgs, "barem": barem_page_imgs}
    page_paths = {"subject": subject_page_paths, "barem": barem_page_paths}
    return query, page_imgs, page_paths, doc_dir


class OcrExtractor(BatchEmulator):
    EMPTY_ITEMS_LABEL = "No documents to process."
    PROGRESS_LABEL = "OCR extracting"

    def __init__(
        self,
        client: TogetherClient,
        *,
        prompts_dir: str = "prompts",
        max_workers: int = 1,
    ):
        super().__init__(client, max_workers=max_workers)
        self._ocr_prompt = Prompt("gemma4_ocr_exam", prompts_dir)
        self._model: str = DEFAULT_OCR_MODEL
        self._verbose: bool = False
        self._dpi: int = 200
        self._image_output_dir: Optional[Path] = None
        self._base_url: Optional[str] = None
        self._image_format: str = "jpeg"
        self._image_quality: int = DEFAULT_IMAGE_QUALITY
        self._flat_r2_keys: bool = True
        self._results: dict[str, list[dict]] = {}

    def build_tasks(self, items: list[DocumentTuple], done: set) -> list[DocumentTuple]:
        return [d for d in items if d.subject_pdf not in done]

    def task_id(self, task: DocumentTuple) -> str:
        return task.subject_pdf

    def plan_line(self, items: list) -> str:
        return f"[DEBUG] {len(items)} document(s) queued"

    def summary_extras(self, items: list) -> dict[str, Any]:
        return {"documents": len(items), "model": self._model}

    def rehydrate_from_prev(self, prev: dict, done: set) -> None:
        for source_pdf in prev.keys():
            self._results[source_pdf] = prev[source_pdf]
            done.add(source_pdf)

    def dump_state(self) -> Any:
        return self._results

    def execute(self, task: DocumentTuple) -> CompletionResult:
        if self._verbose:
            print(f"[DEBUG] Processing {task.subject_pdf}")
        with tempfile.TemporaryDirectory(prefix="ocr_") as tdir:
            temp_base = Path(tdir)
            query, page_imgs, page_paths, _ = _build_query(task, self._ocr_prompt.template, self._dpi, temp_base)
            result = self.client.complete(self._model, query, response_schema=OcrResult)
            if not result.ok:
                print(f"[WARNING] No result for {task.subject_pdf}")
                self._results[task.subject_pdf] = []
                return result
            problems = self._extract_problems(result.content)
            validated = self._validate_problems(task, problems, page_imgs, page_paths)
            self._results[task.subject_pdf] = validated
            return result

    def merge_result(self, task: DocumentTuple, result: CompletionResult) -> None:
        return

    @staticmethod
    def _extract_problems(content: OcrResult | dict | None) -> list[dict]:
        if content is None:
            return []
        if isinstance(content, OcrResult):
            return content.model_dump()["problems"]
        if isinstance(content, dict) and "problems" in content:
            return content["problems"]
        if isinstance(content, list):
            return content
        return []

    def _validate_problems(
        self,
        doc: DocumentTuple,
        problems: list[dict],
        page_imgs: dict[str, list[PILImage.Image]],
        page_paths: dict[str, list[str]],
    ) -> list[dict]:
        img_dir = self._image_output_dir
        img_fmt = self._image_format.lower()
        ext_map = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}
        img_ext = ext_map.get(img_fmt, img_fmt)
        saved_images: dict[str, str] = {}

        def _save_page_image(pdf_path: str, page_idx: int) -> dict:
            pdf_file = Path(pdf_path)
            key = f"{str(pdf_file)}:{page_idx}"
            if key not in saved_images:
                if not img_dir or page_idx < 0:
                    return {}
                doc_hash = hashlib.sha256(str(pdf_file).encode()).hexdigest()[:8]
                if self._flat_r2_keys:
                    out_name = f"{doc_hash}_p{page_idx + 1:04d}.{img_ext}"
                    out_path = img_dir / out_name
                else:
                    safe_name = _make_safe_name(pdf_file.stem)
                    sub = img_dir / f"{safe_name}_{doc_hash}"
                    sub.mkdir(parents=True, exist_ok=True)
                    out_name = f"{safe_name}_p{page_idx + 1:04d}.{img_ext}"
                    out_path = sub / out_name

                out_path.parent.mkdir(parents=True, exist_ok=True)
                src_img: Optional[PILImage.Image] = None
                which = None
                if str(pdf_file) == doc.subject_pdf and 0 <= page_idx < len(page_imgs["subject"]):
                    which = "subject"
                elif doc.barem_pdf and str(pdf_file) == doc.barem_pdf and 0 <= page_idx < len(page_imgs.get("barem", [])):
                    which = "barem"
                if which is None:
                    try:
                        import fitz
                        with fitz.open(pdf_path) as d:
                            src_img = render_pdf_page(d, page_idx, dpi=self._dpi)
                    except Exception:
                        return {}
                else:
                    src_img = page_imgs[which][page_idx].copy()

                save_fmt = "PNG" if img_fmt == "png" else "JPEG"
                src_img.convert("RGB").save(out_path, format=save_fmt, quality=self._image_quality if save_fmt == "JPEG" else None)
                src_img.close()
                saved_images[key] = str(out_path)

            full_path = saved_images[key]
            if self._flat_r2_keys and img_dir:
                rel = Path(full_path).name
            elif img_dir:
                rel = str(Path(full_path).relative_to(img_dir)).replace("\\", "/")
            else:
                rel = full_path
            ret: dict = {"image_path": rel}
            if self._base_url:
                ret["image_url"] = f"{self._base_url.rstrip('/')}/{rel.lstrip('/')}"
            return ret

        def _page_idx(page_number: Any) -> int:
            try:
                return int(page_number) - 1
            except (TypeError, ValueError):
                return -1

        def _process_imagini(imagini: list[dict], pdf_path: str) -> list[dict]:
            out: list[dict] = []
            for img in imagini:
                entry = {
                    "page_number": img.get("page_number", 0),
                    "coordinates": img.get("coordinates", [0, 0, 0, 0]),
                    "description": img.get("description", ""),
                }
                if img_dir:
                    entry.update(_save_page_image(pdf_path, _page_idx(entry["page_number"])))
                out.append(entry)
            return out

        def _inline_imagini(imagini: list[dict]) -> list[dict]:
            return [
                {
                    "page_number": img.get("page_number", 0),
                    "coordinates": img.get("coordinates", [0, 0, 0, 0]),
                    "description": img.get("description", ""),
                }
                for img in imagini
            ]

        validated: list[dict] = []
        for prob in problems:
            imagini = prob.get("imagini", [])
            if not imagini:
                continue
            barem_raw = prob.get("barem")
            validated_barem = None
            if barem_raw:
                barem_imgs = barem_raw.get("imagini", [])
                validated_barem = {
                    "explicatie": barem_raw.get("explicatie", ""),
                    "imagini": _process_imagini(barem_imgs, doc.barem_pdf) if img_dir else _inline_imagini(barem_imgs),
                }
            validated.append({
                "cerinta": prob.get("cerinta", ""),
                "imagini": _process_imagini(imagini, doc.subject_pdf) if img_dir else _inline_imagini(imagini),
                "barem": validated_barem,
            })
        return validated

    def run(
        self,
        documents: list[DocumentTuple],
        output_file: str,
        model: str = DEFAULT_OCR_MODEL,
        dpi: int = 200,
        image_output_dir: Optional[str] = None,
        base_url: Optional[str] = None,
        image_format: str = "jpeg",
        image_quality: int = DEFAULT_IMAGE_QUALITY,
        max_workers: int = 1,
        verbose: bool = False,
        flat_r2_keys: bool = True,
    ) -> dict[str, Any]:
        if convert_from_path is None:
            raise ImportError("pdf2image is required. Install with: pip install pdf2image")

        self._model = model
        self._verbose = verbose
        self._dpi = dpi
        self._image_output_dir = Path(image_output_dir) if image_output_dir else None
        if self._image_output_dir:
            self._image_output_dir.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url
        self._image_format = image_format
        self._image_quality = image_quality
        self._flat_r2_keys = flat_r2_keys
        self.max_workers = max_workers

        summary = self.run_pipeline(list(documents), output_file, verbose=verbose)
        summary["results"] = self._results
        summary["total_tokens"] = (summary["total_input_tokens"], summary["total_output_tokens"])
        return summary
