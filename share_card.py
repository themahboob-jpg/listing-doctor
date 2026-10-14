"""On-the-fly social share images for Listing Doctor (Pillow).

Cards render deterministically from query params — nothing is stored.
1200x630, matching the site's dark + purple/cyan theme.
"""
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG = (8, 8, 15)
PURPLE = (168, 85, 247)
CYAN = (34, 211, 238)
LIME = (163, 230, 53)
ORANGE = (251, 146, 60)
RED = (239, 68, 68)
WHITE = (242, 244, 250)
MUTED = (154, 163, 189)
TRACK = (28, 28, 40)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _font(size, bold=True):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)


def band_color(score):
    if score >= 80:
        return LIME
    if score >= 60:
        return CYAN
    if score >= 40:
        return ORANGE
    return RED


def _base():
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([-220, -180, 520, 430], fill=PURPLE + (30,))
    g.ellipse([760, -140, 1420, 490], fill=CYAN + (24,))
    g.ellipse([260, 470, 940, 920], fill=PURPLE + (16,))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(img)
    for x in range(W):  # bottom gradient bar, purple -> cyan
        t = x / max(W - 1, 1)
        c = tuple(int(PURPLE[i] + (CYAN[i] - PURPLE[i]) * t) for i in range(3))
        d.line([(x, H - 14), (x, H)], fill=c)
    return img, d


def _center(d, cx, y, text, font, fill):
    bb = d.textbbox((0, 0), text, font=font)
    d.text((cx - (bb[2] - bb[0]) / 2, y), text, font=font, fill=fill)


def _fit(font_path_size, text, max_w, bold=True):
    size = font_path_size
    while size > 20:
        f = _font(size, bold)
        if ImageDraw.Draw(Image.new("RGB", (8, 8))).textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return _font(20, bold)


def _wrap(d, text, font, max_w, max_lines=2):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == max_lines - 1:
                # last line: squeeze the rest in with an ellipsis
                rest = " ".join(words[words.index(w):])
                while rest and d.textlength(rest + "…", font=font) > max_w:
                    rest = rest[:-1].rstrip()
                lines.append((rest + "…") if rest else w)
                return lines
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def make_score_card(score, title):
    """Personal score card: ring dial + score, listing title, hook."""
    img, d = _base()
    ring = band_color(score)

    _center(d, W / 2, 62, "LISTING DOCTOR  ·  FREE EBAY AUDIT", _font(28), MUTED)

    # dial ring with score arc
    cx, cy, r, lw = W / 2, 268, 118, 24
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=TRACK, width=lw)
    if score > 0:
        d.arc([cx - r, cy - r, cx + r, cy + r], start=-90,
              end=-90 + 360 * score / 100, fill=ring, width=lw)
    num, suf = str(score), "/100"
    f1, f2 = _font(132), _font(52)
    w1 = d.textlength(num, font=f1)
    w2 = d.textlength(suf, font=f2)
    x0 = cx - (w1 + 10 + w2) / 2
    d.text((x0, cy - 88), num, font=f1, fill=ring)
    d.text((x0 + w1 + 10, cy - 22), suf, font=f2, fill=MUTED)

    # listing title, up to 2 lines
    if title:
        tf = _font(40)
        y = 428
        for line in _wrap(d, title, tf, W - 160):
            _center(d, W / 2, y, line, tf, WHITE)
            y += 52

    _center(d, W / 2, 540, "Is YOUR eBay listing losing sales?",
            _font(34), WHITE)
    return _png(img)


def make_home_banner():
    """Static OG banner for the homepage."""
    img, d = _base()
    _center(d, W / 2, 120, "FREE EBAY LISTING TOOLKIT", _font(30), MUTED)
    _center(d, W / 2, 190, "Is Your eBay Listing",
            _fit(92, "Is Your eBay Listing", W - 120), WHITE)
    _center(d, W / 2, 300, "Losing Sales?",
            _fit(92, "Losing Sales?", W - 120), CYAN)
    _center(d, W / 2, 440, "Free 0–100 health score  ·  No signup  ·  No credit card",
            _fit(36, "Free 0–100 health score  ·  No signup  ·  No credit card",
                 W - 120, bold=False), MUTED)
    return _png(img)


def _png(img):
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    open("static/og-banner.png", "wb").write(make_home_banner())
    open("/tmp/demo-card.png", "wb").write(
        make_score_card(68, "Apple iPhone 13 128GB Midnight Unlocked Excellent Condition (demo listing)"))
    print("wrote static/og-banner.png and /tmp/demo-card.png")
