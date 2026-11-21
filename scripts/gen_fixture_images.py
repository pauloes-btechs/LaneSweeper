"""Render the synthetic vision fixtures (rider/robot POV of a protected bike lane) as PNGs.

Deterministic (seeded per fixture). Output: fixtures/images/<id>.png, 640x480. Every frame is
watermarked SYNTHETIC so it can never be mistaken for field evidence.
Usage: python scripts/gen_fixture_images.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "services/orchestrator/lanekeeper/fixtures/vision.json"
OUT = ROOT / "fixtures/images"
W, H = 640, 480
HORIZON = 170

SCENES = {
    "day": dict(sky=(150, 178, 205), asphalt=(78, 80, 84), lane=(46, 120, 74), light=1.0),
    "dawn": dict(sky=(214, 160, 128), asphalt=(70, 66, 70), lane=(40, 100, 66), light=0.8),
    "night": dict(sky=(18, 22, 38), asphalt=(34, 34, 40), lane=(20, 52, 36), light=0.45),
    "dark": dict(sky=(6, 6, 10), asphalt=(14, 14, 16), lane=(10, 20, 14), light=0.15),
}


def to_px(y1000: int, x1000: int) -> tuple[int, int]:
    return int(x1000 / 1000 * W), int(y1000 / 1000 * H)


def lane_poly(frac_left: float, frac_right: float) -> list[tuple[int, int]]:
    """Trapezoid between two lateral fractions, converging toward a vanishing point."""
    vx = W // 2
    return [(int(vx + (frac_left - 0.5) * 40), HORIZON), (int(vx + (frac_right - 0.5) * 40), HORIZON),
            (int(frac_right * W), H), (int(frac_left * W), H)]


def shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def draw_scene(fx: dict) -> Image.Image:
    t = fx["truth"]
    rnd = random.Random(fx["id"])
    sc = SCENES.get(t.get("scene", "day"), SCENES["day"])
    im = Image.new("RGB", (W, H), sc["sky"])
    d = ImageDraw.Draw(im)
    d.polygon([(0, HORIZON), (W, HORIZON), (W, H), (0, H)], fill=sc["asphalt"])
    # asphalt grain
    for _ in range(5000):
        x, y = rnd.randrange(W), rnd.randrange(HORIZON, H)
        g = rnd.randint(-18, 18)
        d.point((x, y), fill=tuple(max(0, min(255, c + g)) for c in sc["asphalt"]))
    d.polygon(lane_poly(0.18, 0.82), fill=sc["lane"])                       # green protected lane
    d.polygon(lane_poly(0.13, 0.18), fill=shade((200, 200, 200), sc["light"]))  # buffer stripe
    d.polygon(lane_poly(0.82, 0.86), fill=shade((200, 200, 200), sc["light"]))
    for i in range(6):                                                       # flexi-post bollards
        y = HORIZON + 20 + i * 50
        x = int(W * 0.1 - (y - HORIZON) * 0.05)
        d.rectangle([x, y - 30 - i * 6, x + 6 + i, y], fill=shade((230, 230, 230), sc["light"]))

    hz = t.get("hazard")
    pts = t.get("points") or []
    if hz == "glass" and t.get("glass"):
        n = max(3, t.get("shards", 6) * 3)
        for p in pts:
            cx, cy = to_px(p[0], p[1])
            for _ in range(n // max(1, len(pts))):
                ox, oy = rnd.gauss(0, 22), rnd.gauss(0, 10)
                s = rnd.uniform(2, 7)
                poly = [(cx + ox + rnd.uniform(-s, s), cy + oy + rnd.uniform(-s, s)) for _ in range(3)]
                col = rnd.choice([(170, 220, 190), (120, 170, 110), (210, 235, 240), (150, 110, 60)])
                d.polygon(poly, fill=shade(col, sc["light"] + 0.2))
                d.point((cx + ox, cy + oy), fill=(255, 255, 255))   # specular glint
    elif hz == "water":
        for p in pts or [[640, 500]]:
            cx, cy = to_px(p[0], p[1])
            d.ellipse([cx - 150, cy - 30, cx + 150, cy + 35], fill=shade((60, 70, 95), sc["light"] + 0.3))
            for _ in range(8):
                gx = cx + rnd.randint(-120, 120)
                d.line([gx, cy - 20, gx + rnd.randint(-5, 5), cy + 25], fill=(220, 210, 160), width=2)
    elif hz == "litter" and "foil" in fx["id"]:
        cx, cy = to_px(*pts[0][:2])
        d.polygon([(cx + rnd.randint(-18, 18), cy + rnd.randint(-12, 12)) for _ in range(9)], fill=(205, 205, 210))
    elif hz == "litter":
        cx, cy = to_px(*pts[0][:2])
        for _ in range(5):
            x0, y0 = cx + rnd.randint(-30, 30), cy + rnd.randint(-10, 10)
            d.rectangle([x0, y0, x0 + rnd.randint(6, 16), y0 + rnd.randint(3, 8)], fill=(225, 235, 240), outline=(180, 190, 200))
    elif hz == "markings":
        d.rectangle([W // 2 - 4, 260, W // 2 + 4, 470], fill=(245, 245, 245))
        d.ellipse([W // 2 - 50, 330, W // 2 - 10, 370], outline=(245, 245, 245), width=4)
        d.ellipse([W // 2 + 10, 330, W // 2 + 50, 370], outline=(245, 245, 245), width=4)
    elif hz == "pothole":
        cx, cy = to_px(*pts[0][:2])
        d.ellipse([cx - 60, cy - 22, cx + 60, cy + 22], fill=(28, 26, 26), outline=(60, 58, 55), width=3)
    elif hz == "branch":
        cx, cy = to_px(*pts[0][:2])
        d.line([cx - 170, cy + 30, cx + 90, cy - 20], fill=(92, 64, 40), width=11)
        for _ in range(7):
            bx = cx + rnd.randint(-150, 70)
            d.line([bx, cy, bx + rnd.randint(-40, 40), cy - rnd.randint(20, 50)], fill=(70, 110, 50), width=5)
    elif hz == "obstruction":
        d.rectangle([220, 230, 450, 430], fill=(235, 235, 235), outline=(90, 90, 90), width=3)
        d.rectangle([240, 250, 430, 330], fill=(120, 150, 170))

    im = im.filter(ImageFilter.GaussianBlur(0.6))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 22], fill=(0, 0, 0))
    d.text((8, 5), f"SYNTHETIC FIXTURE · {fx['id']}", fill=(255, 200, 0))
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    reg = json.loads(REG.read_text())
    for fx in reg["fixtures"]:
        draw_scene(fx).save(OUT / f"{fx['id']}.png", optimize=True)
    print(f"wrote {len(reg['fixtures'])} images to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
