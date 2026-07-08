import json
from pathlib import Path
from typing import Any, Optional

from PIL import Image as PILImage

from exam_processor.base_extraction import BaseExtraction
from exam_processor.utils.client import CompletionResult, TogetherClient
from exam_processor.utils.images import crop_image, render_pdf_page
from exam_processor.utils.models import (
    DEFAULT_CONSISTENCY_JUDGE_MODEL,
    DEFAULT_DESCRIPTION_MODELS,
)
from exam_processor.utils.prompt import Prompt
from exam_processor.utils.schemas import (
    CdlDescription,
    ConsistencyVerdict,
    NlDescription,
)


def _resolve_page_image(source_pdf: str, page_number: int, fig: dict, image_base: Path) -> Path | None:
    rel = fig.get("image_path")
    if not rel:
        return None
    p = Path(rel)
    candidate = p if p.is_absolute() else image_base / p
    return candidate if candidate.exists() else None


def _crop_figure_to_image(source_pdf: str, entry: dict, image_base: Path, dpi: int = 200) -> Optional[PILImage.Image]:
    page_idx = max(0, int(entry.get("page_number", 0) or 0) - 1)
    saved_page = _resolve_page_image(source_pdf, page_idx + 1, entry, image_base)
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


class DescriptionExtraction(BaseExtraction):
    def __init__(
        self,
        client: TogetherClient,
        prompt: Prompt | list[Prompt] | None = None,
        *,
        prompts_dir: str = "prompts",
        max_workers: int = 10,
    ):
        prompts = prompt or [Prompt("image_to_cdl", prompts_dir), Prompt("image_to_nl", prompts_dir)]
        super().__init__(client, prompts, max_workers=max_workers)
        self._cdl_prompt, self._nl_prompt = self.prompt
        self.models: list[str] = []
        self._ocr_data: dict[str, list] = {}
        self._out_json_path: Optional[Path] = None

    def _iter_work_items(self, ocr_data: dict[str, list], image_base: Path, verbose: bool) -> list[dict]:
        figures: list[dict] = []
        for doc_idx, source_pdf, prob_idx, cerinta, barem_text, kind, img_idx, entry in self._iter_figure_entries(ocr_data):
            base_id = f"d{doc_idx:04d}-p{prob_idx:04d}-{kind[0]}{img_idx:04d}"
            fig_img = _crop_figure_to_image(source_pdf, entry, image_base)
            if fig_img is None:
                if verbose:
                    print(f"[WARNING] Failed to render/crop {source_pdf} page {entry.get('page_number')}")
                continue
            figures.append({
                "source_pdf": source_pdf, "doc_idx": doc_idx, "prob_idx": prob_idx,
                "image_kind": kind, "img_idx": img_idx, "base_id": base_id,
                "img": fig_img, "cerinta": cerinta, "barem_text": barem_text,
            })
        return figures

    def _empty_items_error(self) -> str:
        return "No figure images found."

    def _plan_line(self, items: list) -> str:
        return f"[DEBUG] {len(items)} figures x {len(self.models)} models x 2 tasks = {len(items)*len(self.models)*2} requests"

    def _build_tasks(self, items: list, done: set) -> list:
        tasks: list[dict] = []
        for fig in items:
            prompt_cdl = self._cdl_prompt.render({"PROBLEM_TASK": fig["cerinta"], "CONTEXT": [fig["barem_text"], None]})
            prompt_nl = self._nl_prompt.render({"PROBLEM_TASK": fig["cerinta"], "CONTEXT": [fig["barem_text"], None]})
            for model in self.models:
                for task, prompt, schema in (("cdl", prompt_cdl, CdlDescription), ("nl", prompt_nl, NlDescription)):
                    if (fig["base_id"], model, task) not in done:
                        tasks.append({"fig": fig, "model": model, "task": task, "prompt": prompt, "img": fig["img"], "schema": schema})
        return tasks

    def _done_key(self, task: dict) -> tuple:
        return (task["fig"]["base_id"], task["model"], task["task"])

    def _execute(self, task: dict) -> CompletionResult:
        return self.client.complete(
            task["model"],
            query=[task["prompt"], task["img"]],
            response_schema=task["schema"],
        )

    def _merge(self, task: dict, result: CompletionResult) -> tuple[int, int]:
        fig = task["fig"]
        request_model = task["model"]
        task_kind = task["task"]
        primary = self.models[0]
        probs = self._ocr_data[fig["source_pdf"]]
        imgs = (probs[fig["prob_idx"]].get("barem") or {}).get("imagini", []) if fig["image_kind"] == "barem" else probs[fig["prob_idx"]].get("imagini", [])
        img = imgs[fig["img_idx"]]
        mrs = img.setdefault("model_results", {})
        mr = mrs.setdefault(request_model, {})

        if not result.ok or result.content is None:
            return 0, 1
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
        return 1, 0

    def _progress_desc(self) -> str:
        return "Extracting"

    def _summary_fields(self, items: list) -> dict[str, Any]:
        return {"figures": len(items), "models": self.models}

    def _rehydrate_from_prev(self, prev: dict, done: set) -> None:
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

    def _dump_state(self) -> Any:
        return self._ocr_data

    def _serialize_done_key(self, key: Any) -> Any:
        return list(key)

    def run(
        self,
        ocr_result_file: str,
        image_base_dir: str,
        output_file: str,
        models: Optional[list[str]] = None,
        verbose: bool = False,
        crop_work_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        self.models = models or DEFAULT_DESCRIPTION_MODELS
        with open(ocr_result_file, "r", encoding="utf-8") as f:
            self._ocr_data = json.load(f)
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        items = self._iter_work_items(self._ocr_data, Path(image_base_dir), verbose)
        resume_prev = None
        out_path = Path(output_file)
        if out_path.exists():
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    resume_prev = json.load(f)
            except Exception as e:
                print(f"[WARNING] Could not read previous output ({e}); starting fresh")
        return self._run_pipeline(items, output_file, resume_prev=resume_prev, verbose=verbose)


class ConsistencyAssessment(BaseExtraction):
    def __init__(
        self,
        client: TogetherClient,
        prompt: Prompt | list[Prompt] | None = None,
        *,
        prompts_dir: str = "prompts",
        max_workers: int = 10,
    ):
        super().__init__(client, prompt or Prompt("cdl_nl_consistency", prompts_dir), max_workers=max_workers)
        self._consistency_prompt = self.prompt if isinstance(self.prompt, Prompt) else self.prompt[0]
        self._model: str = DEFAULT_CONSISTENCY_JUDGE_MODEL
        self._ocr_data: dict[str, list] = {}

    def _iter_work_items(self, ocr_data: dict[str, list], image_base: Path, verbose: bool) -> list[dict]:
        pairs: list[dict] = []
        for doc_idx, source_pdf, prob_idx, cerinta, barem_text, kind, img_idx, entry in self._iter_figure_entries(ocr_data):
            mrs = entry.get("model_results", {})
            if not mrs:
                continue
            base_id = f"d{doc_idx:04d}-p{prob_idx:04d}-{kind[0]}{img_idx:04d}"
            for m, res in mrs.items():
                cdl = (res.get("cdl") or {}).get("description")
                nl = (res.get("nl") or {}).get("natural_language")
                if not cdl or not nl:
                    continue
                page_img_path = _resolve_page_image(source_pdf, entry.get("page_number", 0), entry, image_base)
                if not page_img_path:
                    if verbose:
                        print(f"[SKIP] {base_id} {m}: no saved page image")
                    continue
                fig_img = _crop_figure_to_image(source_pdf, entry, image_base)
                if fig_img is None:
                    if verbose:
                        print(f"[WARNING] Failed to crop {base_id} for judge")
                    continue
                pairs.append({
                    "source_pdf": source_pdf, "doc_idx": doc_idx, "prob_idx": prob_idx,
                    "image_kind": kind, "img_idx": img_idx, "base_id": base_id, "model": m,
                    "img": fig_img, "cerinta": cerinta, "barem_text": barem_text,
                    "cdl": cdl, "nl": nl,
                })
        return pairs

    def _empty_items_error(self) -> str:
        return "No (image, model) pairs with BOTH CDL and NL found. Run extract-descriptions first."

    def _plan_line(self, items: list) -> str:
        return f"[DEBUG] {len(items)} (image,model) pairs to judge with {self._model}"

    def _build_tasks(self, items: list, done: set) -> list:
        return [p for p in items if (p["base_id"], p["model"]) not in done]

    def _done_key(self, task: dict) -> tuple:
        return (task["base_id"], task["model"])

    def _execute(self, task: dict) -> CompletionResult:
        prompt = self._consistency_prompt.render({
            "PROBLEM_TASK": task["cerinta"],
            "CONTEXT": [task["barem_text"], None],
            "CDL_DESCRIPTION": task["cdl"],
            "NL_DESCRIPTION": task["nl"],
        })
        return self.client.complete(
            self._model,
            query=[prompt, task["img"]],
            response_schema=ConsistencyVerdict,
        )

    def _merge(self, task: dict, result: CompletionResult) -> tuple[int, int]:
        probs = self._ocr_data[task["source_pdf"]]
        imgs = (probs[task["prob_idx"]].get("barem") or {}).get("imagini", []) if task["image_kind"] == "barem" else probs[task["prob_idx"]].get("imagini", [])
        img = imgs[task["img_idx"]]
        mrs = img.setdefault("model_results", {})
        m = mrs.setdefault(task["model"], {})
        if result.ok and result.content is not None:
            content = result.content
            m["consistency"] = {
                "is_geometric": content.is_geometric,
                "consistent": content.consistent,
                "severity": content.severity,
                "issues": content.issues,
                "suggested_cdl": content.suggested_cdl,
            }
            return 1, 0
        m["consistency"] = {"consistent": False, "severity": "major", "issues": ["API call failed"]}
        return 0, 1

    def _progress_desc(self) -> str:
        return "Judging"

    def _summary_fields(self, items: list) -> dict[str, Any]:
        return {"pairs": len(items), "model": self._model}

    def _rehydrate_from_prev(self, prev: dict, done: set) -> None:
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
                    if res.get("consistency"):
                        done.add((bid, m))
        _scan("imagini", p_pro.get("imagini", []), p_cur.get("imagini", []))
        if p_pro.get("barem") and p_cur.get("barem"):
            _scan("barem", p_pro["barem"].get("imagini", []), p_cur["barem"].get("imagini", []))

    def _dump_state(self) -> Any:
        return self._ocr_data

    def _serialize_done_key(self, key: Any) -> Any:
        return list(key)

    def run(
        self,
        enriched_file: str,
        image_base_dir: str,
        output_file: str,
        model: Optional[str] = None,
        verbose: bool = False,
        crop_work_dir: Optional[str] = None,
    ) -> dict[str, Any]:
        self._model = model or DEFAULT_CONSISTENCY_JUDGE_MODEL
        with open(enriched_file, "r", encoding="utf-8") as f:
            self._ocr_data = json.load(f)
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

        items = self._iter_work_items(self._ocr_data, Path(image_base_dir), verbose)
        resume_prev = None
        out_path = Path(output_file)
        if out_path.exists():
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    resume_prev = json.load(f)
            except Exception as e:
                print(f"[WARNING] Could not read previous output ({e}); starting fresh")
        return self._run_pipeline(items, output_file, resume_prev=resume_prev, verbose=verbose)

