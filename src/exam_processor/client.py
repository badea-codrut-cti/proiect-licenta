"""Together.ai client wrapper."""

import os
from pathlib import Path
from typing import Optional, Any

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

    @staticmethod
    def build_image_content(prompt: str, image_url: str) -> list[dict]:
        """Build message content with text and image URL for multimodal models."""
        return [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]

