#!/usr/bin/env python3
"""Generate test scans that exercise the AI OCR Rescue + routing:
  bad_blank_scan.png   — pure white (0 words -> unreadable -> routed)
  bad_noise_scan.png   — random static noise (garbage -> routed)
  bad_blurry_scan.png  — the Hindi khatauni, heavily blurred + darkened
                         (AI tries to rescue; if it can't -> routed)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from landrec import paths  # noqa: E402

import numpy as np  # noqa: E402
import cv2  # noqa: E402

OUT = os.path.join(paths.resource_dir(), "samples")
os.makedirs(OUT, exist_ok=True)


def blank():
    img = np.full((700, 900, 3), 255, dtype=np.uint8)
    cv2.imwrite(os.path.join(OUT, "bad_blank_scan.png"), img)


def noise():
    img = (np.random.rand(700, 900, 3) * 255).astype(np.uint8)
    cv2.imwrite(os.path.join(OUT, "bad_noise_scan.png"), img)


def blurry():
    src = os.path.join(OUT, "hindi_khatauni_sample.png")
    if not os.path.exists(src):
        print("hindi_khatauni_sample.png missing — skipping blurry sample")
        return
    img = cv2.imread(src)
    h, w = img.shape[:2]
    # shrink to 25% then upscale with nearest -> destroys fine text
    small = cv2.resize(img, (max(20, w // 4), max(20, h // 4)), interpolation=cv2.INTER_AREA)
    destroyed = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    # heavy gaussian blur + 40% darken
    destroyed = cv2.GaussianBlur(destroyed, (9, 9), 3)
    destroyed = (destroyed * 0.6).astype(np.uint8)
    cv2.imwrite(os.path.join(OUT, "bad_blurry_scan.png"), destroyed)


blank()
noise()
blurry()
print("bad samples written to", OUT)
