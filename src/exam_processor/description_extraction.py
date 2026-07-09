from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from PIL import Image as PILImage

from exam_processor.batch_emulator import BatchEmulator
from exam_processor.utils.client import CompletionResult, TogetherClient
from exam_processor.utils.images import crop_image, render_pdf_page
from exam_processor.utils.models import DEFAULT_DESCRIPTION_MODELS
from exam_processor.utils.prompt import Prompt
from exam_processor.utils.schemas import CdlDescription, NlDescription


@dataclass
class FigureEntry:
    doc_idx: int
    source_pdf: str
    prob_idx: int
    cerinta: str
    barem_text: Optional[str]
    image_kind: str
    img_idx: int
    entry: dict

    @property
    def base_id(self) -> str:
        return f"d{self.doc_idx:04d}-p{self.prob_idx:04d}-{self.image_kind[0]}{self.img_idx:04d}"


def walk_figure_entries(ocr_data: dict[str, list]) -> Iterator[FigureEntry]:
    for doc_idx, (source_pdf, problems) in enumerate(ocr_data.items()):
        for prob_idx, prob in enumerate(problems):
            cerinta = prob.get("cerinta", "")
            barem = prob.get("barem")
            barem_text = barem.get("explicatie", "") if barem else None
            for i, e in enumerate(prob.get("imagini", [])):
                yield FigureEntry(doc_idx, source_pdf, prob_idx, cerinta, barem_text, "imagini", i, e)
            if barem:
                for i, e in enumerate(barem.get("imagini", [])):
                    yield FigureEntry(doc_idx, source_pdf, prob_idx, cerinta, barem_text, "barem", i, e)


def resolve_page_image(source_pdf: str, page_number: int, fig: dict, image_base: Path) -> Path | None:
    rel = fig.get("image_path")
    if not rel:
        return None
    p = Path(rel)
    candidate = p if p.is_absolute() else image_base / p
    return candidate if candidate.exists() else None


def crop_figure_to_image(source_pdf: str, entry: dict, image_base: Path, dpi: int = 200) -> Optional[PILImage.Image]:
    page_idx = max(0, int(entry.get("page_number", 0) or 0) - 1)
    saved_page = resolve_page_image(source_pdf, page_idx + 1, entry, image_base)
    page_img: Optional[PILImage.Image] = None
    opened_doc = None
    try:
        if saved_page is not None:
            page_img = PILImage.open(saved_page)
        else:
            import fitz
            opened_doc = fitz.open(source_pdf)
            page_img = render_pdf_page(opened_doc, page_idx, dpi=dpi)
        if page_img is None:
            return None
        page_img.load()
        crop = crop_image(page_img, entry.get("coordinates", []))
        if crop is None:
            return None
        return crop
    finally:
        if page_img is not None:
            try:
                page_img.close()
            except Exception:
                pass
        if opened_doc is not None:
            opened_doc.close()


@dataclass
class FigureTask:
    fig: dict
    model: str
    task_kind: str
    prompt: str
    img: PILImage.Image
    schema: type


