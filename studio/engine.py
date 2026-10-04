from __future__ import annotations

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from PIL import Image
from PySide6.QtCore import QObject, Signal

from .config import atomic_write
from .imaging import (
    center_protect,
    composite_product,
    data_url,
    encode_reference,
    is_cutout,
    kept_fraction,
    legal_size,
    load_rgba,
    locked_output_size,
    openai_mask_bytes,
    png_bytes,
    protect_from_alpha,
    protect_from_user_mask,
    resize_exact,
    restore_pixels,
    soften_protect,
    safe_slug,
)
from .pricing import cost_from_image_usage, cost_from_text_usage, estimate_image_call, estimate_screen_call

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "accepted": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "correction": {"type": "string"},
    },
    "required": ["accepted", "issues", "correction"],
    "additionalProperties": False,
}


class Stopped(Exception):
    pass


class CapReached(Exception):
    pass


@dataclass(frozen=True)
class ProductSpec:
    id: str
    name: str
    notes: str
    mode: str
    scale: float
    image_paths: tuple[str, ...]
    mask_path: str | None
    cutout_path: str | None


@dataclass(frozen=True)
class RunContext:
    brief: str
    requirements: str
    criteria: str
    approved_paths: tuple[str, ...]
    model: str
    quality: str
    size: str
    background: str
    output_dir: str
    screen: bool
    screen_model: str
    retries: int
    parallel: int
    candidates: int
    run_id: str


@dataclass
class Job:
    candidate_id: str
    kind: str
    workspace: str
    output_dir: str
    product_name: str
    index: int
    attempt: int
    source_id: str
    correction: str
    change: str
    parent_path: str
    scene_path: str
    reserve: float
    announce: bool
    context: RunContext | None
    product: ProductSpec | None


def build_prompt(
    *,
    mode: str,
    brief: str,
    notes: str,
    requirements: str,
    correction: str,
    change: str,
    has_product: bool,
    has_approved: bool,
    n_product: int = 0,
    n_approved: int = 0,
    anchor: bool = False,
) -> str:
    parts: list[str] = []
    editing = bool(change.strip())
    if editing:
        parts.append("Edit the first attached image. This is one edit of that image. It is not a continuing session.")
        parts.append("Do not redesign anything that the change below does not mention.")
    else:
        parts.append("This is a standalone request. Use only this brief and the images attached to this request.")
        parts.append("Do not continue, vary, or evolve an earlier generation.")
    if mode == "lock":
        parts.append(
            "Rerender the scene around the product. Do not redraw, relabel, recolor, or move the product. "
            "The original product pixels will be put back after this request."
        )
        if editing:
            parts.append(
                "The first image is the frame to edit. Images after it are the original product photographs, "
                "followed by the approved example when one is attached. Do not switch to a different angle or package."
            )
        else:
            parts.append(
                "The first image is the frame to edit. Later product photographs are other views for accuracy only. "
                "Do not switch to a different angle or a different package."
            )
            if n_product and n_approved:
                parts.append(
                    f"The first {n_product} attached image(s) are product photographs. "
                    f"The next {n_approved} are the approved example for lighting, framing, and background. "
                    "If they conflict, keep the product from the product photographs."
                )
    elif mode == "composite":
        parts.append(
            "Create the surrounding scene only. Do not draw a product, package, bottle, box, label, or logo. "
            "Leave the lower center of the frame uncluttered."
        )
        if editing and n_approved:
            parts.append(
                "The first image is the scene to edit. The later attached images are the approved example "
                "for lighting, framing, and background. Do not copy a product out of them."
            )
        elif n_approved:
            parts.append(
                "The attached images are the approved example for lighting, framing, and background. "
                "Match that lighting and setting. Do not copy a product out of them."
            )
    elif mode == "inset":
        parts.append(
            "The scene already exists and must stay as it is. Do not draw a new scene or a new product."
        )
    elif mode == "stage":
        if editing:
            parts.append(
                "The first image is the picture to edit. Change only what the change text asks for. "
                "Keep the same product, count, scale, and placement unless that text says otherwise."
            )
            parts.append(
                "Do not redesign the scene outside the open center. Those pixels will be put back after this request. "
                "The later photographs are anatomy references. Do not replace the product with a different one drawn from them."
            )
        elif anchor:
            parts.append(
                "The first image is the scene to keep. Draw the product in the open center only. "
                "Do not redesign the scene outside that center. Those pixels will be put back after this request."
            )
            parts.append(
                "The last attached image is the earlier result to stay close to. "
                "The photographs between the scene and that result are anatomy references only."
            )
        else:
            parts.append(
                "The first image is the scene to keep. Draw the product in the open center only. "
                "Do not redesign the scene outside that center. Those pixels will be put back after this request."
            )
            parts.append(
                "The later attached images are photographs for appearance. Create a new product that resembles them. "
                "Do not paste, trace, or copy those photographs into the scene."
            )
    elif has_product and has_approved:
        parts.append(
            "Product photographs define the product's shape, color, label, and geometry. "
            "Approved example images define lighting, framing, and background. "
            "If they conflict, keep the product accurate."
        )
    elif has_product:
        parts.append("The attached photographs define the product. Keep its shape, color, label, and geometry accurate.")
    elif has_approved:
        parts.append("The attached images are the approved example. Match their lighting, framing, background, and subject.")
    if brief.strip():
        parts.append(brief.strip())
    if notes.strip():
        parts.append("Instructions for this product only:\n" + notes.strip())
    if requirements.strip():
        parts.append(
            "The picture must meet these constraints. Do not draw this text in the image:\n" + requirements.strip()
        )
    if correction.strip():
        parts.append(
            "Earlier results from this same brief were rejected for the reasons below. "
            "Fix every reason. Keep every other part of the brief unchanged. "
            "The rejected images are not attached:\n" + correction.strip()
        )
    if editing:
        parts.append("Change to apply:\n" + change.strip())
    if anchor and not editing:
        parts.append(
            "The last attached image is an earlier result. Stay close to it: same subject, count, scale, placement, and lighting. "
            "Do not invent a different arrangement. Do not copy a reference photograph over that result."
        )
    return "\n\n".join(parts)


