"""AI-generated demo media: photoreal rider POV clips (Gemini Omni Flash), 4K stills and 3D textures (Nano Banana).

Generation is billable, so it only runs when an admin presses Generate in the console, the two-key billable switch is
on, and the per-day media caps allow it. Everything produced is labelled AI-GENERATED and SIMULATED; nothing is
presented as real footage of a real street. Each clip carries a ground-truth label (glass / clear / confounder) so the
on-device detector can be evaluated on it honestly.

Storage: private Supabase bucket `media` (hosted) or `.lk/media` (local). Browsers get short-lived signed URLs; large
files never stream through the API function.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import db
from .config import get_settings
from .hashing import sign, verify_signature
from .models import budget
from .models.google import GoogleClient, ModelError, interaction_status, media_output
from .storage import REPO_ROOT

VIDEO_MODEL = os.environ.get("LK_VIDEO_MODEL", "gemini-omni-1.1-flash")
IMAGE_MODEL = os.environ.get("LK_IMAGE_MODEL", "gemini-nano-banana-2.1")
MAX_BYTES = 50 * 1024 * 1024          # Supabase Free per-file limit
CAPS = {"video": (2, 12), "image": (6, 60), "texture": (6, 60)}   # per minute, per day

_STYLE = ("Photorealistic, natural lens, true-to-life colour and grain, physically plausible light. "
          "Fictional location: no real business names, no readable signs, no logos, no license plates, no identifiable faces. ")
_POV = ("First-person point of view from a cyclist's head-mounted camera riding slowly north along a two-way protected bike lane "
        "painted green, separated from car traffic by low concrete barriers, on a wide Manhattan-style avenue. Hands on handlebars "
        "at the bottom of frame, gloves. Smooth natural head motion, 16:9. ")


@dataclass(frozen=True)
class Asset:
    id: str
    kind: str          # video | image | texture
    use_case: str      # glasses_pov | evidence | twin_texture | hero
    prompt: str
    label: str | None = None
    aspect: str = "16:9"
    resolution: str = "1080p"   # video: 720p | 1080p | 4k ; image: 4K
    est_usd: float = 0.0
    title: str = ""


CATALOG: list[Asset] = [
    # ---- rider POV clips (ground-truth labelled) -------------------------------------------------------------
    Asset("pov-dawn-glass", "video", "glasses_pov", _POV + "Blue-hour dawn, low sun. About 15 metres ahead a scatter of broken "
          "green and clear bottle glass covers half the lane width and glints in the light; the rider gradually slows as the camera "
          "approaches it, then stops a few metres before it. " + _STYLE, "glass", title="Dawn, broken glass ahead", est_usd=1.2),
    Asset("pov-night-glass", "video", "glasses_pov", _POV + "Night, sodium and LED streetlights, dry pavement. Ahead in the lane, "
          "small shards of broken glass catch the streetlight and sparkle. The rider slows and steers gently left. " + _STYLE,
          "glass", title="Night, glass shards", est_usd=1.2),
    Asset("pov-day-clear", "video", "glasses_pov", _POV + "Clear morning, soft daylight, a clean empty lane with no debris at all, "
          "a few parked delivery bikes far ahead outside the lane. Steady riding speed. " + _STYLE,
          "clear", title="Morning, clear lane", est_usd=1.2),
    Asset("pov-wet-reflections", "video", "glasses_pov", _POV + "Just after rain at dusk. Puddles and wet asphalt reflect streetlights "
          "and car headlights as bright specks and streaks; there is no glass or debris anywhere. Steady riding. " + _STYLE,
          "confounder_water", title="Wet reflections (no glass)", est_usd=1.2),
    Asset("pov-worn-markings", "video", "glasses_pov", _POV + "Overcast afternoon. The lane has worn, chipped white bicycle symbols and "
          "chevron markings with small flaking paint fragments; no glass, no debris. Steady riding. " + _STYLE,
          "confounder_markings", title="Worn markings (no glass)", est_usd=1.2),
    Asset("pov-autumn-leaves", "video", "glasses_pov", _POV + "Autumn morning, scattered dry leaves and a few twigs drift across the lane; "
          "no glass. Steady riding. " + _STYLE, "debris_other", title="Leaves and twigs (no glass)", est_usd=1.2),
    # ---- evidence stills (4K) -------------------------------------------------------------------------------------
    Asset("evidence-glass-before", "image", "evidence", "Close documentary photo taken from standing height, angled down at a "
          "green-painted protected bike lane on a city avenue at dawn: broken bottle glass, many sharp shards of green and clear "
          "glass spread across most of the lane width, crisp detail, slight morning dew. " + _STYLE, "glass",
          aspect="4:3", resolution="4K", est_usd=0.24, title="Glass in lane (before)"),
    Asset("evidence-glass-after", "image", "evidence", "Same viewpoint and framing as a documentary photo of a green-painted protected "
          "bike lane at dawn after mechanical sweeping: clean surface, faint wet brush marks, no glass or debris. " + _STYLE, "clear",
          aspect="4:3", resolution="4K", est_usd=0.24, title="Lane after sweep"),
    Asset("evidence-glass-partial", "image", "evidence", "Documentary photo of a green-painted protected bike lane at dawn after a "
          "sweep that missed some debris: most of the lane is clean but a small cluster of broken glass shards remains near the "
          "concrete barrier edge. " + _STYLE, "glass", aspect="4:3", resolution="4K", est_usd=0.24, title="Partially cleared"),
    # ---- 3D textures for the digital twin (seamless, top-down) ----------------------------------------------------
    Asset("tex-asphalt", "texture", "twin_texture", "Seamless tileable texture, orthographic top-down view of weathered city asphalt: "
          "fine aggregate, small cracks, subtle oil stains, even flat lighting, no shadows, no markings, no perspective.",
          aspect="1:1", resolution="4K", est_usd=0.24, title="Asphalt"),
    Asset("tex-bike-lane", "texture", "twin_texture", "Seamless tileable texture, orthographic top-down view of green thermoplastic "
          "bike-lane paint over asphalt, slightly worn with tyre polish and small chips showing dark asphalt, even flat lighting, "
          "no symbols, no shadows, no perspective.", aspect="1:1", resolution="4K", est_usd=0.24, title="Green lane paint"),
    Asset("tex-concrete", "texture", "twin_texture", "Seamless tileable texture, orthographic view of a cast concrete traffic barrier "
          "surface: light grey, fine pores, faint form lines and grime, even flat lighting, no shadows.",
          aspect="1:1", resolution="4K", est_usd=0.24, title="Concrete barrier"),
    Asset("tex-facade", "texture", "twin_texture", "Seamless tileable texture, straight-on orthographic view of a Manhattan-style "
          "pre-war apartment building facade: tan brick, regular rows of rectangular windows with dark glass and white frames, "
          "fire escapes absent, even daylight, no signs, no people.", aspect="1:1", resolution="4K", est_usd=0.24, title="Building facade"),
    Asset("tex-sidewalk", "texture", "twin_texture", "Seamless tileable texture, orthographic top-down view of a grey concrete city "
          "sidewalk with square expansion joints, light wear and gum spots, even flat lighting, no shadows.",
          aspect="1:1", resolution="4K", est_usd=0.24, title="Sidewalk"),
]
BY_ID = {a.id: a for a in CATALOG}


def prompt_hash(a: Asset) -> str:
    return hashlib.sha256(f"{a.kind}|{a.prompt}|{a.aspect}|{a.resolution}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------- storage
class MediaStore:
    def __init__(self) -> None:
        self.url = os.environ.get("SUPABASE_URL")
        self._key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        self.remote = bool(self.url and self._key and self.url.startswith("https://"))
        self.root = Path(os.environ.get("LK_MEDIA_DIR", REPO_ROOT / ".lk" / "media"))

    def __repr__(self) -> str:
        return f"MediaStore(remote={self.remote})"

    def _req(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        h = {"authorization": f"Bearer {self._key}", "apikey": self._key or "", **(headers or {})}
        req = urllib.request.Request(f"{self.url}/storage/v1/{path}", data=data, method=method, headers=h)  # noqa: S310
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310  (https enforced in __init__)
            return r.read()

    def put(self, rel: str, data: bytes, mime: str) -> None:
        if self.remote:
            self._req("POST", f"object/media/{rel}", data, {"content-type": mime, "x-upsert": "true",
                                                              "cache-control": "max-age=86400"})
        else:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def signed_url(self, rel: str, ttl_s: int = 3600) -> str:
        if self.remote:
            out = json.loads(self._req("POST", f"object/sign/media/{rel}", json.dumps({"expiresIn": ttl_s}).encode(),
                                       {"content-type": "application/json"}))
            return f"{self.url}/storage/v1{out['signedURL']}"
        exp = int(time.time()) + ttl_s
        return f"/v1/media/file/{rel}?exp={exp}&sig={sign(get_settings().signing_secret, 'media', rel, exp)}"

    def local_path(self, rel: str, exp: int, sig: str) -> Path | None:
        if exp < time.time() or not verify_signature(get_settings().signing_secret, sig, "media", rel, exp):
            return None
        p = (self.root / rel).resolve()
        return p if self.root.resolve() in p.parents and p.is_file() else None


# ---------------------------------------------------------------------------------------------- state
def _row(asset_id: str) -> dict | None:
    return db.one("select * from media_assets where id = %s", (asset_id,))


def _upsert(a: Asset, **fields) -> None:
    cols = {"kind": a.kind, "use_case": a.use_case, "label": a.label, "cost_estimate_usd": a.est_usd, **fields}
    names = ", ".join(cols)
    db.one(f"""insert into media_assets (id, {names}, updated_at) values (%s, {', '.join(['%s'] * len(cols))}, now())
               on conflict (id) do update set {', '.join(f'{k} = excluded.{k}' for k in cols)}, updated_at = now()
               returning 1""", (a.id, *cols.values()))


def catalog(*, with_urls: bool = True) -> list[dict]:
    rows = {r["id"]: r for r in db.all_rows("select * from media_assets")}
    store = MediaStore() if with_urls else None
    out = []
    for a in CATALOG:
        r = rows.get(a.id) or {}
        status = r.get("status", "missing")
        stale = status == "ready" and r.get("prompt_hash") != prompt_hash(a)
        item = {"id": a.id, "kind": a.kind, "use_case": a.use_case, "label": a.label, "title": a.title,
                "resolution": r.get("resolution") or a.resolution, "status": status, "stale": stale,
                "est_usd": a.est_usd, "bytes": r.get("bytes"), "model": r.get("model"), "error": r.get("error"),
                "generated_at": r.get("generated_at"), "provenance": "AI_GENERATED"}
        if store and status == "ready" and r.get("storage_path"):
            try:
                item["url"] = store.signed_url(r["storage_path"])
            except (urllib.error.URLError, KeyError, ValueError):
                item["url"] = None
        out.append(item)
    return out


# ---------------------------------------------------------------------------------------------- generation
class MediaError(RuntimeError):
    pass


def _client() -> GoogleClient:
    s = get_settings()
    if s.ai_mode != "live" or not s.google_api_key:
        raise MediaError("live AI is off: set GOOGLE_API_KEY and AI_MODE=live")
    if not budget.billable_enabled():
        raise MediaError("billable calls are switched off (env BILLABLE_CALLS_ENABLED or the billable_calls flag)")
    return GoogleClient(s.google_api_key)


def _save(a: Asset, data: bytes, mime: str, model: str, resolution: str) -> dict:
    if len(data) > MAX_BYTES:
        raise MediaError(f"generated file is {len(data) // (1024 * 1024)} MB, over the 50 MB storage limit; "
                         "use 1080p for this clip")
    ext = {"video/mp4": "mp4", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "bin")
    rel = f"{a.use_case}/{a.id}-{prompt_hash(a)}.{ext}"
    MediaStore().put(rel, data, mime)
    _upsert(a, status="ready", model=model, resolution=resolution, prompt_hash=prompt_hash(a), storage_path=rel,
            mime_type=mime, bytes=len(data), interaction_id=None, error=None, generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return {"id": a.id, "status": "ready", "bytes": len(data)}


def generate(asset_id: str, requested_by: str | None, *, resolution: str | None = None,
             client: GoogleClient | None = None) -> dict:
    a = BY_ID.get(asset_id)
    if not a:
        raise MediaError("unknown asset")
    r = _row(a.id)
    if r and r["status"] == "generating":
        return {"id": a.id, "status": "generating", "note": "already generating; poll it"}
    client = client or _client()
    per_min, per_day = CAPS[a.kind]
    try:
        budget.take(f"model:media:{a.kind}", per_min, per_day)
    except budget.BudgetExceeded as e:
        raise MediaError(str(e)) from e
    res = resolution or a.resolution
    try:
        if a.kind == "video":
            fmt = {"type": "video", "aspect_ratio": a.aspect, "resolution": res}
            try:
                out = client.create_media(VIDEO_MODEL, a.prompt, fmt, background=True, timeout=60)
            except ModelError as e:
                if not (e.kind == "http" and e.status == 400 and "background" in str(e).lower()):
                    raise
                out = client.create_media(VIDEO_MODEL, a.prompt, fmt, timeout=280)   # model has no background mode
            got = media_output(out, "video")
            if got:
                return _save(a, *got, VIDEO_MODEL, res)
            iid = out.get("id") or out.get("name", "").split("/")[-1]
            if not iid:
                raise MediaError("video job started but no interaction id returned")
            _upsert(a, status="generating", model=VIDEO_MODEL, resolution=res, interaction_id=iid, error=None,
                    requested_by=requested_by)
            return {"id": a.id, "status": "generating"}
        out = client.create_media(IMAGE_MODEL, a.prompt, {"type": "image", "mime_type": "image/png",
                                                          "aspect_ratio": a.aspect, "image_size": res}, timeout=240)
        got = media_output(out, "image")
        if not got:
            raise MediaError("the image model returned no image")
        return _save(a, *got, IMAGE_MODEL, res)
    except ModelError as e:
        _upsert(a, status="failed", error=f"{e.kind}: {str(e)[:300]}", requested_by=requested_by)
        raise MediaError(f"{e.kind}: {e}") from e


def poll(asset_id: str, *, client: GoogleClient | None = None) -> dict:
    a = BY_ID.get(asset_id)
    r = _row(asset_id) if a else None
    if not a or not r:
        raise MediaError("unknown asset")
    if r["status"] != "generating" or not r["interaction_id"]:
        return {"id": a.id, "status": r["status"]}
    client = client or _client()
    try:
        out = client.get_interaction(r["interaction_id"])
    except ModelError as e:
        return {"id": a.id, "status": "generating", "note": f"poll failed ({e.kind}); try again"}
    st = interaction_status(out)
    if st in ("failed", "cancelled", "error"):
        _upsert(a, status="failed", error=json.dumps(out.get("error") or st)[:300])
        return {"id": a.id, "status": "failed"}
    got = media_output(out, "video")
    if not got:
        return {"id": a.id, "status": "generating", "api_status": st}
    return _save(a, *got, r["model"] or VIDEO_MODEL, r["resolution"] or a.resolution)
