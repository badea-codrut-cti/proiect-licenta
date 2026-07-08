from pathlib import Path
from typing import Optional


class Prompt:
    def __init__(self, name: str, prompts_dir: str = "prompts", values: Optional[dict] = None):
        self.name = name
        self.prompts_dir = prompts_dir
        self.values = values or {}

    @property
    def path(self) -> Path:
        return Path(self.prompts_dir) / f"{self.name}.txt"

    @property
    def template(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def render(self, values: Optional[dict] = None) -> str:
        template = self.template
        merged = {**self.values, **(values or {})}
        for key, raw in merged.items():
            occ: Optional[list[object]] = list(raw) if isinstance(raw, list) else None
            marker = f"{{{{{key}}}}}"
            cursor = 0
            slot = 0
            while True:
                idx = template.find(marker, cursor)
                if idx == -1:
                    break
                if occ is not None:
                    repl: Optional[object] = occ[slot] if slot < len(occ) else None
                else:
                    repl = raw
                text = "" if repl is None else str(repl)
                template = (
                    template[:idx]
                    + text
                    + template[idx + len(marker):]
                )
                cursor = idx + len(text)
                slot += 1
        return template