def _screen_text(
    criteria: str,
    brief: str,
    notes: str,
    requirements: str,
    n_product: int,
    n_approved: int,
    mode: str = "",
) -> str:
    lines = ["Judge the candidate image.", "Image 1 is the candidate."]
    next_index = 2
    if n_product:
        end = next_index + n_product - 1
        if mode == "stage":
            if n_product == 1:
                lines.append(f"Image {next_index} is an appearance photograph. The product should resemble it. It is not a file to paste.")
            else:
                lines.append(
                    f"Images {next_index} through {end} are appearance photographs. The product should resemble them. They are not files to paste."
                )
        elif n_product == 1:
            lines.append(f"Image {next_index} is a photograph of the real product.")
        else:
            lines.append(f"Images {next_index} through {end} are photographs of the real product.")
        next_index = end + 1
    if n_approved:
        end = next_index + n_approved - 1
        if mode == "stage":
            if n_approved == 1:
                lines.append(f"Image {next_index} is the original scene. The scene outside the center should still match it.")
            else:
                lines.append(f"Images {next_index} through {end} include the original scene. The scene outside the center should still match it.")
        elif n_approved == 1:
            lines.append(f"Image {next_index} is the approved example for lighting, framing, and background.")
        else:
            lines.append(
                f"Images {next_index} through {end} are the approved example for lighting, framing, and background."
            )
    if not n_product:
        lines.append(
            "No product photograph is attached. Do not apply product-identity, label, or packaging criteria. "
            "Judge the candidate against the brief and any approved example."
        )
    lines.append("Brief:")
    lines.append(brief.strip() or "(no brief)")
    if notes.strip():
        lines.append("Product instructions:")
        lines.append(notes.strip())
    if requirements.strip():
        lines.append("Requirements that were sent to the image model:")
        lines.append(requirements.strip())
    lines.append("Criteria:")
    lines.append(criteria.strip())
    lines.append("Report only problems that are visible.")
    if mode == "stage" and n_product:
        lines.append(
            "The product in the center was drawn to resemble the photographs. Do not reject it only because it is not a copy of a photograph."
        )
        lines.append("Reject it when the scene outside the center changed, or when the product misses a resemblance the criteria name.")
    elif n_product:
        lines.append(
            "If a label in a product photograph is readable and the candidate label is missing, wrong, or unreadable, reject the candidate."
        )
        lines.append("If you are unsure whether the product changed, reject the candidate and name the detail you could not confirm.")
        lines.append("Product photographs win over the approved example when the product itself differs.")
    lines.append("If you accept the candidate, return an empty issues list and an empty correction.")
    lines.append(
        "The correction must say what to change in concrete visual terms, so a new image can be made without seeing the rejected one."
    )
    lines.append("Do not ask for a new style or a new idea.")
    return "\n".join(lines)


_RETRY_AFTER = re.compile(
    r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|mins|minutes)?",
    re.IGNORECASE,
)
_LIMIT_NUMS = re.compile(
    r"Limit\s+([0-9][0-9,]*)\s*,\s*Used\s+([0-9][0-9,]*)\s*,\s*Requested\s+([0-9][0-9,]*)",
    re.IGNORECASE,
)
_LIMIT_KIND = re.compile(r"\bon\s+([^:]{1,80}):", re.IGNORECASE)


def _seconds_from_text(text: str) -> float | None:
    match = _RETRY_AFTER.search(text or "")
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    if unit.startswith("ms") or unit.startswith("mill"):
        value /= 1000.0
    elif unit.startswith("m"):
        value *= 60.0
    return value


def _rate_limit_facts(exc: RateLimitError) -> tuple[str, float, bool]:
    chunks = [str(exc)]
    response = getattr(exc, "response", None)
    if response is not None:
        header = str(response.headers.get("retry-after") or "").strip()
        if header.replace(".", "", 1).isdigit():
            chunks.append(f"try again in {header}s")
    text = " ".join(chunks)
    delay = _seconds_from_text(text)
    if delay is None:
        delay = 60.0
    delay = min(90.0, max(1.0, delay + 1.0))
    detail = "OpenAI refused this request because the account limit for this minute is already used."
    too_big = False
    nums = _LIMIT_NUMS.search(text)
    if nums:
        limit = int(nums.group(1).replace(",", ""))
        used = int(nums.group(2).replace(",", ""))
        requested = int(nums.group(3).replace(",", ""))
        kind = "units"
        kind_match = _LIMIT_KIND.search(text)
        if kind_match:
            kind = " ".join(kind_match.group(1).split())
        detail = (
            f"OpenAI refused this request. The account allows {limit:,} {kind}, "
            f"{used:,} are already used, and this request needs {requested:,}."
        )
        if requested > limit:
            too_big = True
            detail += " This one request is larger than that limit, so waiting will not make it fit."
    return detail, delay, too_big


def friendly(exc: Exception) -> str:
    if isinstance(exc, APIStatusError):
        detail = getattr(exc, "message", "") or str(exc)
        request_id = getattr(exc, "request_id", None)
        suffix = f" Request {request_id}." if request_id else ""
        if exc.status_code == 401:
            return "The API key was rejected. Save a current key and try again."
        return f"OpenAI returned {exc.status_code}: {detail}.{suffix}"
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _fatal(exc: Exception) -> bool:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return True
    text = str(exc).lower()
    return "insufficient_quota" in text or "billing_hard_limit" in text


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _combine_corrections(prior: str, latest: str) -> str:
    prior = prior.strip()
    latest = latest.strip()
    if not prior:
        return latest[:2000]
    if not latest or latest in prior:
        return prior[:2000]
    combined = f"{prior}\n{latest}"
    if len(combined) <= 2000:
        return combined
    return combined[-2000:]


def _prompt_counts(meta: dict) -> tuple[int, int]:
    approved = len(meta.get("approved_files") or [])
    if meta.get("mode") == "composite":
        return 0, approved
    return len(meta.get("product_files") or []), approved


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank_record(job: Job) -> dict:
    return {
        "id": job.candidate_id,
        "kind": job.kind,
        "status": "generating",
        "product_name": job.product_name,
        "index": job.index,
        "attempt": job.attempt,
        "source_id": job.source_id,
        "path": "",
        "scene_path": "",
        "workspace": job.workspace,
        "output_dir": job.output_dir,
        "prompt": "",
        "issues": [],
        "instruction": job.correction or job.change,
        "error": "",
        "notes": "",
        "cost": 0.0,
        "approved_path": "",
        "model": "",
        "created": _now(),
    }


