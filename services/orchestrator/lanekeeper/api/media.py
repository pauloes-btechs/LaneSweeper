"""Demo media: anyone signed in may view ready AI-generated assets; only a city admin (admin.budget) may spend on
generating them. Files are served via short signed links, never streamed through this function on Vercel."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import media, reel
from ..auth import Principal
from ..policy.engine import check
from .common import jurisdiction_for, principal, staff

router = APIRouter(prefix="/v1")


@router.get("/media")
def list_media(p: Principal = Depends(principal)):
    staff(p)   # any signed-in user (rider or staff)
    return {"assets": media.catalog(), "provenance": "AI_GENERATED · SIMULATED", "reel_url": reel.public_url(),
            "models": {"video": media.VIDEO_MODEL, "image": media.IMAGE_MODEL}}


@router.get("/media/file/{rel:path}")
def media_file(rel: str, exp: int, sig: str):
    path = media.MediaStore().local_path(rel, exp, sig)
    if not path:
        raise HTTPException(403, "link expired or invalid")
    return FileResponse(path, headers={"cache-control": "private, max-age=3600"})


class GenerateBody(BaseModel):
    jurisdiction: str | None = None
    resolution: str | None = Field(default=None, pattern=r"^(720p|1080p|4k|1K|2K|4K)$")


def _admin(p: Principal, jurisdiction: str | None) -> None:
    jid = jurisdiction_for(staff(p), jurisdiction)
    check(p, jid, "admin.budget").raise_if_denied()


@router.post("/admin/media/{asset_id}/generate")
def generate(asset_id: str, body: GenerateBody, p: Principal = Depends(principal)):
    _admin(p, body.jurisdiction)
    try:
        return media.generate(asset_id, p.user_id, resolution=body.resolution)
    except media.MediaError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/admin/media/{asset_id}/poll")
def poll(asset_id: str, body: GenerateBody, p: Principal = Depends(principal)):
    _admin(p, body.jurisdiction)
    try:
        return media.poll(asset_id)
    except media.MediaError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/admin/media/reel/build")
def build_reel(body: GenerateBody, p: Principal = Depends(principal)):
    """Assemble the public demo reel from generated media + the release's pre-rendered parts. No model calls."""
    _admin(p, body.jurisdiction)
    try:
        return reel.build()
    except media.MediaError as e:
        raise HTTPException(409, str(e)) from e
