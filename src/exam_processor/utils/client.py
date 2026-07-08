import json
import os
from pathlib import Path
from typing import Any, Optional

from together import Together
from dotenv import load_dotenv

from exam_processor.utils.models import get_model

load_dotenv()


class TogetherClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("TOGETHER_API_KEY")
        if not self.api_key:
            raise ValueError("Together API key is required. Set TOGETHER_API_KEY environment variable.")
        self.client = Together(api_key=self.api_key)

    def load_prompt(self, prompt_file: str) -> str:
        return Path(prompt_file).read_text(encoding="utf-8")

    def chat_completion(self, **kwargs) -> Any:
        return self.client.chat.completions.create(**kwargs)

    def call_model(
        self,
        model: str,
        messages: list[dict],
        response_format: dict,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> tuple[Optional[dict], int, int]:
        try:
            info = get_model(model)
            resolved_max = info.effective_max_tokens(max_tokens)
            resolved_temp = info.effective_temperature(temperature)
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": resolved_max,
                "response_format": response_format,
            }
            if resolved_temp is not None:
                kwargs["temperature"] = resolved_temp
            response = self.chat_completion(**kwargs)
            obj = json.loads(response.choices[0].message.content)
            usage = response.usage
            return obj, usage.prompt_tokens, usage.completion_tokens
        except Exception as e:
            print(f"[ERROR] Model {model} failed: {e}")
            return None, 0, 0

    @staticmethod
    def build_image_content(prompt: str, image_url: str) -> list[dict]:
        return [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]