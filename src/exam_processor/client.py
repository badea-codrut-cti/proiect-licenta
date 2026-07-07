"""Together.ai client wrapper."""

import json
import os
from pathlib import Path
from typing import Any, Optional

from together import Together
from dotenv import load_dotenv

load_dotenv()


class TogetherClient:
    """Client for interacting with Together.ai real-time API."""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Together.ai client."""
        self.api_key = api_key or os.getenv("TOGETHER_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Together API key is required. Set TOGETHER_API_KEY environment variable."
            )
        self.client = Together(api_key=self.api_key)

    def load_prompt(self, prompt_file: str) -> str:
        """Load a prompt template from file."""
        return Path(prompt_file).read_text(encoding="utf-8")

    def chat_completion(self, **kwargs) -> Any:
        """Fire a single chat.completions.create() call (real-time API)."""
        return self.client.chat.completions.create(**kwargs)

    def call_model(
        self,
        model: str,
        messages: list[dict],
        response_format: dict,
        *,
        max_tokens: int = 32768,
        temperature: float = 0.2,
    ) -> tuple[Optional[dict], int, int]:
        """Send a chat completion and parse the constrained JSON response.

        Returns ``(parsed_dict, prompt_tokens, completion_tokens)`` on success
        or ``(None, 0, 0)`` on any failure (API error, non-JSON content, parse
        error).  Failures are logged and swallowed -- the caller decides whether
        to treat them as "skip this work item" (DescriptionExtraction) or as
        "mark inconsistency as major with issues=['API call failed']"
        (ConsistencyAssessment).

        This consolidates the two near-identical ``_call_model`` copies that
        previously lived in ``ocr_batch.py`` and ``cdl_batch.py``.  As a side
        effect it also fixes the latent ``NameError: Any`` that ocr_batch's
        version carried (it referenced ``Any`` without importing it -- only
        ever worked in practice because callers happened to have ``Any`` in
        their own ``from typing import ...``).
        """
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
            }
            response = self.chat_completion(**kwargs)
            content = response.choices[0].message.content
            obj = json.loads(content)
            usage = response.usage
            return obj, usage.prompt_tokens, usage.completion_tokens
        except Exception as e:
            print(f"[ERROR] Model {model} failed: {e}")
            return None, 0, 0

    @staticmethod
    def build_image_content(prompt: str, image_url: str) -> list[dict]:
        """Build message content with text and image URL for multimodal models."""
        return [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]

