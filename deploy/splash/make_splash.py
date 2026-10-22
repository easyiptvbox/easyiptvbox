#!/usr/bin/env python3
"""Generate the EasyIPTVBox boot and shutdown splash images (1920x1080 PNG).

    python3 make_splash.py [output_dir]

Writes splash.png (boot: title + tips) and shutdown.png.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
TITLE = "EasyIPTVBox"
ACCENT = (90, 200, 120)
DIM = (150, 155, 170)
WHITE = (235, 238, 245)

TIPS = [
    "MENU, BACK or HOME on the start page opens the menu",
    "Hold an arrow to scroll quickly",
    "OK pauses live TV  \u00b7  LEFT / RIGHT skip 30 s",
    "Settings: scan the QR code with your phone",
    "New place?  Menu \u203a Wi-Fi setup",
    "New remote?  Menu \u203a Add a remote",
    "MENU in a channel list refreshes the pictures",
]


def font(path, size):
    return ImageFont.truetype(path, size)


def centered(draw, y, text, fnt, fill):
    w = draw.textlength(text, font=fnt)
    draw.text(((W - w) / 2, y), text, font=fnt, fill=fill)


def title_block(draw, cy):
    f = font(BOLD, 150)
    box = draw.textbbox((0, 0), TITLE, font=f)
    th = box[3] - box[1]
    y = cy - th // 2 - box[1]
    centered(draw, y, TITLE, f, WHITE)
    # thin accent line under the title
    lw = 420
    draw.rectangle(((W - lw) // 2, cy + th // 2 + 30,
                    (W + lw) // 2, cy + th // 2 + 36), fill=ACCENT)
    return cy + th // 2 + 36


def boot_image(path):
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    title_block(d, H // 2 - 40)
    ft = font(REG, 34)
    # tips: 3 above, 4 below the title, in two loose bands
    above, below = TIPS[:3], TIPS[3:]
    y = 110
    for t in above:
        centered(d, y, t, ft, DIM)
        y += 62
    y = H // 2 + 150
    for t in below:
        centered(d, y, t, ft, DIM)
        y += 62
    centered(d, H - 70, "Starting\u2026", font(REG, 28), (90, 95, 110))
    img.save(path)


def shutdown_image(path):
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)
    centered(d, H // 2 - 130, TITLE, font(BOLD, 130), WHITE)
    centered(d, H // 2 + 40, "shutting down, please wait",
             font(REG, 60), DIM)
    img.save(path)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    boot_image(os.path.join(out, "splash.png"))
    shutdown_image(os.path.join(out, "shutdown.png"))
    print("wrote", os.path.join(out, "splash.png"),
          os.path.join(out, "shutdown.png"))
