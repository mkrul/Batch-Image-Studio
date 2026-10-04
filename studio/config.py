from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUPPORT = Path.home() / "Library" / "Application Support" / "BatchImageStudio"
SETTINGS_PATH = SUPPORT / "settings.json"
CUTOUT_DIR = SUPPORT / "cutouts"

IMAGE_MODELS = [
    ("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst — best precision"),
    ("gpt-image-2.5-sunburst-2026-09-08", "GPT Image 2.5 Sunburst — 8 Sep 2026 snapshot"),
    ("gpt-image-2.5-flare", "GPT Image 2.5 Flare — faster"),
    ("gpt-image-2.5-flare-2026-09-08", "GPT Image 2.5 Flare — 8 Sep 2026 snapshot"),
    ("gpt-image-2", "GPT Image 2"),
    ("gpt-image-1.5", "GPT Image 1.5"),
]

SCREEN_MODELS = [
    ("gpt-5.4", "GPT-5.4 — careful review"),
    ("gpt-5.4-mini", "GPT-5.4 mini — cheaper review"),
]

BACKGROUNDS = [
    ("opaque", "Opaque"),
    ("transparent", "Transparent"),
    ("auto", "Automatic"),
]

MODES = [
    ("reference", "Reference only"),
    ("lock", "Lock product pixels"),
    ("composite", "New scene, paste product"),
]

QUALITIES_CURRENT = ["low", "medium", "high", "xhigh", "max"]
QUALITIES_EARLIER = ["low", "medium", "high"]
SIZES_CURRENT = ["auto", "1024x1024", "1536x1024", "1024x1536", "2048x1152", "2048x2048"]
SIZES_LEGACY = ["1024x1024", "1536x1024", "1024x1536"]

DEFAULT_CRITERIA = """Fail the image if the product is missing, duplicated, or not the same product as the photographs.
Fail the image if the package shape, proportions, or colors changed.
Fail the image if label text is missing, wrong, or unreadable when it is readable in the photographs.
Fail the image if parts were added or removed.
Fail the image if the product is cropped off.
When an approved example is attached, fail the image if the lighting, framing, or background clearly does not match it.
"""

MODE_HELP = {
    "reference": "The model creates the whole image. It can redraw the product.",
    "lock": "The product stays in its original frame. After the model renders the scene, the original product pixels are put back. Create a cutout or choose a mask first.",
    "composite": "The model renders a scene and does not render the product. The program pastes your cutout onto the lower center. Use a PNG with transparency, or create a cutout first.",
}


def default_output() -> str:
    return str(Path.home() / "Pictures" / "Batch Image Studio")


def qualities_for(model: str) -> list[str]:
    if model.startswith("gpt-image-2.5"):
        return list(QUALITIES_CURRENT)
    return list(QUALITIES_EARLIER)


def sizes_for(model: str) -> list[str]:
    if model.startswith("gpt-image-1"):
        return list(SIZES_LEGACY)
    return list(SIZES_CURRENT)


def default_settings() -> dict:
    return {
        "version": 1,
        "prompt": "",
        "criteria": DEFAULT_CRITERIA.strip(),
        "send_criteria": True,
        "screen": True,
        "references": [],
        "products": [],
        "model": IMAGE_MODELS[0][0],
        "quality": "high",
        "size": "1024x1024",
        "background": "opaque",
        "candidates": 4,
        "parallel": 2,
        "retries": 1,
        "spend_cap": 10.0,
        "screen_model": SCREEN_MODELS[0][0],
        "output_dir": default_output(),
        "geometry": "",
        "theme": "light",
    }


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load_settings() -> dict:
    settings = default_settings()
    if not SETTINGS_PATH.exists():
        return settings
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    if isinstance(stored, dict):
        settings.update({key: stored[key] for key in settings if key in stored})
    return settings


def save_settings(settings: dict) -> None:
    payload = default_settings()
    payload.update(settings)
    payload["version"] = 1
    atomic_write(SETTINGS_PATH, json.dumps(payload, indent=2))


def _env_lines() -> list[str]:
    path = ROOT / ".env"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _write_env(lines: list[str]) -> None:
    path = ROOT / ".env"
    text = ("\n".join(lines).rstrip() + "\n") if lines else ""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def load_api_key() -> str:
    for line in _env_lines():
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if not stripped.startswith("OPENAI_API_KEY="):
            continue
        value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
        if value:
            return value
    return os.environ.get("OPENAI_API_KEY", "").strip()


def save_api_key(raw: str) -> None:
    key = raw.strip().strip('"').strip("'")
    if key.startswith("export "):
        key = key[len("export "):].strip()
    if key.startswith("OPENAI_API_KEY="):
        key = key.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise ValueError("The key is empty.")
    lines = []
    replaced = False
    for line in _env_lines():
        if line.startswith("OPENAI_API_KEY=") or line.startswith("export OPENAI_API_KEY="):
            if not replaced:
                lines.append(f"OPENAI_API_KEY={key}")
                replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.append(f"OPENAI_API_KEY={key}")
    _write_env(lines)
    os.environ["OPENAI_API_KEY"] = key


def clear_api_key() -> None:
    lines = [
        line
        for line in _env_lines()
        if not line.startswith("OPENAI_API_KEY=") and not line.startswith("export OPENAI_API_KEY=")
    ]
    _write_env(lines)
    os.environ.pop("OPENAI_API_KEY", None)
