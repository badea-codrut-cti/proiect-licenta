import base64
import io
import json
import os
from dataclasses import dataclass
from typing import Any, Generic, Optional, TypeVar, Union, overload

from pydantic import BaseModel
from together import Together
from dotenv import load_dotenv
from PIL import Image as PILImage

from exam_processor.utils.models import DEFAULT_IMAGE_QUALITY, get_model

load_dotenv()

History = list[tuple[str, str]]
QueryItem = Union[str, PILImage.Image]
T = TypeVar("T")


@dataclass
class CompletionResult(Generic[T]):
    ok: bool
    content: T | None
    input_tokens: int
    output_tokens: int
    error: str | None


class TogetherClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("TOGETHER_API_KEY")
        if not self.api_key:
            raise ValueError("Together API key is required. Set TOGETHER_API_KEY environment variable.")
        self.client = Together(api_key=self.api_key)

    @overload
    def complete(self, model: str, query: Union[str, list[QueryItem]], *, system_prompt: Optional[str] = None, history: Optional[History] = None, response_schema: type[T], max_tokens: Optional[int] = None, temperature: Optional[float] = None) -> CompletionResult[T]: ...
    @overload
    def complete(self, model: str, query: Union[str, list[QueryItem]], *, system_prompt: Optional[str] = None, history: Optional[History] = None, response_schema: dict, max_tokens: Optional[int] = None, temperature: Optional[float] = None) -> CompletionResult[dict]: ...
    @overload
    def complete(self, model: str, query: Union[str, list[QueryItem]], *, system_prompt: Optional[str] = None, history: Optional[History] = None, response_schema: None = None, max_tokens: Optional[int] = None, temperature: Optional[float] = None) -> CompletionResult[str]: ...
    def complete(self, model, query, *, system_prompt=None, history=None, response_schema=None, max_tokens=None, temperature=None):
        try:
            info = get_model(model)
            resolved_max = info.effective_max_tokens(max_tokens)
            resolved_temp = info.effective_temperature(temperature)
            response_format = self._resolve_response_format(response_schema)

            messages: list[dict] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if history:
                for user_text, assistant_text in history:
                    messages.append({"role": "user", "content": user_text})
                    messages.append({"role": "assistant", "content": assistant_text})
            messages.append({"role": "user", "content": self._build_user_content(query)})

            kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": resolved_max}
            if response_format is not None:
                kwargs["response_format"] = response_format
            if resolved_temp is not None:
                kwargs["temperature"] = resolved_temp

            response = self._post(**kwargs)
            raw = response.choices[0].message.content
            usage = response.usage
            content = self._parse_content(raw, response_schema)
            return CompletionResult(
                ok=True,
                content=content,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                error=None,
            )
        except Exception as e:
            print(f"[ERROR] Model {model} failed: {e}")
            return CompletionResult(ok=False, content=None, input_tokens=0, output_tokens=0, error=str(e))

    def _post(self, **kwargs) -> Any:
        return self.client.chat.completions.create(**kwargs)

    @staticmethod
    def _resolve_response_format(schema: Union[type, dict, None]) -> Optional[dict]:
        if schema is None:
            return None
        if isinstance(schema, dict):
            return schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
        return None

    @staticmethod
    def _parse_content(raw: str, schema: Union[type, dict, None]):
        if schema is None:
            return raw
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(json.loads(raw))
        return json.loads(raw)

    @staticmethod
    def _build_user_content(query: Union[str, list[QueryItem]]) -> list[dict]:
        if isinstance(query, str):
            return [{"type": "text", "text": query}]
        content: list[dict] = []
        for item in query:
            if isinstance(item, PILImage.Image):
                content.append({"type": "image_url", "image_url": {"url": TogetherClient._encode_image(item)}})
            else:
                content.append({"type": "text", "text": str(item)})
        return content

    @staticmethod
    def _encode_image(img: PILImage.Image) -> str:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=DEFAULT_IMAGE_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"

