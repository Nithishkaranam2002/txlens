"""Build docs/images/demo.gif from the frames of frontend/scripts/demo-gif.ts.

Each frame is scaled to WIDTH pixels and shown for its recorded hold time. Identical
consecutive frames are merged, and all frames share one palette, so the GIF stays small.

Usage: uv run python infra/demo/make_gif.py <frames dir> [output] [width]
"""

import json
import sys
from pathlib import Path

from PIL import Image

WIDTH = 960
OUTPUT = Path("docs/images/demo.gif")


def load(folder: Path, width: int) -> list[tuple[Image.Image, int]]:
    """(scaled frame, hold time in ms), identical neighbours merged."""
    frames: list[tuple[Image.Image, int]] = []
    for entry in json.loads((folder / "frames.json").read_text()):
        image = Image.open(folder / entry["file"]).convert("RGB")
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
        if frames and frames[-1][0].tobytes() == image.tobytes():
            frames[-1] = (frames[-1][0], frames[-1][1] + entry["ms"])
        else:
            frames.append((image, entry["ms"]))
    return frames


def build(folder: Path, output: Path, width: int = WIDTH) -> tuple[int, float, int]:
    """Write the GIF; returns (frames, seconds, bytes)."""
    frames = load(folder, width)
    palette = frames[0][0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    images = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image, _ in frames]
    images[0].save(
        output, save_all=True, append_images=images[1:], loop=0, optimize=True,
        duration=[ms for _, ms in frames], disposal=1,
    )  # fmt: skip
    return len(frames), sum(ms for _, ms in frames) / 1000, output.stat().st_size


def main(argv: list[str]) -> int:
    """Entry point."""
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    output = Path(argv[1]) if len(argv) > 1 else OUTPUT
    width = int(argv[2]) if len(argv) > 2 else WIDTH
    count, seconds, size = build(Path(argv[0]), output, width)
    print(f"{output}: {count} frames, {seconds:.1f} s, {size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
