"""Server-side render of the Viridian share card (the clean 1200x630 grade image).

This is a Python/Pillow port of the client-side canvas in ``web/static/sharecard.js``
so the Discord bot and the webhook mirror can post the *same* polished card image
instead of a plain text embed. Keep the two in visual sync if either changes.

Public API: ``render(result) -> bytes | None`` — PNG bytes for an identified grade,
or ``None`` when the result isn't shareable (no card / image), so callers fall back
to the text embed.
"""
from __future__ import annotations

import io
from functools import lru_cache
from typing import Optional

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import config

FONTS_DIR = config.ROOT / "assets" / "fonts"

# palette (matches sharecard.js)
ACCENT = (45, 212, 160)
GOLD = (232, 200, 120)
MUTE = (159, 176, 169)
INK = (234, 242, 238)
DIM = (214, 222, 217)

GRADE_NAMES = {
    10: "Gem Mint", 9: "Mint", 8: "NM-MT", 7: "Near Mint", 6: "EX-MT",
    5: "Excellent", 4: "VG-EX", 3: "Very Good", 2: "Good", 1: "Poor",
}

W, H = 1200, 630


def _grade_color(g) -> tuple:
    if g is None:
        return MUTE
    return GOLD if g >= 9 else ACCENT if g >= 7 else DIM


@lru_cache(maxsize=32)
def _font(family: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS_DIR / f"{family}.ttf"), size)
    try:
        axes = f.get_variation_axes()
        vals = []
        for ax in axes:
            name = ax.get("name", b"")
            name = name.decode() if isinstance(name, bytes) else str(name)
            lo, hi = ax.get("minimum", 0), ax.get("maximum", 1000)
            n = name.lower()
            if "weight" in n:
                vals.append(max(lo, min(hi, weight)))
            elif "optical" in n:
                vals.append(max(lo, min(hi, size)))
            else:
                vals.append(ax.get("default", lo))
        f.set_variation_by_axes(vals)
    except Exception:
        pass
    return f


def sg(size, weight=600):
    return _font("SpaceGrotesk", size, weight)


def inter(size, weight=500):
    return _font("Inter", size, weight)


def mono(size, weight=600):
    # Space Mono is static (no variable axis); pick the bold file for heavier weights.
    return _font("SpaceMono-Bold" if weight >= 600 else "SpaceMono", size, weight)


def _money(n) -> str:
    return f"${float(n):,.2f}"


def _fit(draw, text, font, maxw) -> str:
    if draw.textlength(text, font=font) <= maxw:
        return text
    s = text
    while len(s) > 1 and draw.textlength(s + "…", font=font) > maxw:
        s = s[:-1]
    return s + "…"


