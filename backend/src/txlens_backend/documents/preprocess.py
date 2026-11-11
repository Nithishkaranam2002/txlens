"""Page images prepared for Tesseract: resolution, deskew, denoise, upscale, binarize.

Each step is optional so `make ocr-report` can measure what it adds (evals/reports/ocr.md).
Deskewing rotates the image by at most 2 degrees; word boxes are kept in the rotated frame,
which moves them by at most a few points near the page edges.
"""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from PIL import Image, ImageFilter

Binarize = Literal["none", "otsu", "adaptive"]
MAX_SKEW = 2.0  # degrees searched each way
SKEW_STEP = 0.1
ADAPTIVE_RADIUS = 15  # pixels of the local neighbourhood (at the prepared resolution)
ADAPTIVE_OFFSET = 12  # how much darker than its neighbourhood a pixel must be to be ink


@dataclass(frozen=True)
class OcrSettings:
    """How a page is rendered, cleaned and read."""

    # Defaults: the best setup measured by `make ocr-report` (evals/reports/ocr.md).
    dpi: int = 300
    deskew: bool = True
    median: int = 3  # median filter size in pixels; 0 = off
    binarize: Binarize = "none"
    upscale: float = 1.0
    psm: int = 4
    whitelist_rereads: bool = True  # re-read score and grade tokens with allowed characters

    @property
    def pixels_per_inch(self) -> float:
        """Resolution of the image Tesseract reads."""
        return self.dpi * self.upscale


def skew_angle(image: Image.Image) -> float:
    """The rotation (degrees) that makes text rows sharpest: the most peaked row profile."""
    small = Image.fromarray(255 - np.asarray(image.reduce(4)))
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-MAX_SKEW, MAX_SKEW + SKEW_STEP / 2, SKEW_STEP):
        rows = np.asarray(small.rotate(float(angle)), dtype=np.float64).sum(axis=1)
        score = float(np.var(rows))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return best_angle


def otsu_level(image: Image.Image) -> int:
    """The grey level that best separates ink from paper (Otsu's method)."""
    histogram = np.bincount(np.asarray(image).ravel(), minlength=256).astype(np.float64)
    levels = np.arange(256)
    weight = np.cumsum(histogram)
    mean = np.cumsum(histogram * levels)
    total, total_mean = weight[-1], mean[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (total_mean * weight / total - mean) ** 2 / (weight * (total - weight))
    return int(np.nanargmax(between))


def adaptive(image: Image.Image) -> Image.Image:
    """Ink where a pixel is clearly darker than its neighbourhood (handles uneven paper)."""
    pixels = np.asarray(image, dtype=np.int16)
    local = np.asarray(image.filter(ImageFilter.BoxBlur(ADAPTIVE_RADIUS)), dtype=np.int16)
    return Image.fromarray(np.where(pixels < local - ADAPTIVE_OFFSET, 0, 255).astype(np.uint8))


def prepare(image: Image.Image, settings: OcrSettings) -> Image.Image:
    """Apply the configured steps to a grayscale page image."""
    if settings.deskew:
        image = image.rotate(skew_angle(image), resample=Image.Resampling.BICUBIC, fillcolor=255)
    if settings.median:
        image = image.filter(ImageFilter.MedianFilter(settings.median))
    if settings.upscale != 1.0:
        size = (round(image.width * settings.upscale), round(image.height * settings.upscale))
        image = image.resize(size, Image.Resampling.LANCZOS)
    if settings.binarize == "otsu":
        level = otsu_level(image)
        image = image.point(lambda value: 255 if value > level else 0)
    elif settings.binarize == "adaptive":
        image = adaptive(image)
    return image
