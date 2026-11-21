"""Pre-render the demo reel's non-AI parts into $OUT (default /tmp/release): title/end cards, caption + HUD overlays,
captioned walkthrough segments, the twin render, and reel.json. Every video uses lanekeeper.reel.ENCODE so the
server can stream-copy the join. Run: python scripts/demo/make_parts.py <walkthrough.webm> <marks.json> <twin_frames_dir>"""
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "orchestrator"))
from lanekeeper.reel import ENCODE  # noqa: E402

OUT = Path(os.environ.get("OUT", "/tmp/release"))
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1920, 1080
GF = "/usr/share/fonts/truetype/google-fonts"
F = {k: ImageFont.truetype(f"{GF}/Poppins-{v}.ttf", s) for k, (v, s) in {
    "h1": ("Bold", 104), "h2": ("Medium", 40), "cap": ("Medium", 34), "small": ("Medium", 26), "end": ("Bold", 64)}.items()}
MONO = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 22)
HUD = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
GREEN, ACCENT = (47, 143, 91), (127, 211, 163)


def wrap(d, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) > width and cur:
            lines.append(cur); cur = w
        else:
            cur = t
    return lines + [cur]


def caption_png(name, step, tag, text, badge=True):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lines = wrap(d, text, F["cap"], 1180)
    bh = 30 + 34 + len(lines) * 48 + 24
    x0, y1 = 44, H - 44
    y0 = y1 - bh
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0 + 6, y0 + 10, x0 + 1260, y1 + 10), 18, fill=(0, 0, 0, 110))
    im = Image.alpha_composite(im, shadow.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((x0, y0, x0 + 1250, y1), 16, fill=(10, 14, 20, 222))
    d.rectangle((x0, y0 + 8, x0 + 7, y1 - 8), fill=GREEN)
    d.text((x0 + 30, y0 + 22), f"STEP {step} · {tag}", font=MONO, fill=ACCENT)
    for i, ln in enumerate(lines):
        d.text((x0 + 30, y0 + 58 + i * 48), ln, font=F["cap"], fill="white")
    if badge:
        t = "AI-GENERATED · SIMULATED · FICTIONAL STREET"
        tw = d.textlength(t, font=MONO)
        d.rounded_rectangle((W - tw - 72, 36, W - 36, 80), 8, fill=(0, 0, 0, 150))
        d.text((W - tw - 54, 46), t, font=MONO, fill="white")
    im.save(OUT / name)


def hud_png(name, text):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    tw = d.textlength(text, font=HUD)
    x0, y0 = (W - tw) / 2 - 40, 640
    d.rounded_rectangle((x0, y0, x0 + tw + 80, y0 + 92), 16, fill=(0, 0, 0, 200), outline=(255, 212, 0), width=4)
    d.text((x0 + 40, y0 + 18), text, font=HUD, fill=(255, 212, 0))
    d.text((60, 120), "SIMULATED GLASSES HUD · 18 km/h · LINK OK", font=MONO, fill=(255, 212, 0))
    im.save(OUT / name)


def card_mp4(name, title, sub, foot, secs, big=True):
    im = Image.new("RGB", (W, H))
    px = im.load()
    for y in range(H):   # deep night-street gradient
        c = (int(8 + 10 * y / H), int(14 + 26 * y / H), int(18 + 20 * y / H))
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    d.rectangle((0, H - 14, W, H), fill=GREEN)
    f = F["h1"] if big else F["end"]
    d.text((140, 330 if big else 360), title, font=f, fill="white")
    for i, ln in enumerate(wrap(d, sub, F["h2"], 1500)):
        d.text((144, (480 if big else 470) + i * 58), ln, font=F["h2"], fill=(205, 226, 214))
    d.text((144, H - 120), foot, font=F["small"], fill=(150, 170, 160))
    png = OUT / (name + ".png")
    im.save(png)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", str(secs), "-i", str(png), "-vf",
                    f"fade=in:0:12,fade=out:{int(secs * 30) - 12}:12,format=yuv420p", *ENCODE, str(OUT / name)], check=True)
    png.unlink()


def cut(src, a, b, name):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", src, "-vf",
                    "scale=1920:1080,fps=30,format=yuv420p", "-an", *ENCODE, str(OUT / name)], check=True)


