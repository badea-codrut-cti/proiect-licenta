import re
from pathlib import Path
from typing import Optional

_INCLUDE_RE = re.compile(r"\{\{>\s*([A-Za-z0-9_]+)\s*\}\}")


class Prompt:
    def __init__(
        self,
        name: str,
        prompts_dir: str = "prompts",
        values: Optional[dict] = None,
        *,
        dependencies: Optional[list["Prompt"]] = None,
    ):
        self.name = name
        self.prompts_dir = prompts_dir
        self.values = values or {}
        self._dep_by_name = {dep.name: dep for dep in (dependencies or [])}

    @property
    def path(self) -> Path:
        return Path(self.prompts_dir) / f"{self.name}.txt"

    @property
    def template(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def render(self, values: Optional[dict] = None) -> str:
        template = self._expand_includes(self.template)
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

    def _expand_includes(self, template: str, _stack: Optional[set] = None) -> str:
        stack = _stack if _stack is not None else set()

        def _replace(match: re.Match) -> str:
            partial_name = match.group(1)
            if partial_name in stack:
                return f"{{{{<!-- cyclic include: {partial_name} -->}}}}"
            dep = self._dep_by_name.get(partial_name)
            if dep is None:
                return f"{{{{<!-- missing partial: {partial_name} -->}}}}"
            return self._expand_includes(dep.template.rstrip("\n"), stack | {partial_name})

        return _INCLUDE_RE.sub(_replace, template)

