"""Pack SVG-rendered PNGs into a GIF and storyboard; requires Pillow."""

import argparse
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=Path, required=True)
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[2] / "docs/brand"
    paths = sorted(args.frames.glob("*.png"))
    if len(paths) != 150:
        parser.error("Expected 150 PNGs from tools/brand/build-vector.cjs --frames.")
    frames = [Image.open(path).convert("RGB") for path in paths]
    strip = Image.new("RGB", (360 * 4, 360))
    for index, frame in enumerate([10, 28, 38, 70]):
        strip.paste(frames[frame], (index * 360, 0))
    palette = strip.quantize(colors=192, method=Image.Quantize.MEDIANCUT)
    frames = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    frames[0].save(
        output / "snowbeam-motion.gif",
        save_all=True,
        append_images=frames[1:],
        duration=[60, 70, 70] * 50,
        loop=0,
        disposal=2,
        optimize=False,
    )
    # Packing only: the individual rendered frames remain unchanged.
    board = Image.new("RGB", (320 * 5, 320), (8, 19, 27))
    for index, label in enumerate(["standing", "beam", "vanishing", "gone", "snow"]):
        frame = Image.open(output / f"{label}.png").convert("RGBA")
        board.paste(frame, (index * 320, 0), frame)
    board.save(output / "storyboard.png")
    print("Packed the 10-second GIF and five-frame storyboard.")


if __name__ == "__main__":
    main()
