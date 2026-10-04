from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".heic", ".heif", ".bmp"}
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
MAX_EDGE = 3840


def list_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in IMAGE_EXTENSIONS:
            found.append(path)
    return found


def safe_slug(name: str) -> str:
    cleaned = []
    for character in name.strip().lower():
        if character.isalnum():
            cleaned.append(character)
        elif character in {" ", "-", "_"}:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:60] or "image"


def load_rgba(path: Path) -> Image.Image:
    with Image.open(path) as image:
        oriented = ImageOps.exif_transpose(image)
        converted = oriented.convert("RGBA")
        converted.load()
        return converted


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def resize_exact(image: Image.Image, width: int, height: int) -> Image.Image:
    converted = image.convert("RGBA")
    if converted.size == (width, height):
        return converted
    return converted.resize((width, height), Image.Resampling.LANCZOS)


def locked_output_size(path: Path) -> str:
    image = load_rgba(path)
    width, height = legal_size(*image.size)
    return f"{width}x{height}"


def legal_size(width: int, height: int) -> tuple[int, int]:
    w = max(1, int(width))
    h = max(1, int(height))
    aspect = w / h
    if aspect > 3:
        w = max(1, int(round(h * 3)))
    elif aspect < (1 / 3):
        h = max(1, int(round(w * 3)))
    pixels = w * h
    scale = 1.0
    longest = max(w, h)
    if pixels < MIN_PIXELS:
        scale = (MIN_PIXELS / pixels) ** 0.5
    elif pixels > MAX_PIXELS or longest > MAX_EDGE:
        scale = min((MAX_PIXELS / pixels) ** 0.5, MAX_EDGE / longest)
    w = max(16, int(round((w * scale) / 16.0)) * 16)
    h = max(16, int(round((h * scale) / 16.0)) * 16)
    w = min(MAX_EDGE, w)
    h = min(MAX_EDGE, h)

    def aspect_ok(width_value: int, height_value: int) -> bool:
        return (width_value / height_value) <= 3 and (height_value / width_value) <= 3

    while not aspect_ok(w, h) and w > 16 and h > 16:
        if w > h * 3:
            w -= 16
        else:
            h -= 16
    guard = 0
    while w * h < MIN_PIXELS and guard < 400:
        guard += 1
        grown_w = min(MAX_EDGE, w + 16)
        grown_h = min(MAX_EDGE, h + 16)
        if grown_w == w and grown_h == h:
            break
        if aspect_ok(grown_w, h) and (grown_w * h) <= MAX_PIXELS:
            w = grown_w
        elif aspect_ok(w, grown_h) and (w * grown_h) <= MAX_PIXELS:
            h = grown_h
        elif aspect_ok(grown_w, grown_h) and (grown_w * grown_h) <= MAX_PIXELS:
            w = grown_w
            h = grown_h
        else:
            break
    guard = 0
    while (w * h > MAX_PIXELS or w > MAX_EDGE or h > MAX_EDGE) and w > 16 and h > 16 and guard < 400:
        guard += 1
        if w >= h and w > 16:
            w -= 16
        elif h > 16:
            h -= 16
    w = max(16, (w // 16) * 16)
    h = max(16, (h // 16) * 16)
    return w, h


def transparent_fraction(image: Image.Image) -> float:
    alpha = np.array(image.convert("RGBA").getchannel("A"))
    if alpha.size == 0:
        return 0.0
    return float(np.mean(alpha < 250))


def kept_fraction(protect: Image.Image) -> float:
    alpha = np.array(protect.convert("L"))
    if alpha.size == 0:
        return 0.0
    return float(np.mean(alpha >= 128))


def is_cutout(image: Image.Image) -> bool:
    return transparent_fraction(image) >= 0.02


def protect_from_alpha(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    alpha = image.convert("RGBA").resize(size, Image.Resampling.LANCZOS).getchannel("A")
    return alpha.point(lambda value: 255 if value >= 16 else 0)


def protect_from_user_mask(mask: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = mask.convert("RGBA").resize(size, Image.Resampling.NEAREST)
    alpha = np.array(image.getchannel("A"))
    if int(alpha.min()) < 250:
        protect = np.where(alpha >= 128, 255, 0).astype(np.uint8)
    else:
        luma = np.array(image.convert("L"))
        protect = np.where(luma >= 128, 255, 0).astype(np.uint8)
    return Image.fromarray(protect, mode="L")


def center_protect(width: int, height: int, scale: float) -> Image.Image:
    fraction = min(0.90, max(0.15, float(scale)))
    box_w = min(width, max(16, int(round(width * fraction))))
    box_h = min(height, max(16, int(round(height * fraction))))
    x0 = max(0, (width - box_w) // 2)
    y0 = max(0, (height - box_h) // 2)
    band = np.full((height, width), 255, dtype=np.uint8)
    band[y0 : y0 + box_h, x0 : x0 + box_w] = 0
    return Image.fromarray(band, mode="L")


def soften_protect(protect: Image.Image, radius: float = 8) -> Image.Image:
    return protect.convert("L").filter(ImageFilter.GaussianBlur(radius))


def openai_mask_bytes(protect: Image.Image) -> bytes:
    band = protect.convert("L")
    alpha = np.array(band, dtype=np.uint8)
    rgba = np.zeros((band.height, band.width, 4), dtype=np.uint8)
    rgba[..., 3] = alpha
    return png_bytes(Image.fromarray(rgba, mode="RGBA"))


def encode_reference(path: Path, max_edge: int = 2048) -> tuple[bytes, str]:
    image = load_rgba(path)
    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    data = png_bytes(image)
    if len(data) <= 12_000_000:
        return data, ".png"
    rgb = image.convert("RGB")
    last = b""
    for quality in (90, 82, 74):
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=quality)
        last = buffer.getvalue()
        if len(last) <= 12_000_000:
            return last, ".jpg"
    return last, ".jpg"


def restore_pixels(generated: Image.Image, original: Image.Image, protect: Image.Image) -> Image.Image:
    output = generated.convert("RGBA")
    source = original.convert("RGBA")
    band = protect.convert("L")
    if output.size != source.size:
        output = output.resize(source.size, Image.Resampling.LANCZOS)
    if band.size != source.size:
        band = band.resize(source.size, Image.Resampling.NEAREST)
    output.paste(source, (0, 0), band)
    return output


def composite_product(scene: Image.Image, cutout: Image.Image, scale: float) -> Image.Image:
    base = scene.convert("RGBA")
    sprite = cutout.convert("RGBA")
    if transparent_fraction(sprite) < 0.01:
        raise RuntimeError("The cutout has no transparent backdrop, so it cannot be pasted.")
    scene_w, scene_h = base.size
    fraction = min(0.90, max(0.15, float(scale)))
    target_h = max(1, int(scene_h * fraction))
    target_w = max(1, int(sprite.width * (target_h / sprite.height)))
    limit_w = int(scene_w * 0.86)
    if target_w > limit_w:
        target_w = max(1, limit_w)
        target_h = max(1, int(sprite.height * (target_w / sprite.width)))
    sprite = sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)
    red, green, blue, alpha = sprite.split()
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))
    sprite = Image.merge("RGBA", (red, green, blue, alpha))
    x = max(0, (scene_w - target_w) // 2)
    y = max(0, scene_h - target_h - int(scene_h * 0.05))
    pad = 28
    shadow = Image.new("RGBA", (target_w + pad * 2, target_h + pad * 2), (0, 0, 0, 0))
    shade = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    shade.putalpha(sprite.getchannel("A").point(lambda value: int(value * 0.40)))
    shadow.paste(shade, (pad, pad), shade)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    base.paste(shadow, (x - pad, y - pad + 10), shadow)
    base.paste(sprite, (x, y), sprite)
    return base


def checker_preview(image: Image.Image, edge: int = 140) -> Image.Image:
    thumb = image.convert("RGBA")
    thumb.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    width, height = thumb.size
    background = Image.new("RGB", (width, height))
    pixels = background.load()
    for y in range(height):
        for x in range(width):
            value = 214 if ((x // 8) + (y // 8)) % 2 == 0 else 168
            pixels[x, y] = (value, value, value)
    background.paste(thumb, (0, 0), thumb)
    return background


def _jpeg_under(image: Image.Image, limit: int) -> bytes:
    rgb = image.convert("RGB")
    last = b""
    edge = max(rgb.size)
    while edge >= 256:
        current = rgb.copy()
        current.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        for quality in (85, 70, 55, 40):
            buffer = io.BytesIO()
            current.save(buffer, format="JPEG", quality=quality)
            last = buffer.getvalue()
            if len(last) <= limit:
                return last
        edge = int(edge * 0.75)
    return last


def data_url(path: Path, max_edge: int = 2048) -> str:
    image = load_rgba(path)
    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    data = png_bytes(image)
    mime = "image/png"
    if len(data) > 4_000_000:
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        data = png_bytes(image)
    if len(data) > 4_000_000:
        data = _jpeg_under(image, 4_000_000)
        mime = "image/jpeg"
    encoded = base64.standard_b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _bfs(near: np.ndarray) -> np.ndarray:
    height, width = near.shape
    flat = np.ascontiguousarray(near, dtype=np.uint8).ravel()
    seen = np.zeros(height * width, dtype=np.uint8)
    queue: list[int] = []
    for x in range(width):
        top = x
        if flat[top] and not seen[top]:
            seen[top] = 1
            queue.append(top)
        bottom = (height - 1) * width + x
        if flat[bottom] and not seen[bottom]:
            seen[bottom] = 1
            queue.append(bottom)
    if height > 2 and width > 1:
        for y in range(1, height - 1):
            left = y * width
            right = left + width - 1
            if flat[left] and not seen[left]:
                seen[left] = 1
                queue.append(left)
            if flat[right] and not seen[right]:
                seen[right] = 1
                queue.append(right)
    head = 0
    while head < len(queue):
        index = queue[head]
        head += 1
        y, x = divmod(index, width)
        if x > 0:
            neighbor = index - 1
            if flat[neighbor] and not seen[neighbor]:
                seen[neighbor] = 1
                queue.append(neighbor)
        if x + 1 < width:
            neighbor = index + 1
            if flat[neighbor] and not seen[neighbor]:
                seen[neighbor] = 1
                queue.append(neighbor)
        if y > 0:
            neighbor = index - width
            if flat[neighbor] and not seen[neighbor]:
                seen[neighbor] = 1
                queue.append(neighbor)
        if y + 1 < height:
            neighbor = index + width
            if flat[neighbor] and not seen[neighbor]:
                seen[neighbor] = 1
                queue.append(neighbor)
    return seen.reshape(height, width).astype(bool)


def border_connected(near: np.ndarray) -> np.ndarray:
    height, width = near.shape
    if max(height, width) <= 1600:
        return _bfs(near)
    scale = 1600 / max(height, width)
    small_w = max(1, int(width * scale))
    small_h = max(1, int(height * scale))
    small = np.array(
        Image.fromarray((near.astype(np.uint8) * 255)).resize((small_w, small_h), Image.Resampling.NEAREST)
    ) > 0
    linked = _bfs(small)
    upscaled = np.array(
        Image.fromarray((linked.astype(np.uint8) * 255)).resize((width, height), Image.Resampling.NEAREST)
    ) > 0
    return upscaled & near


def flood_cutout(image: Image.Image, tolerance: int) -> Image.Image:
    rgba = image.convert("RGBA")
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.int16)
    height, width = rgb.shape[:2]
    corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    spread = int(np.max(corners.max(axis=0) - corners.min(axis=0)))
    if spread > tolerance:
        raise ValueError("The corners of this photograph are different colors, so the backdrop is not plain.")
    background = np.median(corners, axis=0)
    near = np.max(np.abs(rgb - background), axis=2) <= int(tolerance)
    connected = border_connected(near)
    fraction = float(connected.mean())
    if fraction < 0.02:
        raise ValueError("Almost no backdrop was found. Raise the tolerance, or use a PNG that already has transparency.")
    if fraction > 0.95:
        raise ValueError("The cutout removed almost the whole photograph. Lower the tolerance and try again.")
    output = np.array(rgba)
    output[..., 3] = np.minimum(output[..., 3], np.where(connected, 0, 255).astype(np.uint8))
    return Image.fromarray(output, mode="RGBA")


def rembg_cutout(image: Image.Image) -> Image.Image:
    try:
        from rembg import remove
    except ImportError as exc:
        raise RuntimeError(
            "The local cutout library is not installed. In Terminal, from this project folder, run: .venv/bin/python -m pip install rembg"
        ) from exc
    result = remove(png_bytes(image.convert("RGBA")))
    if isinstance(result, Image.Image):
        cut = result.convert("RGBA")
    else:
        cut = Image.open(io.BytesIO(result)).convert("RGBA")
    if transparent_fraction(cut) < 0.02:
        raise RuntimeError("The local cutout did not find a backdrop. Use a PNG that already has transparency, or a mask.")
    return cut
