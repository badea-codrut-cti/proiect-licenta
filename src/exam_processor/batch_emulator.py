import json
import os
import threading
import concurrent.futures
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, Hashable, Optional, TypeVar

from tqdm import tqdm

from exam_processor.utils.client import CompletionResult, TogetherClient

T = TypeVar("T")


def _write_atomic(path: str | Path, text: str) -> None:
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


class BatchEmulator(ABC, Generic[T]):
    EMPTY_ITEMS_LABEL = "No items to process."
    PROGRESS_LABEL = "Processing"

    def __init__(self, client: TogetherClient, *, max_workers: int = 10):
        self.client = client
        self.max_workers = max_workers

    @abstractmethod
    def build_tasks(self, items: list, done: set) -> list[T]:
        ...

    @abstractmethod
    def task_id(self, task: T) -> Hashable:
        ...

    @abstractmethod
    def execute(self, task: T) -> CompletionResult:
        ...

    @abstractmethod
    def merge_result(self, task: T, result: CompletionResult) -> None:
        ...

    @abstractmethod
    def rehydrate_from_prev(self, prev: dict, done: set) -> None:
        ...

    @abstractmethod
    def dump_state(self) -> Any:
        ...

    def summary_extras(self, items: list) -> dict[str, Any]:
        return {}

    def plan_line(self, items: list) -> str:
        return f"[DEBUG] {len(items)} task(s) queued"

    def run_pipeline(
        self,
        items: list,
        output_file: str,
        *,
        verbose: bool = False,
    ) -> dict[str, Any]:
        if not items:
            raise ValueError(self.EMPTY_ITEMS_LABEL)

        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        done_path = out_path.with_suffix(out_path.suffix + ".done")
        done: set = set()

        resume_prev = self._read_resume(out_path)
        if resume_prev is not None:
            self.rehydrate_from_prev(resume_prev, done)
        if done_path.exists():
            try:
                with open(done_path, "r", encoding="utf-8") as f:
                    done |= {self._coerce_key(x) for x in json.load(f)}
            except Exception:
                pass

        if verbose and items:
            print(self.plan_line(items))
        tasks = self.build_tasks(items, done)
        if verbose and done:
            print(f"[DEBUG] Resuming: {len(done)} task(s) done; {len(tasks)} remaining")
        if not tasks:
            print("[INFO] All tasks already complete — nothing to do.")
            self._write_state(out_path, done)
            return self._build_summary(items, 0, 0, 0, 0, done)

        write_lock = threading.Lock()

        def _flush() -> None:
            with write_lock:
                self._write_state(out_path, done)

        ok = fail = total_in = total_out = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(self.execute, t): t for t in tasks}
            for future in tqdm(concurrent.futures.as_completed(futs), total=len(futs), desc=self.PROGRESS_LABEL):
                result = future.result()
                total_in += result.input_tokens
                total_out += result.output_tokens
                self.merge_result(futs[future], result)
                if result.ok:
                    ok += 1
                else:
                    fail += 1
                done.add(self.task_id(futs[future]))
                _flush()

        _flush()
        print(f"[INFO] {ok} ok / {fail} fail — {total_in:,} in + {total_out:,} out tokens")
        return self._build_summary(items, ok, fail, total_in, total_out, done)

    @staticmethod
    def _read_resume(out_path: Path) -> Optional[dict]:
        if not out_path.exists():
            return None
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
            print(f"[INFO] Resuming from {out_path}: {len(prev)} entries already done")
            return prev
        except Exception as e:
            print(f"[WARNING] Could not read previous output ({e}); starting fresh")
            return None

    @staticmethod
    def _coerce_key(raw) -> Hashable:
        if isinstance(raw, list):
            return tuple(raw)
        return raw

    @staticmethod
    def _serialize_task_id(key: Hashable) -> Any:
        if isinstance(key, tuple):
            return list(key)
        return key

    def _build_summary(self, items, ok, fail, total_in, total_out, done) -> dict[str, Any]:
        payload: dict[str, Any] = self.summary_extras(items)
        payload.update({
            "ok": ok,
            "fail": fail,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "resumed": max(0, len(done) - ok - fail),
        })
        return payload

    def _write_state(self, out_path: Path, done: set) -> None:
        try:
            _write_atomic(out_path, json.dumps(self.dump_state(), indent=2, ensure_ascii=False))
            done_payload = sorted(self._serialize_task_id(k) for k in done)
            _write_atomic(out_path.with_suffix(out_path.suffix + ".done"),
                         json.dumps(done_payload, ensure_ascii=False))
        except Exception as e:
            print(f"[WARNING] Failed to write incremental output: {e}")

