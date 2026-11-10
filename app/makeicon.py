#!/usr/bin/env python3
"""Vẽ biểu tượng app Cổng (chỉ thư viện chuẩn) — dùng: python3 app/makeicon.py <thư mục ra>."""

import math
import os
import struct
import sys
import zlib

BG_TOP = (30, 42, 78)
BG_BOT = (13, 20, 42)
GLYPH = (61, 220, 151)
SS = 3  # siêu mẫu để khử răng cưa


def in_rounded_rect(x, y, s, radius):
    if x < 0 or y < 0 or x > s or y > s:
        return False
    cx = min(max(x, radius), s - radius)
    cy = min(max(y, radius), s - radius)
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= radius * radius


def capsule(x, y, x0, y0, x1, y1, half):
    vx, vy = x1 - x0, y1 - y0
    wx, wy = x - x0, y - y0
    seg = vx * vx + vy * vy
    t = 0.0 if seg == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / seg))
    px, py = x0 + t * vx, y0 + t * vy
    dx, dy = x - px, y - py
    return dx * dx + dy * dy <= half * half


def sample(x, y, s):
    """Trả về (r,g,b,a) tại điểm (x,y) trong ô vuông cạnh s."""
    radius = 0.225 * s
    if not in_rounded_rect(x, y, s, radius):
        return (0, 0, 0, 0)

    f = max(0.0, min(1.0, y / s))
    r = int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * f)
    g = int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * f)
    b = int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * f)

    cx, cy = s / 2.0, 0.555 * s
    ring_r = 0.255 * s
    thick = 0.068 * s
    d = math.hypot(x - cx, y - cy)
    on_ring = abs(d - ring_r) <= thick / 2.0
    if on_ring:
        ang = math.degrees(math.atan2(-(y - cy), x - cx))  # 90 = hướng lên
        if 66.0 < ang < 114.0:
            on_ring = False
    bar = capsule(x, y, cx, cy - ring_r * 0.98, cx, cy - ring_r * 0.02, thick * 0.42)

    if on_ring or bar:
        return (GLYPH[0], GLYPH[1], GLYPH[2], 255)
    return (r, g, b, 255)


def render(s):
    rows = []
    for y in range(s):
        row = []
        for x in range(s):
            ar = ag = ab = aa = 0
            for sy in range(SS):
                for sx in range(SS):
                    px = x + (sx + 0.5) / SS
                    py = y + (sy + 0.5) / SS
                    cr, cg, cb, ca = sample(px * s / s, py, s)
                    ar += cr * ca
                    ag += cg * ca
                    ab += cb * ca
                    aa += ca
            n = SS * SS
            if aa == 0:
                row.append((0, 0, 0, 0))
            else:
                row.append((ar // aa, ag // aa, ab // aa, aa // n))
        rows.append(row)
    return rows


def write_png(path, rows):
    h = len(rows)
    w = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(kind, data):
        body = kind + data
        return (struct.pack(">I", len(data)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    out = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
           chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(out)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out, exist_ok=True)
    cache = {}
    for name, size in (("AppIcon60x60@2x.png", 120),
                       ("AppIcon60x60@3x.png", 180)):
        if size not in cache:
            cache[size] = render(size)
        write_png(os.path.join(out, name), cache[size])
        print("icon:", os.path.join(out, name), size)


if __name__ == "__main__":
    main()