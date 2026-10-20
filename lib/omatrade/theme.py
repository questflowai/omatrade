"""The palette, derived from the current Omarchy theme and checked for
contrast against the panel's ground (unchanged from 0.2)."""

import tomllib
from pathlib import Path

THEME_DIR = Path.home() / ".local/state/omarchy/current/theme"

def hex_rgb(value):
    v = value.strip().lstrip("#")[:6]
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a, b, t):
    ra, rb = hex_rgb(a), hex_rgb(b)
    return rgb_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def luminance(value):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in hex_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def legible(color, ground, toward, minimum):
    """Nudge a colour toward `toward` until it reads on `ground`."""
    out = color
    for step in range(1, 11):
        if contrast(out, ground) >= minimum:
            return out
        out = mix(color, toward, step / 10)
    return out


def theme_colors(theme_dir=None):
    raw = {}
    try:
        raw = tomllib.loads((Path(theme_dir or THEME_DIR) / "colors.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        pass
    pick = lambda *keys, default: next((raw[k] for k in keys if isinstance(raw.get(k), str)), default)

    bg = pick("background", "color0", default="#101315")
    fg = pick("foreground", "color7", default="#cacccc")
    up = legible(pick("green", "color2", default="#62b36c"), bg, fg, 3.0)
    down = legible(pick("red", "color1", default="#d35d5d"), bg, fg, 3.0)
    accent = legible(pick("accent", "blue", "color4", default=fg), bg, fg, 3.0)
    muted = legible(mix(fg, bg, 0.42), bg, fg, 3.0)

    def tag(fill):
        label = bg if contrast(bg, fill) >= contrast(fg, fill) else fg
        return {"fill": fill, "label": label}

    return {
        "text": fg,
        "muted": muted,
        "up": up,
        "down": down,
        "flat": muted,
        "accent": accent,
        "hover": mix(bg, fg, 0.08),
        "rule": mix(bg, fg, 0.14),
        "tags": {"up": tag(up), "down": tag(down), "flat": tag(mix(bg, fg, 0.18)), "accent": tag(accent)},
    }
