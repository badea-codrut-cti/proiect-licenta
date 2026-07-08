from pathlib import Path
from typing import Optional

from exam_processor.utils.models import render_prompt


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
        merged = {**self.values, **(values or {})}
        return render_prompt(self.template, merged)