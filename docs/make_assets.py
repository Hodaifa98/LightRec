"""Generate the brand assets: logo.svg, icon.ico, icon-512.png and the README screenshots.

Run from the project root with the venv:  .venv\\Scripts\\python docs\\make_assets.py
The screenshots open the real app for about 40 seconds (instant replay needs to fill up).
"""
from __future__ import annotations

import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, ROOT)

BG, RING, DOT = (0x13, 0x15, 0x1A), (0x2D, 0x32, 0x40), (0xFF, 0x4D, 0x5E)


# PNG / ICO writers (no Pillow needed).
def png_bytes(rgba: bytes, w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bmp_icon_entry(rgba: bytes, size: int) -> bytes:
    """
    A 32-bit BMP icon image: header, bottom-up BGRA pixels, then a 1-bit AND mask
    """
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
    rows = []
    for y in range(size - 1, -1, -1):
        row = rgba[y * size * 4:(y + 1) * size * 4]
        rows.append(b"".join(bytes((row[i + 2], row[i + 1], row[i], row[i + 3])) for i in range(0, len(row), 4)))
    mask_row = ((size + 31) // 32) * 4
    return header + b"".join(rows) + bytes(mask_row * size)


def ico_bytes(images: list[tuple[int, bytes, bool]]) -> bytes:
    """
    images: (size, data, is_png)
    """
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = [], []
    for size, data, _is_png in images:
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        blobs.append(data)
        offset += len(data)
    return out + b"".join(entries) + b"".join(blobs)


# Rasterizer for the logo: dark rounded square, grey ring, red dot (4x4 supersampling).
def render_logo(size: int, badge: bool = True) -> bytes:
    ss = 4
    radius = size * 0.22
    ring_r, dot_r = size * 0.34, size * 0.20
    c = size / 2
    out = bytearray()
    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    px, py = x + (sx + 0.5) / ss, y + (sy + 0.5) / ss
                    dx, dy = px - c, py - c
                    d = (dx * dx + dy * dy) ** 0.5
                    if d < dot_r:
                        col = (*DOT, 255)
                    elif d < ring_r:
                        col = (*RING, 255)
                    elif badge:
                        # Rounded square test.
                        qx, qy = abs(dx) - (c - radius), abs(dy) - (c - radius)
                        inside = (max(qx, 0) ** 2 + max(qy, 0) ** 2) ** 0.5 <= radius if (qx > 0 or qy > 0) else True
                        col = (*BG, 255) if inside else (0, 0, 0, 0)
                    else:
                        col = (0, 0, 0, 0)
                    for i in range(4):
                        acc[i] += col[i]
            n = ss * ss
            a = acc[3] / n
            if a == 0:
                out += b"\x00\x00\x00\x00"
            else:
                # Un-premultiply the colour average by alpha coverage.
                out += bytes((int(acc[0] / n * 255 / a), int(acc[1] / n * 255 / a), int(acc[2] / n * 255 / a), int(a)))
    return bytes(out)


def write_icons() -> None:
    os.makedirs(DOCS, exist_ok=True)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
           '<rect width="128" height="128" rx="28" fill="#13151a"/>'
           '<circle cx="64" cy="64" r="43.5" fill="#2d3240"/>'
           '<circle cx="64" cy="64" r="25.6" fill="#ff4d5e"/></svg>\n')
    with open(os.path.join(DOCS, "logo.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg)
    big = render_logo(512)
    with open(os.path.join(DOCS, "icon-512.png"), "wb") as fh:
        fh.write(png_bytes(big, 512, 512))
    images = []
    for size in (16, 32, 48):
        images.append((size, bmp_icon_entry(render_logo(size), size), False))
    images.append((256, png_bytes(render_logo(256), 256, 256), True))
    with open(os.path.join(DOCS, "icon.ico"), "wb") as fh:
        fh.write(ico_bytes(images))
    print("icons written")


def write_screenshots() -> None:
    from lightrec import winapi
    from lightrec.ui.app import App

    app = App()
    root = app.root

    def grab(name: str) -> None:
        root.update_idletasks()
        hwnd = winapi.tk_hwnd(root)
        winapi.user32.SetWindowDisplayAffinity(winapi.wt.HWND(hwnd), 0)
        g = winapi.WindowGrabber(hwnd, [], False, True)
        data = g.grab()
        w, h = g.width, g.height
        g.close()
        winapi.exclude_from_capture(hwnd)
        if data is None:
            print("grab failed for", name)
            return
        rgba = bytearray()
        for i in range(0, len(data), 4):
            rgba += bytes((data[i + 2], data[i + 1], data[i], 255))
        with open(os.path.join(DOCS, name), "wb") as fh:
            fh.write(png_bytes(bytes(rgba), w, h))
        print("wrote", name, w, h)

    def gamer_shot() -> None:
        grab("screenshot-gamer.png")
        app.replay_var.set(False)
        app._replay_toggled()
        root.after(500, app.on_close)

    root.after(1500, lambda: grab("screenshot-recorder.png"))
    root.after(1700, lambda: (app.view_var.set("game"), app._view_changed()))
    root.after(2000, lambda: (app.replay_var.set(True), app._replay_toggled()))
    # Let the buffer fill so the status reads "30 s ready".
    root.after(35000, gamer_shot)
    root.after(60000, root.destroy)
    app.run()


if __name__ == "__main__":
    write_icons()
    if "--no-shots" not in sys.argv:
        write_screenshots()
