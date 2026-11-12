"""Generate placeholder tray/Discord assets with Pillow."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ASSETS = Path(__file__).resolve().parents[1] / "assets"
BASE_CANDIDATES = ("Switch2Icon.webp", "Switch2Icon.png", "switch2_base.png")
STATE_COLORS = {
    "tray_off": (110, 110, 118),
    "tray_home": (64, 156, 255),
    "tray_game": (76, 187, 108),
    "tray_handheld": (176, 122, 255),
    "tray_error": (230, 74, 74),
}


def load_base(size: int) -> Image.Image | None:
    """Use the user-supplied Switch 2 art when present."""
    for name in BASE_CANDIDATES:
        path = ASSETS / name
        if not path.exists():
            continue
        try:
            return ImageOps.fit(Image.open(path).convert("RGBA"), (size, size), Image.LANCZOS)
        except Exception:
            continue
    return None


def add_state_dot(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    size = image.size[0]
    scale = size / 64.0
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    r = max(3, int(7 * scale))
    cx, cy = size - max(5, int(11 * scale)), size - max(5, int(11 * scale))
    draw.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), fill=(24, 26, 32, 255))
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, 255))
    return canvas


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_switch(size: int, dot: tuple[int, int, int] | None = None, show_two: bool = True) -> Image.Image:
    scale = size / 64.0
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = int(7 * scale)
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=int(14 * scale),
        fill=(30, 33, 42, 255),
        outline=(70, 76, 92, 255),
        width=max(1, int(2 * scale)),
    )
    left = (int(15 * scale), int(19 * scale), int(27 * scale), int(45 * scale))
    right = (int(37 * scale), int(19 * scale), int(49 * scale), int(45 * scale))
    draw.rounded_rectangle(left, radius=int(5 * scale), fill=(0, 196, 220, 255))
    draw.rounded_rectangle(right, radius=int(5 * scale), fill=(232, 74, 86, 255))
    draw.rounded_rectangle(
        (int(29 * scale), int(19 * scale), int(35 * scale), int(45 * scale)),
        radius=int(2 * scale),
        fill=(22, 24, 30, 255),
    )
    for cx, cy in ((21, 26), (21, 38), (43, 26), (43, 38)):
        r = int(2.0 * scale)
        draw.ellipse((cx * scale - r, cy * scale - r, cx * scale + r, cy * scale + r), fill=(20, 22, 28, 255))
    if show_two and size >= 48:
        try:
            font = _font(int(13 * scale))
            draw.text((27 * scale, 28 * scale), "2", font=font, fill=(240, 242, 248, 255), anchor="mm")
        except Exception:
            pass
    if dot is not None:
        r = int(7 * scale)
        cx, cy = size - int(12 * scale), size - int(12 * scale)
        draw.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2), fill=(24, 26, 32, 255))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*dot, 255))
    return image


def draw_discord(size: int = 512) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = int(size * 0.08)
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=int(size * 0.2),
        fill=(24, 27, 36, 255),
        outline=(88, 101, 242, 255),
        width=max(2, int(size * 0.012)),
    )
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    cx, cy = size // 2, int(size * 0.52)
    for radius, alpha in ((int(size * 0.36), 24), (int(size * 0.30), 32), (int(size * 0.24), 42)):
        gdraw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(88, 101, 242, alpha))
    image = Image.alpha_composite(image, glow)
    draw = ImageDraw.Draw(image)
    joy_w, joy_h = int(size * 0.17), int(size * 0.40)
    gap = int(size * 0.035)
    left_box = (cx - gap // 2 - joy_w, cy - joy_h // 2, cx - gap // 2, cy + joy_h // 2)
    right_box = (cx + gap // 2, cy - joy_h // 2, cx + gap // 2 + joy_w, cy + joy_h // 2)
    draw.rounded_rectangle(left_box, radius=int(size * 0.045), fill=(0, 196, 220, 255))
    draw.rounded_rectangle(right_box, radius=int(size * 0.045), fill=(232, 74, 86, 255))
    center_w = gap
    draw.rounded_rectangle(
        (cx - center_w // 2, cy - joy_h // 2, cx + center_w // 2, cy + joy_h // 2),
        radius=int(size * 0.012),
        fill=(16, 18, 24, 255),
    )
    dot = int(size * 0.018)
    for ox, oy in ((-0.055, -0.10), (-0.055, 0.02), (0.055, -0.10), (0.055, 0.02)):
        px, py = int(size * (0.5 + ox)), int(size * (0.52 + oy))
        draw.ellipse((px - dot, py - dot, px + dot, py + dot), fill=(20, 22, 28, 255))
    try:
        font = _font(int(size * 0.18))
        draw.text((cx, int(size * 0.78)), "2", font=font, fill=(235, 238, 246, 255), anchor="mm")
    except Exception:
        pass
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    base = load_base(512)
    switch2 = base if base is not None else draw_discord(512)
    switch2.save(ASSETS / "switch2.png")
    switch2.resize((256, 256), Image.LANCZOS).save(
        ASSETS / "switch2.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    tray_base = load_base(64)
    for name, color in STATE_COLORS.items():
        if tray_base is not None:
            add_state_dot(tray_base, color).save(ASSETS / f"{name}.png")
        else:
            draw_switch(64, dot=color).save(ASSETS / f"{name}.png")
    print(f"Wrote assets to {ASSETS}")


if __name__ == "__main__":
    main()
