"""Model definitions with pricing info and validation for Together.ai."""

from dataclasses import dataclass

@dataclass
class ModelInfo:
    """Information about a Together.ai model."""
    name: str
    input_price_per_million: float  # $/1M tokens
    output_price_per_million: float  # $/1M tokens
    max_context_length: int
    supports_images: bool

    def input_price(self, tokens: int) -> float:
        """Calculate cost for input tokens in dollars."""
        return (tokens / 1_000_000) * self.input_price_per_million

    def output_price(self, tokens: int) -> float:
        """Calculate cost for output tokens in dollars."""
        return (tokens / 1_000_000) * self.output_price_per_million


# Format: (name, input_$/1M, output_$/1M, max_context, supports_images)
MODELS: dict[str, ModelInfo] = {
"Qwen/Qwen3.5-9B": ModelInfo(
        name="Qwen/Qwen3.5-9B",
        input_price_per_million=0.17,
        output_price_per_million=0.25,
        max_context_length=262144,
        supports_images=True,
    ),
    "Qwen/Qwen3.5-397B-A17B": ModelInfo(
        name="Qwen/Qwen3.5-397B-A17B",
        input_price_per_million=0.6,
        output_price_per_million=3.6,
        max_context_length=256000,
        supports_images=True,
    ),
    "google/gemma-4-31B-it": ModelInfo(
        name="google/gemma-4-31B-it",
        input_price_per_million=0.20,
        output_price_per_million=0.50,
        max_context_length=256000,
        supports_images=True,
    ),
    "moonshotai/Kimi-K2.6": ModelInfo(
        name="moonshotai/Kimi-K2.6",
        input_price_per_million=1.20,
        output_price_per_million=4.50,
        max_context_length=262144,
        supports_images=True,
    ),
}

DEFAULT_EXTRACTION_MODEL = "Qwen/Qwen3.5-9B"
DEFAULT_OCR_MODEL = "google/gemma-4-31B-it"
KIMI_K2_MODEL = "moonshotai/Kimi-K2.6"

# Models used for the multi-model CDL + NL description stage
DEFAULT_DESCRIPTION_MODELS = [
    "Qwen/Qwen3.5-9B",
    "google/gemma-4-31B-it",
    "moonshotai/Kimi-K2.6",
]

# Gemma 4 supports configurable vision token budgets: 70, 140, 280 (default), 560, 1120
# For OCR tasks, the highest budget (1120) is recommended for fine-grained detail.
GEMMA4_MAX_SOFT_TOKENS = 1120

# How much to expand each crop region beyond the LLM's bounding box,
# as a fraction of the box's width/height applied to each side.
# 0.15 = add 15% padding on left, top, right, bottom.
DEFAULT_OUTER_PADDING = 0.12


def get_model(model_name: str) -> ModelInfo:
    """Get model info by name. Raises ValueError if not found."""
    if model_name not in MODELS:
        available = ", ".join(MODELS.keys())
        raise ValueError(
            f"Unknown model: {model_name!r}\n"
            f"Available models: {available}"
        )
    return MODELS[model_name]


def format_usage_info(
    input_tokens: int,
    output_tokens: int,
    model: ModelInfo
) -> str:
    """Format token usage and cost info as a string."""
    input_cost = model.input_price(input_tokens)
    output_cost = model.output_price(output_tokens)
    total_cost = input_cost + output_cost

    return (
        f"Token usage: {input_tokens:,} input + {output_tokens:,} output "
        f"({input_tokens + output_tokens:,} total)\n"
        f"  Cost: ${input_cost:.4f} input + ${output_cost:.4f} output "
        f"= ${total_cost:.4f} total"
    )

