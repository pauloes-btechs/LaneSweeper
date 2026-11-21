"""Demo reel: manifest validation and an end-to-end build from synthetic parts (no model calls, no network)."""
import json
import subprocess

import imageio_ffmpeg
import pytest

from lanekeeper import db, media, reel

pytestmark = pytest.mark.db
FF = imageio_ffmpeg.get_ffmpeg_exe()


def _clip(path, size="1920x1080", secs=2, encode=True):
    args = [FF, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30", "-t", str(secs)]
    subprocess.run([*args, *(reel.ENCODE if encode else ["-c:v", "libx264", "-preset", "ultrafast"]), str(path)], check=True)  # noqa: S603
    return path.read_bytes()


def _png(path, w=1920, h=1080):
    from PIL import Image
    Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(path)
    return path.read_bytes()


def test_manifest_validation_rejects_paths_and_unknown_assets():
    with pytest.raises(media.MediaError):
        reel.validate({"segments": [{"type": "file", "name": "../../etc/passwd.mp4"}]})
    with pytest.raises(media.MediaError):
        reel.validate({"segments": [{"type": "media", "asset": "not-in-catalog"}]})
    with pytest.raises(media.MediaError):
        reel.validate({"segments": [{"type": "file", "name": "a.mp4", "overlays": [{"png": "x.mp4"}]}]})
    ok = reel.validate({"segments": [{"type": "media", "asset": "pov-dawn-glass", "start": -5, "dur": 99}]})
    assert ok[0]["start"] == 0 and ok[0]["dur"] == 15


def test_build_joins_release_parts_with_ai_clip_and_still(fresh_db, monkeypatch, tmp_path):
    monkeypatch.setenv("LK_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    db.one("delete from media_assets returning 1")
    store = media.MediaStore()
    store.put("glasses_pov/pov.mp4", _clip(tmp_path / "pov.mp4", "3840x2160", 3, encode=False), "video/mp4")
    from PIL import Image
    Image.new("RGB", (4800, 3584), (60, 140, 90)).save(tmp_path / "still.jpg")
    store.put("evidence/still.jpg", (tmp_path / "still.jpg").read_bytes(), "image/jpeg")
    for aid, rel in (("pov-dawn-glass", "glasses_pov/pov.mp4"), ("evidence-glass-before", "evidence/still.jpg")):
        a = media.BY_ID[aid]
        media._upsert(a, status="ready", storage_path=rel)

    release = {
        "title.mp4": _clip(tmp_path / "title.mp4", secs=1),
        "cap-1.png": _png(tmp_path / "cap-1.png"),
        "reel.json": json.dumps({"segments": [
            {"type": "file", "name": "title.mp4"},
            {"type": "media", "asset": "pov-dawn-glass", "start": 0.5, "dur": 2, "overlays": [{"png": "cap-1.png", "from": 0}]},
            {"type": "still", "asset": "evidence-glass-before", "dur": 1.5, "overlays": [{"png": "cap-1.png"}]},
            {"type": "media", "asset": "pov-night-glass", "optional": True},
        ]}).encode(),
    }
    out = reel.build(fetch=lambda name: release[name])
    assert out["segments"] == 3 and out["ai_assets"] == ["pov-dawn-glass", "evidence-glass-before"]
    probe = subprocess.run([FF, "-i", out["url"]], capture_output=True, text=True)  # noqa: S603
    assert "1920x1080" in probe.stderr
    dur = probe.stderr.split("Duration: ")[1].split(",")[0]
    h, m, s = dur.split(":")
    assert 4.0 <= float(s) <= 5.2, dur


def test_missing_required_asset_is_a_clear_error(fresh_db, monkeypatch, tmp_path):
    monkeypatch.setenv("LK_MEDIA_DIR", str(tmp_path))
    db.one("delete from media_assets returning 1")
    rel = {"reel.json": json.dumps({"segments": [{"type": "media", "asset": "pov-dawn-glass"}]}).encode()}
    with pytest.raises(media.MediaError, match="not generated yet"):
        reel.build(fetch=lambda n: rel[n])
