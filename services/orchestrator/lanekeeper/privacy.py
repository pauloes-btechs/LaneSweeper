"""Evidence minimisation for rider/glasses frames.

Deterministic de-identification that needs no model: keep only the road-surface band of the frame (where lane
hazards are), drop everything above it (faces, plates, storefronts are mostly above the lane surface), downscale,
and re-encode to strip EXIF/GPS metadata. Documented limitation: a person lying in the lane or a plate on a vehicle
in the lane would remain; reports with obstruction claims are kept private and never published."""
from __future__ import annotations

import io

from PIL import Image

KEEP_BOTTOM_FRACTION = 0.55
MAX_WIDTH = 960


def minimise_frame(data: bytes) -> tuple[bytes, dict]:
    im = Image.open(io.BytesIO(data))
    im.load()
    w, h = im.size
    top = int(h * (1 - KEEP_BOTTOM_FRACTION))
    crop = im.convert("RGB").crop((0, top, w, h))
    if crop.width > MAX_WIDTH:
        crop = crop.resize((MAX_WIDTH, int(crop.height * MAX_WIDTH / crop.width)))
    out = io.BytesIO()
    crop.save(out, format="JPEG", quality=82)   # fresh encode: no EXIF, no GPS tags
    return out.getvalue(), {"method": "road_band_crop+reencode", "kept_bottom_fraction": KEEP_BOTTOM_FRACTION,
                            "original_size": [w, h], "output_size": list(crop.size), "exif_stripped": True}
