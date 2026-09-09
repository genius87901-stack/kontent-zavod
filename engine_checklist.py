# -*- coding: utf-8 -*-
"""
ЧЕК-ЛИСТ (Checklist · Save-bait) — стиль 09 из библиотеки 18 (15.08.2026,
референс karusel-styles-library.html). Нумерованные/отмеченные пункты, вид
«рабочей тетради», чекбоксы, прогресс «k из N». Заточен под сохранения.

АКЦЕНТ. Референс просит синий (заголовок) + зелёный (галочка выполнено) —
здесь единственный акцент держит и заголовок, и заливку отмеченного чекбокса
(тот же принцип «Развилки»/«Цифры»).

ШРИФТ. Rubik (600) + Golos Text (400) — оба уже вендорены, новых шрифтов не
требует.
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

BG = (251, 250, 247)     # #FBFAF7
INK = (28, 30, 34)       # #1C1E22
MUTED = (150, 146, 140)
LINE = (226, 222, 216)

_RUBIK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Rubik")
_GOLOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "GolosText",
                      "GolosText-Variable.ttf")


def rubik(weight, size):
    name = "Rubik-Bold.ttf" if weight == "Bold" else "Rubik-Regular.ttf"
    return ImageFont.truetype(os.path.join(_RUBIK_DIR, name), size)


def golos(size, weight=500):
    f = ImageFont.truetype(_GOLOS, size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def _wrap(d, text, f, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        probe = (cur + " " + w).strip()
        if d.textlength(probe, font=f) <= maxw or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _pageno(d, i, n):
    f = golos(20, 700)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=f), 58), t, font=f, fill=MUTED)


def cover(title, count_label, photo_key, acc, out, i=1, n=5):
    """Обложка «Чек-лист: N пунктов»: фото приглушено, чип с числом сверху,
    заголовок-обещание внизу."""
    base = e.fit(e.PHOTOS[photo_key], H)
    img = Image.eval(base, lambda p: int(p * 0.72)).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top = int(H * 0.55)
    for yy in range(top, H):
        a = int(200 * min(1, (yy - top) / (H * 0.45)))
        od.line([(0, yy), (W, yy)], fill=(10, 10, 10, min(a, 200)))
    img = Image.alpha_composite(img, ov).convert("RGB")
    d = ImageDraw.Draw(img)
    _pageno(d, i, n)

    if count_label:
        fc = golos(20, 700)
        cw = d.textlength(count_label, font=fc)
        d.rounded_rectangle([M, 52, M + cw + 32, 92], 20, fill=acc)
        d.text((M + 16, 62), count_label, font=fc, fill=(255, 255, 255))

    bw = W - 2 * M
    fh = rubik("Bold", 56)
    lines = _wrap(d, title, fh, bw)[:3]
    lh = int(56 * 1.24)
    top_y = y = H - 90 - len(lines) * lh
    for line in lines:
        d.text((M, y), line, font=fh, fill=(255, 255, 255))
        y += lh

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top_y, W - M, y)}


def items_slide(items, progress, acc, out, i=2, n=5):
    """Блок пунктов с чекбоксами и прогрессом «k из N» — рабочая тетрадь,
    не карточка. items — список (текст, done: bool); progress — «3 из 12»
    строкой (может быть шире, чем len(items), если пункты идут по слайдам)."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _pageno(d, i, n)

    fp = golos(22, 600)
    d.text((M, 58), progress.upper(), font=fp, fill=acc)

    fi = golos(30, 500)
    box = 40
    row_gap = 26
    ind = box + 24

    top = y0 = H * 0.30
    y = y0
    for it, checked in items:
        d.rounded_rectangle([M, y, M + box, y + box], 8,
                            outline=acc, width=2,
                            fill=acc if checked else None)
        if checked:
            d.line([(M + 10, y + 21), (M + 17, y + 29), (M + 31, y + 12)],
                  fill=(255, 255, 255), width=3, joint="curve")
        lines = _wrap(d, it, fi, W - 2 * M - ind)
        for j, line in enumerate(lines):
            d.text((M + ind, y + j * 38), line, font=fi,
                  fill=INK if not checked else MUTED)
        row_h = max(box, len(lines) * 38)
        y += row_h + row_gap
        d.line([(M, y - row_gap // 2), (W - M, y - row_gap // 2)], fill=LINE, width=1)

    img.save(out)
    return {"text_bbox": (M, top, W - M, y - row_gap)}


def outro(prompt, save_hint, acc, out, i=5, n=5, brand_line=""):
    """Финал: «сохрани, чтобы не потерять» + мягкий призыв."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _pageno(d, i, n)

    bw = W - 2 * M
    fh = rubik("Bold", 46)
    lines = _wrap(d, prompt, fh, bw)
    lh = int(46 * 1.28)
    block_h = len(lines) * lh + (46 if save_hint else 0)
    top = y = (H - block_h) // 2
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh
    bottom = y
    if save_hint:
        fs = golos(26, 600)
        y += 14
        d.text((M, y), save_hint, font=fs, fill=acc)
        bottom = y + 34

    if brand_line:
        fb = golos(18, 700)
        d.text((M, H - 60), brand_line.upper(), font=fb, fill=MUTED)

    img.save(out)
    return {"text_bbox": (M, top, W - M, bottom)}