if __name__ == "__main__":
    webm, marks_json, twin_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    card_mp4("00-title.mp4", "LaneKeeper OS",
             "From a rider’s smart glasses to a verified bike-lane cleanup: AI agents, human approval, simulated robots.",
             "Demo · fictional NYC tenant · AI-generated footage is labelled · no real robot is controlled", 4.5)
    caption_png("cap-glasses.png", 1, "AI-GENERATED FOOTAGE · SIMULATED HUD",
                "Smart glasses spot broken glass on the device and warn the rider. It works offline.")
    hud_png("hud-warn.png", "⚠  Possible debris ahead — slow down")
    caption_png("cap-confounder.png", 1, "AI-GENERATED FOOTAGE · DETECTOR TEST CLIP",
                "Wet reflections are a known false-alarm risk; every clip carries a ground-truth label for honest testing.")
    caption_png("cap-before.png", 5, "AI-GENERATED STILL · BEFORE", "Verification compares before and after frames of the lane cell.")
    caption_png("cap-after.png", 5, "AI-GENERATED STILL · AFTER", "Only verified cells count as cleared. A sweep alone is never enough.")
    caption_png("cap-clear.png", 8, "AI-GENERATED FOOTAGE", "The lane is clear again, and the rider who reported it gets the status update.")
    m = json.load(open(marks_json))
    marks = m["marks"]
    names = {}
    for i, x in enumerate(marks):
        if x["label"].startswith("SEG"):
            end = next(y["t"] for y in marks[i + 1:] if y["label"] == "END")
            seg = x["label"].split()[1]
            names[seg] = f"rec-{seg}.mp4"
            cut(m["video"], x["t"] + 0.4, end + 0.2, names[seg])
    # twin render + baked caption
    caption_png("cap-twin.png", 4, "DIGITAL TWIN · SIMULATION",
                "The LK-01 twin sweeps the lane. Shards disappear only where the brushes pass.", badge=False)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "24", "-i", f"{twin_dir}/twin-%04d.jpg",
                    "-i", str(OUT / "cap-twin.png"), "-filter_complex", "[0:v]fps=30,format=yuv420p[a];[a][1:v]overlay=0:0,format=yuv420p",
                    *ENCODE, str(OUT / "twin.mp4")], check=True)
    (OUT / "cap-twin.png").unlink()
    card_mp4("99-end.mp4", "Simulation-first. Human-approved.",
             "No AI output ever becomes a motor, steering or braking command. Every dispatch needs a named supervisor’s approval.",
             "github.com/pauloes-btechs/LaneSweeper · footage: Gemini Omni Flash, Nano Banana (AI-generated) · fictional tenant", 5, big=False)
    manifest = {"segments": [
        {"type": "file", "name": "00-title.mp4"},
        {"type": "media", "asset": "pov-dawn-glass", "start": 0.0, "dur": 9.5,
         "overlays": [{"png": "cap-glasses.png", "from": 0}, {"png": "hud-warn.png", "from": 4.2}]},
        {"type": "media", "asset": "pov-wet-reflections", "start": 1.0, "dur": 4.5, "optional": True,
         "overlays": [{"png": "cap-confounder.png", "from": 0}]},
        {"type": "file", "name": names["drafts"]},
        {"type": "file", "name": names["approval"]},
        {"type": "file", "name": "twin.mp4"},
        {"type": "still", "asset": "evidence-glass-before", "dur": 3.5, "overlays": [{"png": "cap-before.png", "from": 0}]},
        {"type": "still", "asset": "evidence-glass-after", "dur": 3.5, "overlays": [{"png": "cap-after.png", "from": 0}]},
        {"type": "file", "name": names["ticket"]},
        {"type": "file", "name": names["report"]},
        {"type": "media", "asset": "pov-day-clear", "start": 1.0, "dur": 5, "optional": True,
         "overlays": [{"png": "cap-clear.png", "from": 0}]},
        {"type": "file", "name": "99-end.mp4"},
    ]}
    (OUT / "reel.json").write_text(json.dumps(manifest, indent=2))
    print("\n".join(sorted(p.name for p in OUT.iterdir())))
