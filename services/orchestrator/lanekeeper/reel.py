"""Official demo reel: joins pre-rendered segments (title cards, captioned console recordings, the twin render and
overlay PNGs, kept in demo-media/reel) with the AI-generated clips and stills from private storage, then
publishes one 1080p MP4 to the public `demo` bucket. No model calls: building the reel costs nothing.

The parts folder holds a manifest (`reel.json`) listing segments. It is validated strictly: names are plain file names,
asset ids must be in the media catalog, and numbers are bounded. Nothing from the manifest is passed to a shell.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from . import db
from .media import BY_ID, MediaError, MediaStore
from .storage import REPO_ROOT

# Pre-rendered parts ship with the code (demo-media/reel, bundled into the Vercel function). A URL can override.
PARTS_DIR = Path(os.environ.get("LK_REEL_PARTS_DIR", REPO_ROOT / "demo-media" / "reel"))
ASSETS_URL = os.environ.get("LK_REEL_ASSETS_URL", "")
PUBLIC_BUCKET = "demo"
REEL_NAME = "lanekeeper-os-demo.mp4"
W, H, FPS = 1920, 1080, 30
# Every part (here and in the pre-rendered release files) uses exactly these settings, so the join is a stream copy.
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-maxrate", "4000k", "-bufsize", "8000k",
          "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "15360"]
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}\.(mp4|png|json)$")
LIMIT = 50 * 1024 * 1024


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _fetch(name: str, dest: Path, fetch=None) -> Path:
    if not _NAME.match(name):
        raise MediaError(f"bad asset name in manifest: {name!r}")
    p = dest / name
    if not p.exists():
        if fetch:
            p.write_bytes(fetch(name))
        elif not ASSETS_URL and (PARTS_DIR / name).is_file():
            return PARTS_DIR / name
        else:
            if not ASSETS_URL.startswith("https://"):
                raise MediaError("reel assets must come from https")
            with urllib.request.urlopen(f"{ASSETS_URL}/{name}", timeout=60) as r:  # noqa: S310  (https enforced)
                p.write_bytes(r.read())
    return p


def validate(manifest: dict) -> list[dict]:
    segs = manifest.get("segments")
    if not isinstance(segs, list) or not 1 <= len(segs) <= 40:
        raise MediaError("manifest needs 1–40 segments")
    out = []
    for s in segs:
        kind = s.get("type")
        if kind not in ("file", "media", "still"):
            raise MediaError(f"unknown segment type {kind!r}")
        seg = {"type": kind, "overlays": []}
        if kind == "file":
            seg["name"] = str(s["name"])
            if not _NAME.match(seg["name"]):
                raise MediaError("bad file name")
        else:
            if s.get("asset") not in BY_ID:
                raise MediaError(f"unknown asset {s.get('asset')!r}")
            seg["asset"] = s["asset"]
            seg["optional"] = bool(s.get("optional"))
            seg["start"] = float(min(max(float(s.get("start", 0)), 0), 14))
            seg["dur"] = float(min(max(float(s.get("dur", 4)), 0.5), 15))
        for o in s.get("overlays", []):
            name = str(o["png"])
            if not _NAME.match(name) or not name.endswith(".png"):
                raise MediaError("bad overlay name")
            seg["overlays"].append({"png": name, "from": float(min(max(float(o.get("from", 0)), 0), 15)),
                                    "to": float(min(max(float(o.get("to", 99)), 0), 99))})
        out.append(seg)
    return out


def _render_segment(seg: dict, src: Path, overlays: list[tuple[Path, float, float]], out: Path, *, still: bool) -> None:
    ff = _ffmpeg()
    args = [ff, "-y", "-loglevel", "error"]
    if still:
        args += ["-i", str(src)]   # one frame in; zoompan emits exactly `frames` frames
    else:
        args += ["-ss", f"{seg['start']:.2f}", "-t", f"{seg['dur']:.2f}", "-i", str(src)]
    for p, _, _ in overlays:
        args += ["-i", str(p)]
    frames = int(seg["dur"] * FPS)
    if still:   # slow push-in (Ken Burns) on the 4K still
        base = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                f"zoompan=z='1+0.06*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}[v0]")
    else:
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},fps={FPS},setsar=1[v0]"
    chain, last = [base], "v0"
    for i, (_, a, b) in enumerate(overlays, start=1):
        chain.append(f"[{last}][{i}:v]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[v{i}]")
        last = f"v{i}"
    args += ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-an", *ENCODE, str(out)]
    subprocess.run(args, check=True, timeout=200)  # noqa: S603


def build(*, fetch=None, upload: bool = True) -> dict:
    """Build the reel. `fetch(name) -> bytes` overrides the release download (tests)."""
    t0 = time.monotonic()
    store = MediaStore()
    rows = {r["id"]: r for r in db.all_rows("select id, status, storage_path, web_path from media_assets")}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        segs = validate(json.loads(_fetch("reel.json", tmp, fetch).read_text()))
        parts: list[Path] = []
        used: list[str] = []
        for i, seg in enumerate(segs):
            out = tmp / f"part-{i:02d}.mp4"
            ovs = [(_fetch(o["png"], tmp, fetch), o["from"], min(o["to"], seg.get("dur", 99))) for o in seg["overlays"]]
            if seg["type"] == "file":
                src = _fetch(seg["name"], tmp, fetch)
                if not ovs:
                    parts.append(src)
                    continue
                seg = {**seg, "start": 0.0, "dur": 15.0}
                _render_segment(seg, src, ovs, out, still=False)
            else:
                r = rows.get(seg["asset"])
                if not r or r["status"] != "ready":
                    if seg["optional"]:
                        continue
                    raise MediaError(f"{seg['asset']} is not generated yet")
                rel = r["web_path"] or r["storage_path"]
                ext = rel.rsplit(".", 1)[-1]
                src = tmp / f"src-{i:02d}.{ext}"
                src.write_bytes(store.get(rel))
                _render_segment(seg, src, ovs, out, still=seg["type"] == "still")
                used.append(seg["asset"])
            parts.append(out)
        lst = tmp / "parts.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        reel = tmp / REEL_NAME
        # Stream-copy join: parts share ENCODE settings, so nothing is re-encoded here.
        subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),  # noqa: S603
                        "-c", "copy", "-movflags", "+faststart", str(reel)], check=True, timeout=120)
        data = reel.read_bytes()
        if len(data) > LIMIT:
            raise MediaError(f"reel is {len(data) // 1048576} MB, over the 50 MB limit")
        url = publish(data) if upload else None
        if not upload:
            (store.root / "reel").mkdir(parents=True, exist_ok=True)
            (store.root / "reel" / REEL_NAME).write_bytes(data)
    return {"url": url, "bytes": len(data), "segments": len(parts), "ai_assets": used,
            "build_s": round(time.monotonic() - t0, 1)}


def publish(data: bytes) -> str:
    store = MediaStore()
    if not store.remote:
        p = store.root / "reel" / REEL_NAME
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)
    store._req("POST", f"object/{PUBLIC_BUCKET}/{REEL_NAME}", data,
               {"content-type": "video/mp4", "x-upsert": "true", "cache-control": "max-age=300"})
    return f"{store.url}/storage/v1/object/public/{PUBLIC_BUCKET}/{REEL_NAME}"


def public_url() -> str | None:
    store = MediaStore()
    return f"{store.url}/storage/v1/object/public/{PUBLIC_BUCKET}/{REEL_NAME}" if store.remote else None