def _background() -> Image.Image:
    """Diagonal dark-green gradient + a soft top-left accent glow (numpy)."""
    tl, br = np.array([10, 19, 15]), np.array([15, 28, 23])
    mid = (tl + br) / 2
    corners = np.array([[tl, mid], [mid, br]], dtype=np.float64)  # 2x2 -> upscaled
    grad = np.asarray(Image.fromarray(corners.astype(np.uint8)).resize((W, H), Image.BICUBIC),
                      dtype=np.float64)
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt((xx - 120) ** 2 + (yy - 90) ** 2)
    a = np.clip(1 - dist / 520, 0, 1)[..., None] * 0.16
    out = grad * (1 - a) + np.array(ACCENT, dtype=np.float64) * a
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def _card_image(url: str, cw: int, ch: int, radius: int) -> Optional[Image.Image]:
    """Download + cover-fit the card art into cw x ch with rounded corners (RGBA)."""
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "viridian-sharecard/1.0"})
        if r.status_code != 200:
            return None
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        return None
    ar, box = img.width / img.height, cw / ch
    if ar > box:                       # cover: match height, crop width
        dh, dw = ch, int(round(ch * ar))
    else:                              # match width, crop height
        dw, dh = cw, int(round(cw / ar))
    img = img.resize((dw, dh), Image.LANCZOS)
    img = img.crop(((dw - cw) // 2, (dh - ch) // 2, (dw - cw) // 2 + cw, (dh - ch) // 2 + ch))
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw - 1, ch - 1], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def _norm(result: dict) -> Optional[dict]:
    match = result.get("match") or {}
    card = match.get("card") or {}
    if not card.get("id") or not card.get("image"):
        return None
    g = result.get("grade") or {}
    slab = result.get("slab") or {}
    is_slab = bool(slab and slab.get("grade") is not None)
    overall = slab.get("grade") if is_slab else g.get("overall")
    value = result.get("value") or {}
    usd = next((v for v in (value.get("values") or [])
                if v.get("currency") == "USD" or v.get("symbol") == "$"), {})
    cen = g.get("centering") or {}
    sub = " · ".join(x for x in [card.get("set"),
          ("#" + str(card["number"])) if card.get("number") else None] if x)
    return {
        "name": card.get("name"), "image": card.get("image"), "grade": overall,
        "slab": is_slab, "sub": sub,
        "rawUsd": usd.get("raw"), "gradedUsd": usd.get("graded"),
        "gradedReal": value.get("graded_real") is True,
        "subs": [("Cen", cen.get("grade") if cen.get("ok") else None),
                 ("Cor", (g.get("corners") or {}).get("grade")),
                 ("Edg", (g.get("edges") or {}).get("grade")),
                 ("Sur", (g.get("surface") or {}).get("grade"))],
    }


def render(result: dict) -> Optional[bytes]:
    if not result or not result.get("ok"):
        return None
    norm = _norm(result)
    if norm is None:
        return None

    base = _background()
    ch, cw, cx, cy, rad = 490, round(490 * 5 / 7), 64, 70, 18

    # card art with drop shadow
    card = _card_image(norm["image"], cw, ch, rad)
    if card is not None:
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ImageDraw.Draw(sd).rounded_rectangle([0, 0, cw - 1, ch - 1], radius=rad, fill=(0, 0, 0, 140))
        shadow.paste(sd, (cx, cy + 18), sd)
        shadow = shadow.filter(ImageFilter.GaussianBlur(20))
        base = Image.alpha_composite(base, shadow)
        base.paste(card, (cx, cy), card)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    if card is not None:                        # hairline border on the card
        od.rounded_rectangle([cx, cy, cx + cw - 1, cy + ch - 1], radius=rad,
                             outline=(255, 255, 255, 26), width=2)

    d = ImageDraw.Draw(base)
    x0 = cx + cw + 56
    colW = W - x0 - 64

    def text(xy, s, font, fill):
        d.text(xy, s, font=font, fill=fill, anchor="ls")

    text((x0, cy + 22), "VIRIDIAN GRADING LAB", sg(22, 600), ACCENT)

    gc = _grade_color(norm["grade"])
    text((x0, cy + 86), "PSA", sg(26, 600), MUTE)
    num = "—" if norm["grade"] is None else str(norm["grade"])
    text((x0 - 4, cy + 210), num, sg(150, 700), gc)
    num_w = d.textlength(num, font=sg(150, 700))
    gname = "Graded slab" if norm["slab"] else GRADE_NAMES.get(norm["grade"], "Ungraded")
    text((x0 + num_w + 24, cy + 150), gname, sg(40, 600), INK)
    text((x0 + num_w + 26, cy + 188),
         "verified grade" if norm["slab"] else "estimated grade", inter(22, 500), MUTE)

    text((x0, cy + 300), _fit(d, norm["name"] or "Pokémon card", sg(46, 600), colW),
         sg(46, 600), INK)
    if norm["sub"]:
        text((x0, cy + 338), _fit(d, norm["sub"], inter(26, 500), colW), inter(26, 500), MUTE)

    # subgrade chips
    chip_x, chip_y = x0, cy + 366
    cf = mono(22, 600)
    for label, val in norm["subs"]:
        txt = f"{label} {'—' if val is None else val}"
        w = d.textlength(txt, font=cf) + 30
        if chip_x + w > x0 + colW:
            break
        od.rounded_rectangle([chip_x, chip_y, chip_x + w, chip_y + 40], radius=12,
                             fill=(255, 255, 255, 13), outline=(255, 255, 255, 26), width=1)
        d.text((chip_x + 15, chip_y + 27), txt, font=cf, fill=(205, 216, 210), anchor="ls")
        chip_x += w + 12

    # value line
    vy = cy + 452
    if norm["rawUsd"] is not None or norm["gradedUsd"] is not None:
        vx = x0
        if norm["rawUsd"] is not None:
            text((vx, vy), "Raw ", inter(27, 500), MUTE)
            vx += d.textlength("Raw ", font=inter(27, 500))
            rs = _money(norm["rawUsd"])
            text((vx, vy), rs, inter(27, 500), INK)
            vx += d.textlength(rs, font=inter(27, 500)) + 22
        if norm["gradedUsd"] is not None:
            lbl = "Graded " if norm["gradedReal"] else "PSA 10 est. "
            text((vx, vy), lbl, inter(27, 500), MUTE)
            vx += d.textlength(lbl, font=inter(27, 500))
            text((vx, vy), _money(norm["gradedUsd"]), inter(27, 600), gc)

    text((x0, cy + 500), "Grade your card free — viridian", inter(22, 500), MUTE)

    base = Image.alpha_composite(base, overlay)
    buf = io.BytesIO()
    base.convert("RGB").save(buf, "PNG")
    return buf.getvalue()
