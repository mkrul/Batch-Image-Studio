from __future__ import annotations

TEXT_RATES = {
    "gpt-5.4": {"input": 2.50, "cached": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached": None, "output": 4.50},
}

IMAGE_TEXT_INPUT = 5.0
IMAGE_INPUT = 8.0
IMAGE_OUTPUT = 30.0

QUALITY_ALLOWANCE = {
    "low": 0.08,
    "medium": 0.25,
    "high": 0.80,
    "xhigh": 1.60,
    "max": 3.00,
}


def money(value: float) -> str:
    if abs(value) < 1:
        return f"${value:.4f}"
    return f"${value:.2f}"


def estimate_image_call(quality: str, size: str, input_images: int) -> float:
    base = QUALITY_ALLOWANCE.get(quality, 0.80)
    if "x" in size and size != "auto":
        width_text, height_text = size.lower().split("x", 1)
        edge = max(int(width_text), int(height_text))
        if edge > 2048:
            base *= 4
        elif edge > 1536:
            base *= 2
    return base + (0.05 * max(0, input_images))


def estimate_screen_call(model: str, image_count: int) -> float:
    base = 0.03 if model == "gpt-5.4-mini" else 0.10
    return base + (0.015 * max(1, image_count))


def _dig(obj: object, *names: str, default: object = None) -> object:
    current = obj
    for name in names:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return default if current is None else current


def cost_from_image_usage(usage: object) -> float | None:
    if usage is None:
        return None
    input_tokens = int(_dig(usage, "input_tokens", default=0) or 0)
    output_tokens = int(_dig(usage, "output_tokens", default=0) or 0)
    image_in = _dig(usage, "input_tokens_details", "image_tokens", default=None)
    text_in = _dig(usage, "input_tokens_details", "text_tokens", default=None)
    if image_in is None and text_in is None:
        image_in = input_tokens
        text_in = 0
    else:
        image_in = int(image_in or 0)
        text_in = int(text_in or 0)
    remainder = input_tokens - (image_in + text_in)
    if remainder > 0:
        image_in += remainder
    total = (text_in * IMAGE_TEXT_INPUT) + (image_in * IMAGE_INPUT) + (output_tokens * IMAGE_OUTPUT)
    return total / 1_000_000


def cost_from_text_usage(usage: object, model: str) -> float | None:
    if usage is None:
        return None
    rates = TEXT_RATES.get(model, TEXT_RATES["gpt-5.4"])
    input_tokens = int(_dig(usage, "input_tokens", default=0) or 0)
    output_tokens = int(_dig(usage, "output_tokens", default=0) or 0)
    cached = int(_dig(usage, "input_tokens_details", "cached_tokens", default=0) or 0)
    cached = max(0, min(cached, input_tokens))
    cached_rate = rates["cached"]
    if cached_rate is None:
        cached = 0
        cached_rate = 0.0
    uncached = input_tokens - cached
    total = (uncached * rates["input"]) + (cached * cached_rate) + (output_tokens * rates["output"])
    return total / 1_000_000
