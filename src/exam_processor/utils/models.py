from dataclasses import dataclass
from typing import Optional

DEFAULT_MAX_TOKENS = 32768
DEFAULT_TEMPERATURE = 0.2
DEFAULT_IMAGE_QUALITY = 90

GEMMA4_MAX_SOFT_TOKENS = 1120
DEFAULT_OUTER_PADDING = 0.12


@dataclass
class ModelInfo:
    name: str
    input_price_per_million: float
    output_price_per_million: float
    max_context_length: int
    supports_images: bool
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    supports_temperature: bool = True

    def input_price(self, tokens: int) -> float:
        return (tokens / 1_000_000) * self.input_price_per_million

    def output_price(self, tokens: int) -> float:
        return (tokens / 1_000_000) * self.output_price_per_million

    def effective_max_tokens(self, user_max_tokens: Optional[int] = None) -> int:
        if user_max_tokens is not None:
            return user_max_tokens
        if self.max_tokens is not None:
            return self.max_tokens
        return DEFAULT_MAX_TOKENS

    def effective_temperature(self, user_temperature: Optional[float] = None) -> Optional[float]:
        if not self.supports_temperature:
            return None
        if user_temperature is not None:
            return user_temperature
        if self.temperature is not None:
            return self.temperature
        return DEFAULT_TEMPERATURE


MODELS: dict[str, ModelInfo] = {
    "Qwen/Qwen3.5-9B": ModelInfo(
        name="Qwen/Qwen3.5-9B",
        input_price_per_million=0.17,
        output_price_per_million=0.25,
        max_context_length=262144,
        supports_images=True,
        temperature=0.7,
    ),
    "Qwen/Qwen3.5-397B-A17B": ModelInfo(
        name="Qwen/Qwen3.5-397B-A17B",
        input_price_per_million=0.6,
        output_price_per_million=3.6,
        max_context_length=256000,
        supports_images=True,
        temperature=0.7,
    ),
    "google/gemma-4-31B-it": ModelInfo(
        name="google/gemma-4-31B-it",
        input_price_per_million=0.20,
        output_price_per_million=0.50,
        max_context_length=256000,
        supports_images=True,
        temperature=1.0,
    ),
    "moonshotai/Kimi-K2.6": ModelInfo(
        name="moonshotai/Kimi-K2.6",
        input_price_per_million=1.20,
        output_price_per_million=4.50,
        max_context_length=262144,
        supports_images=True,
        supports_temperature=False,
        temperature=None,
    ),
}

DEFAULT_OCR_MODEL = "google/gemma-4-31B-it"

DEFAULT_DESCRIPTION_MODELS = [
    "Qwen/Qwen3.5-9B",
    "google/gemma-4-31B-it",
    "moonshotai/Kimi-K2.6",
]

DEFAULT_CONSISTENCY_JUDGE_MODEL = "Qwen/Qwen3.5-9B"


def get_model(model_name: str) -> ModelInfo:
    if model_name not in MODELS:
        available = ", ".join(MODELS.keys())
        raise ValueError(
            f"Unknown model: {model_name!r}\n"
            f"Available models: {available}"
        )
    return MODELS[model_name]


def format_usage_info(input_tokens: int, output_tokens: int, model: ModelInfo) -> str:
    input_cost = model.input_price(input_tokens)
    output_cost = model.output_price(output_tokens)
    total_cost = input_cost + output_cost
    return (
        f"Token usage: {input_tokens:,} input + {output_tokens:,} output "
        f"({input_tokens + output_tokens:,} total)\n"
        f"  Cost: ${input_cost:.4f} input + ${output_cost:.4f} output "
        f"= ${total_cost:.4f} total"
    )
