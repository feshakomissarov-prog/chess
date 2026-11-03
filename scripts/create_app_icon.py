"""Generate the project's small original Windows ICO (stdlib only)."""

from __future__ import annotations

import struct
from pathlib import Path


SIZE = 64
BG = (26, 31, 37, 255)
TEAL = (72, 184, 166, 255)
LIGHT = (225, 229, 222, 255)
DARK = (47, 55, 63, 255)


def _inside_polygon(x: float, y: float, points: tuple[tuple[int, int], ...]) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def make_icon() -> bytes:
    pixels = [[BG for _x in range(SIZE)] for _y in range(SIZE)]
    for y in range(5, 59):
        for x in range(5, 59):
            if x in range(5, 8) or x in range(56, 59) or y in range(5, 8) or y in range(56, 59):
                pixels[y][x] = TEAL
    left, top, cell = 12, 12, 10
    for row in range(4):
        for col in range(4):
            color = LIGHT if (row + col) % 2 == 0 else DARK
            for y in range(top + row * cell, top + (row + 1) * cell):
                for x in range(left + col * cell, left + (col + 1) * cell):
                    pixels[y][x] = color
    # A simple original knight profile made from a polygon, independent of fonts/assets.
    knight = ((26, 43), (26, 38), (22, 34), (24, 28), (29, 22), (28, 18),
              (34, 20), (40, 26), (39, 32), (36, 36), (40, 39), (40, 43))
    for y in range(SIZE):
        for x in range(SIZE):
            if _inside_polygon(x + 0.5, y + 0.5, knight):
                pixels[y][x] = BG
    # Eye cutout.
    pixels[25][32] = TEAL
    xor_bitmap = bytearray()
    for row in reversed(pixels):
        for red, green, blue, alpha in row:
            xor_bitmap.extend((blue, green, red, alpha))
    and_row_bytes = ((SIZE + 31) // 32) * 4
    and_mask = bytes(and_row_bytes * SIZE)  # all pixels are opaque
    image_size = 40 + len(xor_bitmap) + len(and_mask)
    header = struct.pack("<IIIHHIIIIII", 40, SIZE, SIZE * 2, 1, 32, 0,
                         len(xor_bitmap) + len(and_mask), 0, 0, 0, 0)
    bitmap = header + xor_bitmap + and_mask
    directory = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", SIZE, SIZE, 0, 0, 1, 32, image_size, 22)
    return directory + entry + bitmap


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "src" / "chess_coach" / "resources" / "chess_coach.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(make_icon())
    print(target)


if __name__ == "__main__":
    main()