class Engine(QObject):
    log = Signal(str)
    candidate = Signal(dict)
    spend = Signal(float, float)
    busy = Signal(bool)
    idle = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.key_getter = lambda: ""
        self.cancel = threading.Event()
        self.state_lock = threading.Lock()
        self.prep_lock = threading.Lock()
        self.client_lock = threading.Lock()
        self.money_lock = threading.Lock()
        self.pool: ThreadPoolExecutor | None = None
        self.pool_size = 0
        self.parallel = 2
        self.inflight = 0
        self.starting = False
        self.spent = 0.0
        self.reserved = 0.0
        self.spend_cap = 10.0
        self._client: OpenAI | None = None
        self._key = ""
        self.decided: set[str] = set()
        self.cap_logged = False
        self.anchors: dict[str, str] = {}
        self.pending_followers: list[Job] = []

    def set_cap(self, value: float) -> None:
        with self.money_lock:
            self.spend_cap = max(0.0, float(value))

    def note_decision(self, candidate_id: str) -> None:
        if candidate_id:
            with self.state_lock:
                self.decided.add(candidate_id)

    def _human_decided(self, candidate_id: str) -> bool:
        with self.state_lock:
            return candidate_id in self.decided

    def is_busy(self) -> bool:
        with self.state_lock:
            return self.inflight > 0

    def drop_client(self) -> None:
        with self.client_lock:
            self._client = None
            self._key = ""

    def stop(self) -> None:
        self.cancel.set()
        self.log.emit(
            "Stopping. A request that has already reached OpenAI will finish and be saved. No further requests will be sent."
        )

    def shutdown(self, wait: bool) -> None:
        self.cancel.set()
        with self.state_lock:
            pool = self.pool
            self.pool = None
        if pool is not None:
            pool.shutdown(wait=wait, cancel_futures=True)

    def start_batch(self, context: RunContext, products: list[ProductSpec]) -> bool:
        with self.state_lock:
            if self.inflight:
                self.log.emit("A run is already in progress.")
                return False
            self.cancel.clear()
            self.starting = True
            self.spent = 0.0
            self.reserved = 0.0
            self.cap_logged = False
            self.parallel = max(1, min(8, int(context.parallel)))
        self.spend.emit(0.0, 0.0)
        self._reset_pool()
        output = Path(context.output_dir)
        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._clear_starting()
            raise RuntimeError(f"Could not create the output folder. {exc}") from exc
        leaders: list[Job] = []
        followers: list[Job] = []
        pasted = [product.name for product in products if product.mode == "inset"]
        for product in products:
            workspace = output / "_workspace" / f"{context.run_id}_{product.id}"
            copies = 1 if product.mode == "inset" else max(1, int(context.candidates))
            for index in range(copies):
                job = self._make_fresh_job(context, product, workspace, index)
                if index == 0 or product.mode == "inset":
                    leaders.append(job)
                else:
                    followers.append(job)
        jobs = leaders + followers
        if not jobs:
            self._clear_starting()
            self.log.emit("Nothing to generate.")
            return False
        self.log.emit(
            f"Starting {len(products)} product run(s), {len(jobs)} candidate(s). "
            f"Model {context.model}. Quality {context.quality}. Size {context.size}."
        )
        if pasted and int(context.candidates) > 1:
            self.log.emit(
                "Use my scene, paste product saves one picture per product. "
                "Extra candidates would be the same paste, so they are not created."
            )
        self.busy.emit(True)
        with self.state_lock:
            self.pending_followers = followers
        try:
            for job in leaders:
                self._submit(job)
        finally:
            self._clear_starting()
        return True

    def request_another(self, parent: dict) -> None:
        self._request_from_parent(parent, kind="another", change="", correction="")

    def request_change(self, parent: dict, change: str) -> None:
        text = change.strip()
        if not text:
            self.log.emit("Write the change first.")
            return
        self._request_from_parent(parent, kind="change", change=text, correction="")

    def _request_from_parent(self, parent: dict, *, kind: str, change: str, correction: str) -> None:
        with self.state_lock:
            if self.inflight and self.cancel.is_set():
                self.log.emit("The run is stopping. Wait, then try this image again.")
                return
            if self.inflight == 0:
                self.cancel.clear()
        workspace = str(parent.get("workspace") or "")
        if not workspace or not (Path(workspace) / "meta.json").exists():
            self.log.emit("The saved references for this image are missing, so it cannot be requested again.")
            return
        try:
            meta = json.loads((Path(workspace) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.log.emit("The saved references for this image could not be read.")
            return
        if not isinstance(meta, dict):
            self.log.emit("The saved references for this image could not be read.")
            return
        if kind == "another":
            parent_file = str(parent.get("path") or "")
            if parent_file and Path(parent_file).is_file():
                self._remember_anchor(self._anchor_key_from_workspace(workspace), parent_file, force=True)
        if str(meta.get("mode") or "") == "inset" and kind == "change":
            self.log.emit(
                "This picture is a paste onto your scene. Change Height and generate again. "
                "A text change cannot move the product."
            )
            return
        if kind == "change" and str(meta.get("mode") or "") == "composite":
            if not Path(str(parent.get("scene_path") or "")).is_file():
                self.log.emit("The scene file is missing, so the product cannot be pasted again.")
                return
        elif kind == "change" and not Path(str(parent.get("path") or "")).is_file():
            self.log.emit("This image has no file to edit.")
            return
        uploads = meta.get("uploads") or []
        if not isinstance(uploads, list):
            uploads = []
        extra = 1 if kind == "change" else 0
        if str(meta.get("mode") or "") == "inset":
            reserve = 0.0
        else:
            reserve = estimate_image_call(
                str(meta.get("quality") or "high"),
                str(meta.get("size") or "1024x1024"),
                len(uploads) + extra,
            )
        job = Job(
            candidate_id=_new_id(),
            kind=kind,
            workspace=workspace,
            output_dir=str(parent.get("output_dir") or meta.get("output_dir") or ""),
            product_name=str(parent.get("product_name") or meta.get("product_name") or "Image"),
            index=int(parent.get("index") or 0),
            attempt=int(parent.get("attempt") or 0) + 1,
            source_id=str(parent.get("id") or ""),
            correction=correction,
            change=change,
            parent_path=str(parent.get("path") or ""),
            scene_path=str(parent.get("scene_path") or ""),
            reserve=reserve,
            announce=True,
            context=None,
            product=None,
        )
        self.busy.emit(True)
        self._submit(job)

    def _make_fresh_job(self, context: RunContext, product: ProductSpec, workspace: Path, index: int) -> Job:
        if product.mode == "composite":
            images = len(context.approved_paths)
        elif product.mode == "inset":
            images = 0
        elif product.mode == "stage":
            images = 1 + len(product.image_paths)
        else:
            images = len(product.image_paths) + len(context.approved_paths)
        size = context.size
        if product.mode == "inset" and context.approved_paths:
            try:
                scene = load_rgba(Path(context.approved_paths[0]))
                size = f"{scene.width}x{scene.height}"
            except Exception:
                size = context.size
        if product.mode == "stage" and context.approved_paths:
            try:
                size = locked_output_size(Path(context.approved_paths[0]))
            except Exception:
                size = context.size
        if product.mode == "lock" and product.image_paths:
            try:
                size = locked_output_size(Path(product.image_paths[0]))
            except Exception:
                size = context.size
        reserve = 0.0 if product.mode == "inset" else estimate_image_call(context.quality, size, min(16, images))
        return Job(
            candidate_id=_new_id(),
            kind="fresh",
            workspace=str(workspace),
            output_dir=context.output_dir,
            product_name=product.name,
            index=index,
            attempt=0,
            source_id="",
            correction="",
            change="",
            parent_path="",
            scene_path="",
            reserve=reserve,
            announce=False,
            context=context,
            product=product,
        )

    def _clear_starting(self) -> None:
        with self.state_lock:
            self.starting = False
            idle = self.inflight == 0
        if idle:
            self.busy.emit(False)
            self.idle.emit()

    def _reset_pool(self) -> None:
        with self.state_lock:
            if self.inflight:
                return
            pool = self.pool
            self.pool = None
            self.pool_size = 0
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def _pool(self) -> ThreadPoolExecutor:
        with self.state_lock:
            workers = max(1, min(8, int(self.parallel)))
            if self.pool is not None and (self.pool_size == workers or self.inflight):
                return self.pool
            old = self.pool
            self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="image")
            self.pool_size = workers
        if old is not None:
            old.shutdown(wait=False, cancel_futures=True)
        with self.state_lock:
            if self.pool is None:
                self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="image")
                self.pool_size = workers
            return self.pool

    def _submit(self, job: Job) -> None:
        pool = self._pool()
        with self.state_lock:
            self.inflight += 1
        if job.announce:
            self._emit(_blank_record(job))
        pool.submit(self._execute, job)

    def _anchor_key(self, job: Job) -> str:
        if job.product is not None and job.product.id:
            return job.product.id
        return self._anchor_key_from_workspace(job.workspace)

    def _anchor_key_from_workspace(self, workspace: str) -> str:
        name = Path(workspace).name
        if "_" in name:
            return name.split("_", 1)[1]
        return workspace or name

    def _remember_anchor(self, key: str, path: str, *, force: bool = False) -> None:
        if not key or not path:
            return
        with self.state_lock:
            current = self.anchors.get(key, "")
            if force or not current or not Path(current).is_file():
                self.anchors[key] = path

    def _note_saved(self, job: Job, dest: Path) -> None:
        self._remember_anchor(self._anchor_key(job), str(dest), force=job.kind == "change")

    def _anchor_for(self, job: Job) -> str:
        if job.kind == "change":
            return ""
        if job.kind == "another":
            path = job.parent_path
            return path if path and Path(path).is_file() else ""
        key = self._anchor_key(job)
        with self.state_lock:
            current = self.anchors.get(key, "")
        if not current or not Path(current).is_file():
            return ""
        if job.kind == "retry" and job.parent_path:
            try:
                if Path(current).resolve() == Path(job.parent_path).resolve():
                    return ""
            except OSError:
                return ""
        return current

    def _with_anchor(self, uploads: list[tuple[str, bytes, str]], anchor_path: str) -> list[tuple[str, bytes, str]]:
        data = Path(anchor_path).read_bytes()
        combined = [*uploads, ("anchor.png", data, "image/png")]
        if len(combined) <= 16:
            return combined
        return [combined[0], *combined[1:15], combined[-1]]

    def _release_followers(self, job: Job) -> None:
        key = self._anchor_key(job)
        with self.state_lock:
            if self.cancel.is_set():
                self.pending_followers = []
                return
            ready = [item for item in self.pending_followers if self._anchor_key(item) == key]
            self.pending_followers = [item for item in self.pending_followers if self._anchor_key(item) != key]
        for follower in ready:
            self._submit(follower)

    def _finish(self) -> None:
        with self.state_lock:
            self.inflight = max(0, self.inflight - 1)
            idle = self.inflight == 0 and not self.starting
        if idle:
            self.busy.emit(False)
            self.idle.emit()

    def _emit(self, record: dict) -> None:
        payload = dict(record)
        payload["issues"] = list(record.get("issues") or [])
        self.candidate.emit(payload)

    def _emit_spend(self) -> None:
        with self.money_lock:
            spent = self.spent
            reserved = self.reserved
        self.spend.emit(spent, reserved)

    def _try_reserve(self, amount: float) -> bool:
        with self.money_lock:
            if self.spent + self.reserved + amount > self.spend_cap + 1e-9:
                return False
            self.reserved += amount
        self._emit_spend()
        return True

    def _reconcile(self, reserved: float, actual: float) -> None:
        with self.money_lock:
            self.reserved = max(0.0, self.reserved - reserved)
            self.spent += max(0.0, actual)
        self._emit_spend()

    def _openai(self) -> OpenAI:
        key = self.key_getter().strip()
        if not key:
            raise RuntimeError("Save an API key first.")
        with self.client_lock:
            if self._client is None or self._key != key:
                self._client = OpenAI(api_key=key, timeout=300.0, max_retries=0)
                self._key = key
            return self._client

    def _sleep(self, seconds: float) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            if self.cancel.is_set():
                return False
            time.sleep(0.25)
        return True

    def _call(self, fn):
        delay = 2.0
        for attempt in range(5):
            if self.cancel.is_set() and attempt > 0:
                raise Stopped()
            try:
                return fn()
            except RateLimitError as exc:
                detail, wait, too_big = _rate_limit_facts(exc)
                if too_big or attempt == 4:
                    self.log.emit(detail)
                    raise
                self.log.emit(f"{detail} Waiting {wait:.0f}s, then sending this same request again.")
                if not self._sleep(wait):
                    raise Stopped() from exc
            except (APITimeoutError, APIConnectionError, InternalServerError) as exc:
                if attempt == 4:
                    raise
                self.log.emit(f"The API was busy ({exc.__class__.__name__}). Waiting {delay:.0f}s, then sending this same request again.")
                if not self._sleep(delay):
                    raise Stopped() from exc
                delay = min(delay * 2, 40)
        raise RuntimeError("The request failed.")

    def _execute(self, job: Job) -> None:
        record = _blank_record(job)
        held = False
        try:
            if self.cancel.is_set():
                if job.announce:
                    record["status"] = "cancelled"
                    record["error"] = "Stopped before the request was sent."
                    self._emit(record)
                return
            if not self._try_reserve(job.reserve):
                self._cap_block(job, record)
                return
            held = True
            if self.cancel.is_set():
                raise Stopped()
            meta = self._ensure_workspace(job)
            n_product, n_approved = _prompt_counts(meta)
            uploads, mask = self._job_files(job, meta)
            anchor_path = "" if str(meta.get("mode") or "") == "inset" else self._anchor_for(job)
            if anchor_path:
                uploads = self._with_anchor(uploads, anchor_path)
                self.log.emit(
                    f"{job.product_name}: matching the earlier result {Path(anchor_path).name} "
                    "so this image stays close to it."
                )
            prompt = build_prompt(
                mode=str(meta["mode"]),
                brief=str(meta.get("brief") or ""),
                notes=str(meta.get("notes_text") or ""),
                requirements=str(meta.get("requirements") or ""),
                correction="" if job.kind == "change" else job.correction,
                change=job.change,
                has_product=bool(meta.get("product_files")) and meta["mode"] == "reference",
                has_approved=bool(meta.get("approved_files")),
                n_product=n_product,
                n_approved=n_approved,
                anchor=bool(anchor_path),
            )
            record["prompt"] = prompt
            record["model"] = str(meta.get("model") or "")
            record["mode"] = str(meta.get("mode") or "")
            record["notes"] = "\n".join(meta.get("warnings") or [])
            record["output_dir"] = str(meta.get("output_dir") or job.output_dir)
            if str(meta.get("mode") or "") == "inset":
                self._reconcile(job.reserve, 0.0)
                held = False
                record["cost"] = 0.0
                record["prompt"] = (
                    "The image model was not called. The cutout was pasted onto the first reference image. "
                    f"Height {float(meta.get('scale') or 0.62):.2f} of that scene."
                )
                try:
                    image = self._paste_supplied_scene(Path(job.workspace), meta)
                except Exception as exc:
                    record["status"] = "error"
                    record["error"] = friendly(exc)
                    self._emit(record)
                    self.log.emit(f"{job.product_name}: {record['error']}")
                    return
                dest = self._save_visible(job, meta, image)
                record["path"] = str(dest)
                will_screen = bool(meta.get("screen")) and bool(str(meta.get("criteria") or "").strip())
                record["status"] = "reviewing" if will_screen else "unreviewed"
                self._emit(record)
                self.log.emit(f"Pasted {dest.name} for {record['product_name']} onto your scene.")
                if will_screen and not self.cancel.is_set():
                    self._screen_and_maybe_retry(job, meta, record)
                elif self.cancel.is_set() and record["status"] == "reviewing":
                    record["status"] = "unreviewed"
                    record["error"] = "Saved. Stopped before review."
                    self._emit(record)
                return
            raw, actual, request_id = self._generate(meta, prompt, uploads, mask)
            self._reconcile(job.reserve, job.reserve if actual is None else actual)
            held = False
            record["cost"] = job.reserve if actual is None else actual
            if actual is None:
                self.log.emit("The API did not return token counts. The pre-call allowance was counted instead.")
            try:
                image, scene_path = self._postprocess(job, meta, raw)
            except Exception as exc:
                dest = self._save_bytes(job, meta, raw)
                self._note_saved(job, dest)
                record["path"] = str(dest)
                record["status"] = "unreviewed"
                record["error"] = f"Saved the generated image. The product could not be placed back onto it. {friendly(exc)}"
                self._emit(record)
                self.log.emit(record["error"])
                return
            dest = self._save_visible(job, meta, image)
            self._note_saved(job, dest)
            record["path"] = str(dest)
            record["scene_path"] = str(scene_path or "")
            will_screen = bool(meta.get("screen")) and bool(str(meta.get("criteria") or "").strip())
            record["status"] = "reviewing" if will_screen else "unreviewed"
            self._emit(record)
            suffix = f" Request {request_id}." if request_id else ""
            self.log.emit(f"Saved {dest.name} for {record['product_name']}.{suffix}")
            if will_screen and not self.cancel.is_set():
                self._screen_and_maybe_retry(job, meta, record)
            elif self.cancel.is_set() and record["status"] == "reviewing":
                record["status"] = "unreviewed"
                record["error"] = "Saved. Stopped before review."
                self._emit(record)
        except Stopped:
            if held:
                self._reconcile(job.reserve, 0.0)
            if job.announce or record.get("path"):
                record["status"] = "cancelled"
                record["error"] = "Stopped."
                self._emit(record)
        except Exception as exc:
            if held:
                self._reconcile(job.reserve, 0.0)
            if _fatal(exc):
                self.cancel.set()
                self.log.emit("Stopping the run because the account rejected the request.")
            record["status"] = "error"
            record["error"] = friendly(exc)
            self._emit(record)
            self.log.emit(f"{job.product_name}: {record['error']}")
        finally:
            if job.kind == "fresh" and job.index == 0:
                self._release_followers(job)
            self._finish()

    def _cap_block(self, job: Job, record: dict) -> None:
        message = "The spend cap was reached before this image was requested."
        with self.state_lock:
            first = not self.cap_logged
            self.cap_logged = True
        if job.announce:
            record["status"] = "cap"
            record["error"] = message
            self._emit(record)
            self.log.emit(message)
            return
        if first:
            self.log.emit("Spend cap reached. No further images in this run will be requested.")

    def _ensure_workspace(self, job: Job) -> dict:
        with self.prep_lock:
            meta_path = Path(job.workspace) / "meta.json"
            if meta_path.exists():
                return json.loads(meta_path.read_text(encoding="utf-8"))
            if job.product is None or job.context is None:
                raise RuntimeError("The saved references for this image are missing, so it cannot be requested again.")
            meta = self._build_workspace(job)
            for warning in meta.get("warnings") or []:
                self.log.emit(str(warning))
            return meta

    def _build_workspace(self, job: Job) -> dict:
        product = job.product
        context = job.context
        if product is None or context is None:
            raise RuntimeError("The saved references for this image are missing, so it cannot be requested again.")
        workspace = Path(job.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        mode = product.mode
        size = context.size
        uploads: list[str] = []
        product_files: list[str] = []
        approved_files: list[str] = []
        mask_name = None
        restore_name = None
        protect_name = None
        cutout_name = None
        if mode == "lock":
            if not product.image_paths:
                raise RuntimeError(f"{product.name}: add the product photograph before locking pixels.")
            base = load_rgba(Path(product.image_paths[0]))
            src_w, src_h = base.size
            width, height = legal_size(src_w, src_h)
            if (width, height) != (src_w, src_h):
                warnings.append(
                    f"{product.name}: the photograph was resized from {src_w}×{src_h} to {width}×{height} "
                    "so the API would accept it. Locked pixels are that resized photograph, not a redraw."
                )
            base = resize_exact(base, width, height)
            if product.mask_path:
                protect = protect_from_user_mask(load_rgba(Path(product.mask_path)), (width, height))
            elif product.cutout_path:
                protect = protect_from_alpha(load_rgba(Path(product.cutout_path)), (width, height))
            elif is_cutout(base):
                raise RuntimeError(
                    f"{product.name}: this file is a cutout. Use New scene, paste product. "
                    "Lock mode keeps a product inside its original frame."
                )
            else:
                raise RuntimeError(
                    f"{product.name}: create a cutout or choose a mask before locking pixels. "
                    "The program will not guess which pixels to keep."
                )
            fraction = kept_fraction(protect)
            if fraction < 0.001:
                raise RuntimeError(f"{product.name}: the kept area is almost empty.")
            if fraction > 0.98:
                raise RuntimeError(f"{product.name}: the kept area is almost the whole frame, so there is no scene to rerender.")
            size = f"{width}x{height}"
            restore_name = "restore.png"
            protect_name = "protect.png"
            mask_name = "mask.png"
            (workspace / restore_name).write_bytes(png_bytes(base))
            (workspace / protect_name).write_bytes(png_bytes(protect))
            (workspace / mask_name).write_bytes(openai_mask_bytes(protect))
            uploads.append(self._store_bytes(workspace, "upload_00", png_bytes(base), ".png"))
            product_files.append(uploads[0])
            for path in product.image_paths[1:]:
                product_files.append(self._store_reference(workspace, f"upload_{len(uploads):02d}", Path(path)))
                uploads.append(product_files[-1])
            for path in context.approved_paths:
                approved_files.append(self._store_reference(workspace, f"upload_{len(uploads):02d}", Path(path)))
                uploads.append(approved_files[-1])
        elif mode == "composite":
            sprite = self._sprite(product)
            cutout_name = "cutout.png"
            (workspace / cutout_name).write_bytes(png_bytes(sprite))
            product_files.append(self._store_bytes(workspace, "product_00", png_bytes(sprite), ".png"))
            for index, path in enumerate(product.image_paths, start=1):
                if product.cutout_path and Path(path).resolve() == Path(product.cutout_path).resolve():
                    continue
                product_files.append(self._store_reference(workspace, f"product_{index:02d}", Path(path)))
            for path in context.approved_paths:
                approved_files.append(self._store_reference(workspace, f"upload_{len(uploads):02d}", Path(path)))
                uploads.append(approved_files[-1])
            warnings.append(
                f"{product.name}: the scene is generated without the product. The cutout is pasted on afterward, so the product pixels are the photograph."
            )
        elif mode == "inset":
            if not context.approved_paths:
                raise RuntimeError(f"{product.name}: drop the scene you already have into Reference images.")
            scene = load_rgba(Path(context.approved_paths[0]))
            sprite = self._sprite(product)
            cutout_name = "cutout.png"
            scene_name = "supplied_scene.png"
            (workspace / cutout_name).write_bytes(png_bytes(sprite))
            (workspace / scene_name).write_bytes(png_bytes(scene))
            product_files.append(cutout_name)
            for index, path in enumerate(product.image_paths, start=1):
                if product.cutout_path and Path(path).resolve() == Path(product.cutout_path).resolve():
                    continue
                product_files.append(self._store_reference(workspace, f"product_{index:02d}", Path(path)))
            approved_files.append(scene_name)
            size = f"{scene.width}x{scene.height}"
            if len(context.approved_paths) > 1:
                warnings.append(f"{product.name}: only the first reference image is used as the scene.")
            warnings.append(
                f"{product.name}: your scene is kept. The cutout is pasted at height {float(product.scale):.2f}. "
                "The image model is not asked to draw the scene or the product."
            )
        elif mode == "stage":
            if not context.approved_paths:
                raise RuntimeError(f"{product.name}: drop the scene you already have into Reference images.")
            if not product.image_paths:
                raise RuntimeError(f"{product.name}: add photographs of the product the model should draw.")
            scene = load_rgba(Path(context.approved_paths[0]))
            src_w, src_h = scene.size
            width, height = legal_size(src_w, src_h)
            if (width, height) != (src_w, src_h):
                warnings.append(
                    f"{product.name}: the scene was resized from {src_w}×{src_h} to {width}×{height} "
                    "so the API would accept it. The pixels put back are that resized scene, not a redraw."
                )
            scene = resize_exact(scene, width, height)
            protect = center_protect(width, height, product.scale)
            size = f"{width}x{height}"
            restore_name = "restore.png"
            protect_name = "protect.png"
            mask_name = "mask.png"
            (workspace / restore_name).write_bytes(png_bytes(scene))
            (workspace / protect_name).write_bytes(png_bytes(protect))
            (workspace / mask_name).write_bytes(openai_mask_bytes(protect))
            scene_upload = self._store_bytes(workspace, "upload_00", png_bytes(scene), ".png")
            uploads.append(scene_upload)
            approved_files.append(scene_upload)
            for path in product.image_paths:
                stored = self._store_reference(workspace, f"upload_{len(uploads):02d}", Path(path))
                product_files.append(stored)
                uploads.append(stored)
            if len(context.approved_paths) > 1:
                warnings.append(f"{product.name}: only the first reference image is used as the scene.")
            if product.mask_path:
                warnings.append(f"{product.name}: the mask on this card is not used. Height sets the open center.")
            warnings.append(
                f"{product.name}: the scene stays outside the center. Height {float(product.scale):.2f} "
                "opens the middle of the scene. The model draws a new product there from the photographs on the card."
            )
        else:
            for path in product.image_paths:
                product_files.append(self._store_reference(workspace, f"upload_{len(uploads):02d}", Path(path)))
                uploads.append(product_files[-1])
            for path in context.approved_paths:
                approved_files.append(self._store_reference(workspace, f"upload_{len(uploads):02d}", Path(path)))
                uploads.append(approved_files[-1])
        if len(uploads) > 16:
            warnings.append(f"{product.name}: only the first 16 images were kept. The API allows 16 input images.")
            uploads = uploads[:16]
            if mode == "composite":
                approved_files = [name for name in approved_files if name in uploads]
            else:
                product_files = [name for name in product_files if name in uploads]
                approved_files = [name for name in approved_files if name in uploads]
        meta = {
            "version": 1,
            "mode": mode,
            "brief": context.brief,
            "notes_text": product.notes,
            "requirements": context.requirements,
            "criteria": context.criteria,
            "model": context.model,
            "quality": context.quality,
            "size": size,
            "background": context.background,
            "scale": product.scale,
            "screen": context.screen,
            "screen_model": context.screen_model,
            "retries": context.retries,
            "product_name": product.name,
            "output_dir": context.output_dir,
            "uploads": uploads,
            "product_files": product_files,
            "approved_files": approved_files,
            "mask": mask_name,
            "restore": restore_name,
            "protect": protect_name,
            "cutout": cutout_name,
            "warnings": warnings,
        }
        atomic_write(workspace / "meta.json", json.dumps(meta, indent=2))
        return meta

    def _paste_supplied_scene(self, workspace: Path, meta: dict) -> Image.Image:
        scene = load_rgba(workspace / "supplied_scene.png")
        cutout = load_rgba(workspace / str(meta.get("cutout") or "cutout.png"))
        return composite_product(scene, cutout, float(meta.get("scale") or 0.62))

    def _sprite(self, product: ProductSpec) -> Image.Image:
        if product.cutout_path:
            image = load_rgba(Path(product.cutout_path))
        elif product.image_paths:
            image = load_rgba(Path(product.image_paths[0]))
        else:
            raise RuntimeError(f"{product.name}: add a product photograph.")
        if not is_cutout(image):
            raise RuntimeError(
                f"{product.name}: create a cutout, or use a PNG that already has transparency, before pasting the product."
            )
        return image

    def _store_reference(self, workspace: Path, stem: str, source: Path) -> str:
        try:
            data, suffix = encode_reference(source)
        except Exception as exc:
            raise RuntimeError(f"Could not read {source.name}. {exc}") from exc
        return self._store_bytes(workspace, stem, data, suffix)

    def _store_bytes(self, workspace: Path, stem: str, data: bytes, suffix: str) -> str:
        name = f"{stem}{suffix}"
        (workspace / name).write_bytes(data)
        return name

    def _read_upload(self, workspace: Path, name: str) -> tuple[str, bytes, str]:
        path = workspace / name
        data = path.read_bytes()
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        else:
            mime = "image/png"
        return (path.name, data, mime)

    def _trim(self, uploads: list[tuple[str, bytes, str]]) -> list[tuple[str, bytes, str]]:
        if len(uploads) <= 16:
            return uploads
        return [uploads[0], *uploads[1:16]]

    def _job_files(self, job: Job, meta: dict) -> tuple[list[tuple[str, bytes, str]], tuple[str, bytes, str] | None]:
        workspace = Path(job.workspace)
        stored = [self._read_upload(workspace, name) for name in meta.get("uploads") or []]
        if job.kind != "change":
            mask = None
            if meta.get("mask"):
                mask = self._read_upload(workspace, str(meta["mask"]))
            return self._trim(stored) if stored else stored, mask
        if meta.get("mode") == "composite":
            if not job.scene_path or not Path(job.scene_path).is_file():
                raise RuntimeError("The scene file is missing, so the product cannot be pasted again.")
            uploads = [("scene.png", Path(job.scene_path).read_bytes(), "image/png"), *stored]
            return self._trim(uploads), None
        if not job.parent_path or not Path(job.parent_path).is_file():
            raise RuntimeError("The image file is missing, so it cannot be edited.")
        current = Path(job.parent_path).read_bytes()
        uploads = [("current.png", current, "image/png"), *stored]
        mask = None
        if meta.get("mode") in {"lock", "stage"} and meta.get("protect"):
            with Image.open(BytesIO(current)) as current_image:
                size = current_image.size
            protect = load_rgba(workspace / str(meta["protect"])).convert("L")
            if protect.size != size:
                protect = protect.resize(size, Image.Resampling.NEAREST)
            mask = ("mask.png", openai_mask_bytes(protect), "image/png")
        return self._trim(uploads), mask

    def _generate(self, meta: dict, prompt: str, uploads: list[tuple[str, bytes, str]], mask: tuple[str, bytes, str] | None) -> tuple[bytes, float | None, str]:
        client = self._openai()
        model = str(meta["model"])
        kwargs = {
            "model": model,
            "prompt": prompt,
            "size": str(meta.get("size") or "1024x1024"),
            "quality": str(meta.get("quality") or "high"),
            "output_format": "png",
            "background": str(meta.get("background") or "opaque"),
            "n": 1,
        }
        if uploads:
            kwargs["image"] = uploads
            if mask is not None:
                kwargs["mask"] = mask
            if str(meta.get("mode") or "") != "stage" and (
                not model.startswith("gpt-image-2") or model.startswith("gpt-image-2.5")
            ):
                kwargs["input_fidelity"] = "high"
            result = self._call(lambda: self._edit_or_drop_fidelity(client, kwargs))
        else:
            result = self._call(lambda: client.images.generate(**kwargs))
        if not result.data or not result.data[0].b64_json:
            raise RuntimeError("OpenAI returned no image.")
        import base64

        raw = base64.b64decode(result.data[0].b64_json)
        request_id = str(getattr(result, "_request_id", "") or getattr(result, "request_id", "") or "")
        return raw, cost_from_image_usage(getattr(result, "usage", None)), request_id

    def _edit_or_drop_fidelity(self, client: OpenAI, kwargs: dict):
        try:
            return client.images.edit(**kwargs)
        except BadRequestError as exc:
            if "input_fidelity" in kwargs and "input_fidelity" in str(exc).lower():
                reduced = dict(kwargs)
                reduced.pop("input_fidelity", None)
                return client.images.edit(**reduced)
            raise

    def _postprocess(self, job: Job, meta: dict, raw: bytes) -> tuple[Image.Image, Path | None]:
        workspace = Path(job.workspace)
        mode = str(meta.get("mode") or "reference")
        if mode in {"lock", "stage"} and meta.get("restore") and meta.get("protect"):
            original = load_rgba(workspace / str(meta["restore"]))
            with Image.open(workspace / str(meta["protect"])) as protect_image:
                protect = protect_image.convert("L")
                protect.load()
            if mode == "stage":
                protect = soften_protect(protect)
            with Image.open(BytesIO(raw)) as generated:
                if job.kind == "change" and generated.size != original.size:
                    protect = protect.resize(generated.size, Image.Resampling.NEAREST)
                    original = original.resize(generated.size, Image.Resampling.LANCZOS)
                output = restore_pixels(generated, original, protect)
            return output, None
        if mode == "composite" and meta.get("cutout"):
            with Image.open(BytesIO(raw)) as generated:
                scene = generated.convert("RGBA")
            scenes = workspace / "scenes"
            scenes.mkdir(parents=True, exist_ok=True)
            scene_path = scenes / f"{job.candidate_id}.png"
            scene_path.write_bytes(png_bytes(scene))
            cutout = load_rgba(workspace / str(meta["cutout"]))
            return composite_product(scene, cutout, float(meta.get("scale") or 0.62)), scene_path
        with Image.open(BytesIO(raw)) as generated:
            return generated.convert("RGBA"), None

    def _save_visible(self, job: Job, meta: dict, image: Image.Image) -> Path:
        folder = Path(str(meta.get("output_dir") or job.output_dir)) / safe_slug(str(meta.get("product_name") or job.product_name))
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{safe_slug(str(meta.get('product_name') or job.product_name))}-{job.candidate_id}.png"
        dest.write_bytes(png_bytes(image))
        return dest

    def _save_bytes(self, job: Job, meta: dict, raw: bytes) -> Path:
        folder = Path(str(meta.get("output_dir") or job.output_dir)) / safe_slug(str(meta.get("product_name") or job.product_name))
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{safe_slug(str(meta.get('product_name') or job.product_name))}-{job.candidate_id}.png"
        dest.write_bytes(raw)
        return dest

    def _screen_and_maybe_retry(self, job: Job, meta: dict, record: dict) -> None:
        try:
            accepted, issues, correction, _screen_cost = self._screen(meta, record)
        except CapReached:
            record["status"] = "unreviewed"
            record["error"] = "Saved. The spend cap was reached before review."
            self._emit(record)
            self.log.emit(record["error"])
            return
        except Stopped:
            record["status"] = "unreviewed"
            record["error"] = "Saved. Stopped before review finished."
            self._emit(record)
            return
        except Exception as exc:
            record["status"] = "unreviewed"
            record["error"] = f"Saved. The reviewer did not finish: {friendly(exc)}"
            self._emit(record)
            self.log.emit(record["error"])
            return
        if accepted:
            if self._human_decided(job.candidate_id):
                self._emit(record)
                self.log.emit(f"You already decided on {record['product_name']}, so the reviewer result was not applied.")
                return
            record["status"] = "passed"
            record["issues"] = []
            record["error"] = ""
            self._emit(record)
            self.log.emit(f"Reviewer passed {record['product_name']}.")
            return
        if self._human_decided(job.candidate_id):
            self._emit(record)
            self.log.emit(f"You already decided on {record['product_name']}, so the reviewer result was not applied.")
            return
        record["status"] = "failed"
        record["issues"] = issues
        record["instruction"] = correction
        record["error"] = ""
        self._emit(record)
        detail = "; ".join(issues) if issues else "the reviewer rejected it"
        self.log.emit(f"Reviewer rejected {record['product_name']}: {detail}")
        if str(meta.get("mode") or "") == "inset":
            self.log.emit(
                f"{record['product_name']}: this picture is a paste, so a rejection does not request another image. "
                "Change Height and generate again."
            )
            return
        retries = int(meta.get("retries") or 0)
        if job.attempt >= retries:
            self.log.emit(f"Retry limit reached for {record['product_name']}.")
            return
        if self.cancel.is_set() or self._human_decided(job.candidate_id):
            return
        self._submit_retry(job, meta, correction, str(record.get("path") or ""))

    def _screen(self, meta: dict, record: dict) -> tuple[bool, list[str], str, float]:
        criteria = str(meta.get("criteria") or "").strip()
        if not criteria:
            return True, [], "", 0.0
        workspace = Path(str(record["workspace"]))
        candidate = Path(str(record["path"]))
        product_files = list(meta.get("product_files") or [])[:4]
        approved_files = list(meta.get("approved_files") or [])[:2]
        if len(meta.get("product_files") or []) > 4 or len(meta.get("approved_files") or []) > 2:
            self.log.emit("The reviewer saw the first 4 product photographs and the first 2 approved examples.")
        paths = [candidate, *[workspace / name for name in product_files], *[workspace / name for name in approved_files]]
        reserve = estimate_screen_call(str(meta.get("screen_model") or "gpt-5.4"), len(paths))
        if not self._try_reserve(reserve):
            raise CapReached()
        try:
            if self.cancel.is_set():
                raise Stopped()
            content: list[dict] = [
                {
                    "type": "input_text",
                    "text": _screen_text(
                        criteria,
                        str(meta.get("brief") or ""),
                        str(meta.get("notes_text") or ""),
                        str(meta.get("requirements") or ""),
                        len(product_files),
                        len(approved_files),
                        str(meta.get("mode") or ""),
                    ),
                }
            ]
            for path in paths:
                content.append({"type": "input_image", "image_url": data_url(path), "detail": "original"})
            model = str(meta.get("screen_model") or "gpt-5.4")
            kwargs = {
                "model": model,
                "reasoning": {"effort": "low"},
                "input": [
                    {
                        "role": "system",
                        "content": "You compare a generated image with reference photographs. You report only visible problems.",
                    },
                    {"role": "user", "content": content},
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "image_screen",
                        "strict": True,
                        "schema": SCREEN_SCHEMA,
                    }
                },
            }
            response = self._call(lambda: self._screen_request(kwargs))
            actual = cost_from_text_usage(getattr(response, "usage", None), model)
            cost = reserve if actual is None else actual
        except Exception:
            self._reconcile(reserve, 0.0)
            raise
        self._reconcile(reserve, cost)
        record["cost"] = float(record.get("cost") or 0) + cost
        text = (getattr(response, "output_text", "") or "").strip()
        if not text:
            raise RuntimeError("The reviewer returned no decision.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("The reviewer returned text that was not valid JSON.") from exc
        issues = [str(item).strip() for item in data.get("issues") or [] if str(item).strip()]
        accepted = bool(data.get("accepted")) and not issues
        correction = str(data.get("correction") or "").strip()
        if not accepted and not correction:
            correction = " ".join(issues) or "Follow the original brief and the reference images more closely."
        return accepted, issues, correction[:2000], cost

    def _screen_request(self, kwargs: dict):
        client = self._openai()
        try:
            return client.responses.create(**kwargs)
        except BadRequestError as exc:
            if "reasoning" in kwargs and "reasoning" in str(exc).lower():
                reduced = dict(kwargs)
                reduced.pop("reasoning", None)
                return client.responses.create(**reduced)
            raise

    def _submit_retry(self, job: Job, meta: dict, correction: str, rejected_path: str) -> None:
        reserve = estimate_image_call(
            str(meta.get("quality") or "high"),
            str(meta.get("size") or "1024x1024"),
            len(meta.get("uploads") or []),
        )
        retry = Job(
            candidate_id=_new_id(),
            kind="retry",
            workspace=job.workspace,
            output_dir=str(meta.get("output_dir") or job.output_dir),
            product_name=str(meta.get("product_name") or job.product_name),
            index=job.index,
            attempt=job.attempt + 1,
            source_id=job.candidate_id,
            correction=_combine_corrections(job.correction, correction),
            change="",
            parent_path=rejected_path,
            scene_path="",
            reserve=reserve,
            announce=True,
            context=None,
            product=None,
        )
        self.log.emit(
            f"Requesting a new image for {retry.product_name} from the original brief and the corrections so far. "
            "The rejected image is not attached."
        )
        self._submit(retry)
