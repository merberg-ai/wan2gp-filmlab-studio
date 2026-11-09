"""Draws a Storybook (storybook.py) as pages with Pillow: a cover, then picture-book pages (one picture, narration and
dialogue below) or comic pages (panels with caption boxes and speech bubbles). Writes page-NNN.png, book.pdf,
pages.zip (the PNGs) and book.cbz (JPEG pages, for comic readers). Pages are A4 portrait at 150 dpi."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1240, 1754
PAPER, INK, MUTED = (247, 242, 230), (30, 27, 24), (120, 112, 100)
CAPTION_BG, BUBBLE_BG = (255, 241, 186), (255, 255, 255)
FONT_DIRS = (Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu"), Path("C:/Windows/Fonts"))
FONTS = {"serif": ("DejaVuSerif.ttf", "georgia.ttf"), "serif_bold": ("DejaVuSerif-Bold.ttf", "georgiab.ttf"),
         "serif_italic": ("DejaVuSerif-Italic.ttf", "georgiai.ttf", "DejaVuSerif.ttf"),  # core DejaVu has no italic
         "sans": ("DejaVuSans.ttf", "arial.ttf"),
         "sans_bold": ("DejaVuSans-Bold.ttf", "arialbd.ttf")}
_cache: dict = {}


def font(kind: str, size: int):
    key = (kind, size)
    if key not in _cache:
        f = None
        for name in FONTS[kind]:
            for d in FONT_DIRS:
                if (d / name).is_file():
                    f = ImageFont.truetype(str(d / name), size)
                    break
            if f:
                break
        _cache[key] = f or ImageFont.load_default(size)
    return _cache[key]


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines: list[str] = []
    for para in str(text).split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=f) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return [ln for ln in lines if ln] or [""]


def fit_text(draw, text, kind, width, height, start, smallest, spacing=1.3):
    """The largest size from start down to smallest at which text fits the box; (font, lines, line height)."""
    size = start
    while True:
        f = font(kind, size)
        lines = wrap(draw, text, f, width)
        lh = int(size * spacing)
        if len(lines) * lh <= height or size <= smallest:
            if len(lines) * lh > height:  # still too long: cut it
                lines = lines[:max(1, height // lh)]
                lines[-1] = lines[-1].rstrip(".,; ") + "…"
            return f, lines, lh
        size -= 2


def cover_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img, (w, h), Image.LANCZOS, centering=(0.5, 0.45))


def contain(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.contain(img, (w, h), Image.LANCZOS)


def page_number(draw, n: int):
    f = font("serif", 22)
    t = str(n)
    draw.text(((W - draw.textlength(t, font=f)) / 2, H - 60), t, font=f, fill=MUTED)


def dialogue_text(lines: list[dict]) -> str:
    return "\n".join(f"{ln['speaker'] + ': ' if ln['speaker'] else ''}“{ln['text']}”" for ln in lines)


# --- pages ------------------------------------------------------------------------------------------------

def draw_cover(img_path: Path, cover: dict, logline: str) -> Image.Image:
    page = cover_fit(Image.open(img_path).convert("RGB"), W, H)
    shade = Image.new("L", (1, 256))
    for y in range(256):
        shade.putpixel((0, y), int(max(0, (y - 120) / 136) ** 1.4 * 235))
    band = Image.new("RGB", (W, H), (8, 8, 10))
    page = Image.composite(band, page, shade.resize((W, H)))
    d = ImageDraw.Draw(page)
    title = cover["title"] or "Untitled"
    f, lines, lh = fit_text(d, title, "serif_bold", W - 200, 330, 104, 48, 1.12)
    sub = cover.get("subtitle") or logline
    subf = font("serif_italic", 34)
    sub_lines = wrap(d, sub, subf, W - 240)[:3] if sub else []
    by = f"by {cover['author']}" if cover.get("author") else ""
    total = len(lines) * lh + (len(sub_lines) * 46 + 30 if sub_lines else 0) + (70 if by else 0)
    y = H - 140 - total
    for ln in lines:
        d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=(250, 246, 236))
        y += lh
    y += 30 if sub_lines else 0
    for ln in sub_lines:
        d.text(((W - d.textlength(ln, font=subf)) / 2, y), ln, font=subf, fill=(225, 218, 200))
        y += 46
    if by:
        bf = font("sans", 28)
        d.text(((W - d.textlength(by, font=bf)) / 2, y + 30), by.upper(), font=bf, fill=(217, 164, 65))
    return page


def chapter_head(d, chapter: str, y: int, number: int | None) -> int:
    if number is not None:
        lab = f"CHAPTER {number}"
        f = font("sans", 22)
        d.text(((W - d.textlength(lab, font=f)) / 2, y), lab, font=f, fill=MUTED)
        y += 40
    f, lines, lh = fit_text(d, chapter, "serif_bold", W - 240, 130, 52, 32, 1.15)
    for ln in lines:
        d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=INK)
        y += lh
    d.line([(W / 2 - 60, y + 18), (W / 2 + 60, y + 18)], fill=(217, 164, 65), width=3)
    return y + 50


def draw_picture_page(panel: dict, img_path: Path, chapter: str | None, chapter_no: int | None, n: int) -> Image.Image:
    page = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(page)
    top = chapter_head(d, chapter, 90, chapter_no) + 10 if chapter else 110
    bottom = H - 120
    margin, tw = 100, W - 2 * 100 - 40
    words = panel["narration"]
    talk = dialogue_text(panel["lines"]) if panel["dialogue"] and panel["lines"] else ""
    has_text = bool(words or talk)
    pic = contain(Image.open(img_path).convert("RGB"), W - 2 * margin, bottom - top - (380 if has_text else 0))
    gap = 56 if has_text else 0
    lines, f, lh, talk_from = [], None, 0, 0
    if has_text:
        body = words + ("\n" if words and talk else "") + talk
        # Larger type when there's room (a short line under a wide picture), never past the page.
        f, lines, lh = fit_text(d, body, "serif", tw, bottom - top - pic.height - gap, 40, 20, 1.42)
        talk_from = len(wrap(d, words, f, tw)) if words else 0
    block = pic.height + gap + len(lines) * lh
    y = top + max(0, (bottom - top - block) // 3)  # a little above the middle of the free space
    px = (W - pic.width) // 2
    d.rectangle([px - 2, y - 2, px + pic.width + 1, y + pic.height + 1], outline=(200, 190, 170), width=2)
    page.paste(pic, (px, y))
    y += pic.height + gap
    if lines:
        ital = font("serif_italic", f.size) if hasattr(f, "size") else f
        for i, ln in enumerate(lines):
            said = i >= talk_from
            d.text((margin + 20, y), ln, font=ital if said else f, fill=(84, 72, 60) if said else INK)
            y += lh
    page_number(d, n)
    return page


def comic_rows(n: int, portrait: bool) -> list[int]:
    if portrait:
        return {1: [1], 2: [2], 3: [3], 4: [2, 2], 5: [3, 2], 6: [3, 3]}[n]
    return {1: [1], 2: [1, 1], 3: [1, 1, 1], 4: [1, 2, 1], 5: [1, 2, 2], 6: [2, 2, 2]}[n]


def draw_caption(page, d, text: str, x: int, y: int, max_w: int, max_h: int):
    f, lines, lh = fit_text(d, text, "sans", max_w - 28, max_h - 20, 26, 15, 1.25)
    w = max(d.textlength(ln, font=f) for ln in lines) + 28
    h = len(lines) * lh + 18
    d.rectangle([x, y, x + w, y + h], fill=CAPTION_BG, outline=INK, width=3)
    for i, ln in enumerate(lines):
        d.text((x + 14, y + 9 + i * lh), ln, font=f, fill=INK)


def draw_bubble(d, line: dict, x: int, y_bottom: int, max_w: int, max_h: int, tail_right: bool):
    who = (line["speaker"] or "").upper()
    f, lines, lh = fit_text(d, line["text"], "sans_bold", max_w - 40, max_h - 40, 26, 15, 1.22)
    wf = font("sans", 17)
    w = max([d.textlength(ln, font=f) for ln in lines] + [d.textlength(who, font=wf)]) + 44
    h = len(lines) * lh + (26 if who else 0) + 28
    if tail_right:  # right-aligned in its box
        x = int(x + max(0, max_w - w))
    top = y_bottom - h - 26
    d.rounded_rectangle([x, top, x + w, top + h], radius=min(30, int(h / 2)), fill=BUBBLE_BG, outline=INK, width=3)
    tx = x + (w * 0.7 if tail_right else w * 0.3)
    d.polygon([(tx - 14, top + h - 2), (tx + 14, top + h - 2), (tx + (18 if tail_right else -18), top + h + 24)],
              fill=BUBBLE_BG, outline=INK)
    d.line([(tx - 12, top + h - 1), (tx + 12, top + h - 1)], fill=BUBBLE_BG, width=4)
    yy = top + 14
    if who:
        d.text((x + 22, yy), who, font=wf, fill=MUTED)
        yy += 26
    for ln in lines:
        d.text((x + 22, yy), ln, font=f, fill=INK)
        yy += lh


def draw_comic_page(panels: list[dict], pics: list[Path], chapter: str | None, portrait: bool, n: int) -> Image.Image:
    page = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(page)
    m, gut, top = 56, 22, 56
    if chapter:
        f = font("serif_bold", 44)
        d.text((m, 48), chapter, font=f, fill=INK)
        d.line([(m, 112), (W - m, 112)], fill=INK, width=3)
        top = 136
    rows = comic_rows(len(panels), portrait)
    row_h = (H - top - 100 - gut * (len(rows) - 1)) // len(rows)
    k, y = 0, top
    for cols in rows:
        cw = (W - 2 * m - gut * (cols - 1)) // cols
        x = m
        for _ in range(cols):
            p, path = panels[k], pics[k]
            cell = cover_fit(Image.open(path).convert("RGB"), cw, row_h)
            page.paste(cell, (x, y))
            d.rectangle([x, y, x + cw - 1, y + row_h - 1], outline=INK, width=5)
            if p["narration"]:
                draw_caption(page, d, p["narration"], x + 14, y + 14, int(cw * 0.72), int(row_h * 0.38))
            if p["dialogue"] and p["lines"]:
                talk = p["lines"][:2]
                bw = int(cw * (0.6 if len(talk) == 1 else 0.48))
                for i, ln in enumerate(talk):
                    right = i == 1
                    bx = x + cw - bw - 16 if right else x + 16
                    draw_bubble(d, ln, bx, y + row_h - 18, bw, int(row_h * 0.42), right)
            x += cw + gut
            k += 1
        y += row_h + gut
    page_number(d, n)
    return page


def render_book(view: dict, pictures: dict[str, Path], cover: Path, folder: Path) -> int:
    """Draw every page into folder; returns the number of pages (with the cover)."""
    doc = view["doc"]
    by_id = {p["shot_id"]: p for p in view["panels"]}
    pages = [draw_cover(cover, doc["cover"], view.get("logline") or "")]
    chapter_no = 0
    for i, pg in enumerate(view["pages"]):
        if pg["chapter"]:
            chapter_no += 1
        n = i + 2
        panels = [by_id[s] for s in pg["shot_ids"]]
        if doc["layout"] == "picture":
            pages.append(draw_picture_page(panels[0], pictures[panels[0]["shot_id"]], pg["chapter"],
                                           chapter_no if pg["chapter"] else None, n))
        else:
            pages.append(draw_comic_page(panels, [pictures[p["shot_id"]] for p in panels], pg["chapter"],
                                         view.get("portrait", False), n))
    from .storybook import zip_pages
    pngs, jpgs = [], []
    for i, pg in enumerate(pages, 1):
        png = folder / f"page-{i:03d}.png"
        pg.save(png, optimize=True)
        jpg = folder / f"page-{i:03d}.jpg"
        pg.save(jpg, quality=90)
        pngs.append(png)
        jpgs.append(jpg)
    pages[0].save(folder / "book.pdf", save_all=True, append_images=pages[1:], resolution=150.0,
                  title=doc["cover"]["title"] or "Storybook", author=doc["cover"].get("author") or "")
    zip_pages(folder, pngs, jpgs)
    for j in jpgs:
        j.unlink()
    return len(pages)
