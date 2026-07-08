import json
import os
import threading
import concurrent.futures
from pathlib import Path
from typing import Any, Hashable, Optional

from tqdm import tqdm

from exam_processor.utils.client import CompletionResult, TogetherClient
from exam_processor.utils.prompt import Prompt


def _write_atomic(path: str | Path, text: str) -> None:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


class BaseExtraction:
    def __init__(
        self,
        client: TogetherClient,
        prompt: Prompt | list[Prompt] | None = None,
        *,
        max_workers: int = 10,
    ):
        self.client = client
        self.prompt = prompt
        self.max_workers = max_workers
        self._state: dict[str, Any] = {}

    def _iter_figure_entries(self, ocr_data: dict[str, list]):
        for doc_idx, (source_pdf, problems) in enumerate(ocr_data.items()):
            for prob_idx, prob in enumerate(problems):
                cerinta = prob.get("cerinta", "")
                barem = prob.get("barem")
                barem_text = barem.get("explicatie", "") if barem else None
                for i, e in enumerate(prob.get("imagini", [])):
                    yield doc_idx, source_pdf, prob_idx, cerinta, barem_text, "imagini", i, e
                if barem:
                    for i, e in enumerate(barem.get("imagini", [])):
                        yield doc_idx, source_pdf, prob_idx, cerinta, barem_text, "barem", i, e

    def _build_tasks(self, items: list, done: set) -> list:
        raise NotImplementedError

    def _done_key(self, task: Any) -> Hashable:
        raise NotImplementedError

    def _execute(self, task: Any) -> CompletionResult:
        raise NotImplementedError

    def _merge(self, task: Any, result: CompletionResult) -> tuple[int, int]:
        raise NotImplementedError

    def _empty_items_error(self) -> str:
        raise NotImplementedError

    def _progress_desc(self) -> str:
        raise NotImplementedError

    def _summary_fields(self, items: list) -> dict[str, Any]:
        raise NotImplementedError

    def _verbose_plan_line(self, items: list, verbose: bool) -> None:
        if verbose and items:
            print(self._plan_line(items))

    def _plan_line(self, items: list) -> str:
        return f"[DEBUG] {len(items)} tasks queued"

    def _build_summary(self, items, ok, fail, total_in, total_out, done) -> dict[str, Any]:
        payload: dict[str, Any] = self._summary_fields(items)
        payload.update({
            "ok": ok,
            "fail": fail,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "resumed": max(0, len(done) - ok - fail),
        })
        return payload

    def _run_pipeline(
        self,
        items: list,
        output_file: str,
        *,
        resume_prev: Optional[dict] = None,
        verbose: bool = False,
    ) -> dict[str, Any]:
        if not items:
            raise ValueError(self._empty_items_error())

        out_json_path = Path(output_file)
        done_path = out_json_path.with_suffix(out_json_path.suffix + ".done")
        done: set = set()

        if resume_prev is not None:
            self._rehydrate_from_prev(resume_prev, done)
        if done_path.exists():
            try:
                with open(done_path, "r", encoding="utf-8") as f:
                    done |= {self._coerce_key(x) for x in json.load(f)}
            except Exception:
                pass

        self._verbose_plan_line(items, verbose)
        tasks = self._build_tasks(items, done)
        if verbose and done:
            print(f"[DEBUG] Resuming: {len(done)} task(s) done; {len(tasks)} remaining")
        if not tasks:
            print("[INFO] All tasks already complete — nothing to do.")
            self._flush_state(out_json_path, done)
            return self._build_summary(items, 0, 0, 0, 0, done)

        _write_lock = threading.Lock()

        def _flush() -> None:
            with _write_lock:
                self._flush_state(out_json_path, done)

        ok = fail = total_in = total_out = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(self._execute, t): t for t in tasks}
            for future in tqdm(concurrent.futures.as_completed(futs), total=len(futs), desc=self._progress_desc()):
                result = future.result()
                total_in += result.input_tokens
                total_out += result.output_tokens
                ok_d, fail_d = self._merge(futs[future], result)
                ok += ok_d
                fail += fail_d
                done.add(self._done_key(futs[future]))
                _flush()

        _flush()
        print(f"[INFO] {ok} ok / {fail} fail — {total_in:,} in + {total_out:,} out tokens")
        return self._build_summary(items, ok, fail, total_in, total_out, done)

    def _coerce_key(self, raw) -> Hashable:
        if isinstance(raw, list):
            return tuple(raw)
        return raw

    def _rehydrate_from_prev(self, prev: dict, done: set) -> None:
        raise NotImplementedError

    def _dump_state(self) -> Any:
        raise NotImplementedError

    def _serialize_done_key(self, key: Any) -> Any:
        raise NotImplementedError

    def _flush_state(self, out_json_path: Path, done: set) -> None:
        try:
            _write_atomic(out_json_path, json.dumps(self._dump_state(), indent=2, ensure_ascii=False))
            done_payload = sorted(self._serialize_done_key(k) for k in done)
            _write_atomic(out_json_path.with_suffix(out_json_path.suffix + ".done"),
                         json.dumps(done_payload, ensure_ascii=False))
        except Exception as e:
            print(f"[WARNING] Failed to write incremental output: {e}")