class DescriptionExtraction(BatchEmulator):
    EMPTY_ITEMS_LABEL = "No figure images found."
    PROGRESS_LABEL = "Extracting"

    def __init__(
        self,
        client: TogetherClient,
        *,
        prompts_dir: str = "prompts",
        max_workers: int = 10,
    ):
        super().__init__(client, max_workers=max_workers)
        self._cdl_prompt = Prompt("image_to_cdl", prompts_dir)
        self._nl_prompt = Prompt("image_to_nl", prompts_dir)
        self.models: list[str] = []
        self._ocr_data: dict[str, list] = {}

    def _iter_work_items(self, ocr_data: dict[str, list], image_base: Path, verbose: bool) -> list[dict]:
        figures: list[dict] = []
        for fe in walk_figure_entries(ocr_data):
            fig_img = crop_figure_to_image(fe.source_pdf, fe.entry, image_base)
            if fig_img is None:
                if verbose:
                    print(f"[WARNING] Failed to render/crop {fe.source_pdf} page {fe.entry.get('page_number')}")
                continue
            figures.append({
                "source_pdf": fe.source_pdf, "doc_idx": fe.doc_idx, "prob_idx": fe.prob_idx,
                "image_kind": fe.image_kind, "img_idx": fe.img_idx, "base_id": fe.base_id,
                "img": fig_img, "cerinta": fe.cerinta, "barem_text": fe.barem_text,
            })
        return figures

    def plan_line(self, items: list) -> str:
        return f"[DEBUG] {len(items)} figures x {len(self.models)} models x 2 tasks = {len(items)*len(self.models)*2} requests"

    def build_tasks(self, items: list, done: set) -> list[FigureTask]:
        tasks: list[FigureTask] = []
        for fig in items:
            prompt_cdl = self._cdl_prompt.render({"PROBLEM_TASK": fig["cerinta"], "CONTEXT": [fig["barem_text"], None]})
            prompt_nl = self._nl_prompt.render({"PROBLEM_TASK": fig["cerinta"], "CONTEXT": [fig["barem_text"], None]})
            for model in self.models:
                for task_kind, prompt, schema in (("cdl", prompt_cdl, CdlDescription), ("nl", prompt_nl, NlDescription)):
                    if (fig["base_id"], model, task_kind) not in done:
                        tasks.append(FigureTask(fig, model, task_kind, prompt, fig["img"], schema))
        return tasks

    def task_id(self, task: FigureTask) -> tuple:
        return (task.fig["base_id"], task.model, task.task_kind)

    def execute(self, task: FigureTask) -> CompletionResult:
        return self.client.complete(
            task.model,
            query=[task.prompt, task.img],
            response_schema=task.schema,
        )

    def merge_result(self, task: FigureTask, result: CompletionResult) -> None:
        fig = task.fig
        request_model = task.model
        task_kind = task.task_kind
        primary = self.models[0]
        probs = self._ocr_data[fig["source_pdf"]]
        imgs = (probs[fig["prob_idx"]].get("barem") or {}).get("imagini", []) if fig["image_kind"] == "barem" else probs[fig["prob_idx"]].get("imagini", [])
        img = imgs[fig["img_idx"]]
        mrs = img.setdefault("model_results", {})
        mr = mrs.setdefault(request_model, {})
        if not result.ok or result.content is None:
            return
        content = result.content
        if task_kind == "cdl":
            mr["cdl"] = {
                "is_geometric": content.is_geometric,
                "description": content.description,
                "is_complete": content.is_complete,
            }
        else:
            mr["nl"] = {
                "is_geometric": content.is_geometric,
                "natural_language": content.natural_language,
            }
        if request_model == primary:
            if task_kind == "cdl":
                img["cdl_is_geometric"] = content.is_geometric
                img["cdl_description"] = content.description
                img["cdl_is_complete"] = content.is_complete
            else:
                img["nl_is_geometric"] = content.is_geometric
                img["natural_language"] = content.natural_language

    def summary_extras(self, items: list) -> dict[str, Any]:
        return {"figures": len(items), "models": self.models}

    def rehydrate_from_prev(self, prev: dict, done: set) -> None:
        ocr_data = self._ocr_data
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
                self._scan_problem(p_pro, p_cur, di, pi, done)

    def _scan_problem(self, p_pro: dict, p_cur: dict, doc_idx: int, prob_idx: int, done: set) -> None:
        def _scan(kind: str, c_pro: list, c_cur: list) -> None:
            for ii, e_pro in enumerate(c_pro):
                e_cur = c_cur[ii] if ii < len(c_cur) else None
                if not e_cur:
                    continue
                mrs = e_pro.get("model_results", {})
                if not mrs:
                    continue
                e_cur.setdefault("model_results", {}).update(mrs)
                bid = f"d{doc_idx:04d}-p{prob_idx:04d}-{kind[0]}{ii:04d}"
                for m, res in mrs.items():
                    if "cdl" in res:
                        done.add((bid, m, "cdl"))
                    if "nl" in res:
                        done.add((bid, m, "nl"))
        _scan("imagini", p_pro.get("imagini", []), p_cur.get("imagini", []))
        if p_pro.get("barem") and p_cur.get("barem"):
            _scan("barem", p_pro["barem"].get("imagini", []), p_cur["barem"].get("imagini", []))

    def dump_state(self) -> Any:
        return self._ocr_data

    def run(
        self,
        ocr_result_file: str,
        image_base_dir: str,
        output_file: str,
        models: Optional[list[str]] = None,
        verbose: bool = False,
    ) -> dict[str, Any]:
        self.models = models or DEFAULT_DESCRIPTION_MODELS
        with open(ocr_result_file, "r", encoding="utf-8") as f:
            self._ocr_data = json.load(f)
        items = self._iter_work_items(self._ocr_data, Path(image_base_dir), verbose)
        return self.run_pipeline(items, output_file, verbose=verbose)