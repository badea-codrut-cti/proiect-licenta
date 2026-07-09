from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from PIL import Image as PILImage

from exam_processor.batch_emulator import BatchEmulator
from exam_processor.utils.client import CompletionResult, TogetherClient
from exam_processor.utils.models import DEFAULT_CONSISTENCY_JUDGE_MODEL
from exam_processor.utils.prompt import Prompt
from exam_processor.utils.schemas import ConsistencyVerdict
from exam_processor.description_extraction import (
    walk_figure_entries,
    resolve_page_image,
    crop_figure_to_image,
)


@dataclass
class PairTask:
    source_pdf: str
    prob_idx: int
    image_kind: str
    img_idx: int
    base_id: str
    model: str
    img: PILImage.Image
    cerinta: str
    barem_text: Optional[str]
    cdl: str
    nl: str


class ConsistencyAssessment(BatchEmulator):
    EMPTY_ITEMS_LABEL = "No (image, model) pairs with BOTH CDL and NL found. Run extract-descriptions first."
    PROGRESS_LABEL = "Judging"

    def __init__(
        self,
        client: TogetherClient,
        *,
        prompts_dir: str = "prompts",
        max_workers: int = 10,
    ):
        super().__init__(client, max_workers=max_workers)
        self._consistency_prompt = Prompt("cdl_nl_consistency", prompts_dir)
        self._model: str = DEFAULT_CONSISTENCY_JUDGE_MODEL
        self._ocr_data: dict[str, list] = {}

    def _iter_work_items(self, ocr_data: dict[str, list], image_base: Path, verbose: bool) -> list[PairTask]:
        pairs: list[PairTask] = []
        for fe in walk_figure_entries(ocr_data):
            mrs = fe.entry.get("model_results", {})
            if not mrs:
                continue
            for m, res in mrs.items():
                cdl = (res.get("cdl") or {}).get("description")
                nl = (res.get("nl") or {}).get("natural_language")
                if not cdl or not nl:
                    continue
                page_img_path = resolve_page_image(fe.source_pdf, fe.entry.get("page_number", 0), fe.entry, image_base)
                if not page_img_path:
                    if verbose:
                        print(f"[SKIP] {fe.base_id} {m}: no saved page image")
                    continue
                fig_img = crop_figure_to_image(fe.source_pdf, fe.entry, image_base)
                if fig_img is None:
                    if verbose:
                        print(f"[WARNING] Failed to crop {fe.base_id} for judge")
                    continue
                pairs.append(PairTask(
                    source_pdf=fe.source_pdf, prob_idx=fe.prob_idx,
                    image_kind=fe.image_kind, img_idx=fe.img_idx, base_id=fe.base_id, model=m,
                    img=fig_img, cerinta=fe.cerinta, barem_text=fe.barem_text,
                    cdl=cdl, nl=nl,
                ))
        return pairs

    def plan_line(self, items: list) -> str:
        return f"[DEBUG] {len(items)} (image,model) pairs to judge with {self._model}"

    def build_tasks(self, items: list[PairTask], done: set) -> list[PairTask]:
        return [p for p in items if (p.base_id, p.model) not in done]

    def task_id(self, task: PairTask) -> tuple:
        return (task.base_id, task.model)

    def execute(self, task: PairTask) -> CompletionResult:
        prompt = self._consistency_prompt.render({
            "PROBLEM_TASK": task.cerinta,
            "CONTEXT": [task.barem_text, None],
            "CDL_DESCRIPTION": task.cdl,
            "NL_DESCRIPTION": task.nl,
        })
        return self.client.complete(
            self._model,
            query=[prompt, task.img],
            response_schema=ConsistencyVerdict,
        )

    def merge_result(self, task: PairTask, result: CompletionResult) -> None:
        probs = self._ocr_data[task.source_pdf]
        imgs = (probs[task.prob_idx].get("barem") or {}).get("imagini", []) if task.image_kind == "barem" else probs[task.prob_idx].get("imagini", [])
        img = imgs[task.img_idx]
        mrs = img.setdefault("model_results", {})
        m = mrs.setdefault(task.model, {})
        if not result.ok or result.content is None:
            m["consistency"] = {"consistent": False, "severity": "major", "issues": ["API call failed"]}
            return
        content = result.content
        m["consistency"] = {
            "is_geometric": content.is_geometric,
            "consistent": content.consistent,
            "severity": content.severity,
            "issues": content.issues,
            "suggested_cdl": content.suggested_cdl,
        }

    def summary_extras(self, items: list) -> dict[str, Any]:
        return {"pairs": len(items), "model": self._model}

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
                    if res.get("consistency"):
                        done.add((bid, m))
        _scan("imagini", p_pro.get("imagini", []), p_cur.get("imagini", []))
        if p_pro.get("barem") and p_cur.get("barem"):
            _scan("barem", p_pro["barem"].get("imagini", []), p_cur["barem"].get("imagini", []))

    def dump_state(self) -> Any:
        return self._ocr_data

    def run(
        self,
        enriched_file: str,
        image_base_dir: str,
        output_file: str,
        model: Optional[str] = None,
        verbose: bool = False,
    ) -> dict[str, Any]:
        self._model = model or DEFAULT_CONSISTENCY_JUDGE_MODEL
        with open(enriched_file, "r", encoding="utf-8") as f:
            self._ocr_data = json.load(f)
        items = self._iter_work_items(self._ocr_data, Path(image_base_dir), verbose)
        return self.run_pipeline(items, output_file, verbose=verbose)