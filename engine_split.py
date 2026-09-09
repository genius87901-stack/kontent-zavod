# -*- coding: utf-8 -*-
"""
РАЗВИЛКА (Split Contrast) — стиль 06 из библиотеки 18 (15.08.2026, референс
karusel-styles-library.html). Экран разбит пополам: слева «как все / было»,
справа «как надо / стало». Визуальный контраст понятен до чтения.

АКЦЕНТ. Референс просит ДВА цвета одновременно (зелёный #2FB574 «стало» +
красный #E5484D «было») — прямое нарушение правила завода «один акцент на
карусель» (checks.check_palette). Адаптация: правая сторона («стало») —
единственный зарегистрированный акцент карусели, левая («было») — приглушённый
серый (INK с низкой яркостью, вне сети проверки цвета, как у остальных
семейств). Контраст держат ВЕС и цвет-против-серого, не два ярких цвета —
тот же принцип различения, без второго глобального акцента.

ШРИФТ. Unbounded (уже вендорен под «Бетон») + Golos Text (уже вендорен).
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

BG = (242, 240, 235)     # #F2F0EB
INK = (23, 23, 26)       # #17171A
LEFT = (150, 146, 140)   # приглушённый серый — сторона «как все»

_UNB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Unbounded")
_GOLOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "GolosText",
                      "GolosText-Variable.ttf")


def unbounded(weight, size):
    name = "Unbounded-Black.ttf" if weight == "Black" else "Unbounded-Bold.ttf"
    return ImageFont.truetype(os.path.join(_UNB_DIR, name), size)


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


def _fit(d, text, maxw, hi=64, lo=38, max_lines=3):
    for size in range(hi, lo - 1, -2):
        f = unbounded("Bold", size)
        lines = _wrap(d, text.upper(), f, maxw)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.22)
    f = unbounded("Bold", lo)
    return f, _wrap(d, text.upper(), f, maxw), int(lo * 1.22)


def cover(hook, photo_key, acc, out, i=1, n=4):
    """Обложка «как все vs как надо»: фото приглушено, заголовок-развилка
    внизу, тонкое подчёркивание акцентом."""
    base = e.fit(e.PHOTOS[photo_key], H)
    img = base.convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top = int(H * 0.55)
    for yy in range(top, H):
        a = int(215 * min(1, (yy - top) / (H * 0.45)))
        od.line([(0, yy), (W, yy)], fill=(15, 15, 14, min(a, 215)))
    img = Image.alpha_composite(img, ov).convert("RGB")
    d = ImageDraw.Draw(img)

    fe = golos(22, 700)
    d.text((M, H - 260), "КАК ВСЕ VS КАК НАДО", font=fe, fill=(230, 228, 222))
    fst = e.font("SemiBold", 22)
    d.text((W - M - d.textlength("%d/%d" % (i, n), font=fst), 58),
           "%d/%d" % (i, n), font=fst, fill=(255, 255, 255))

    bw = W - 2 * M
    fh, lines, lh = _fit(d, hook, bw, hi=58, lo=38, max_lines=3)
    top = y = H - 200 - len(lines) * lh
    for line in lines:
        d.text((M, y), line, font=fh, fill=(255, 255, 255))
        y += lh
    d.rectangle([M, y + 10, M + 90, y + 16], fill=acc)

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, y + 16)}


def split_row(bad, good, acc, out, i=2, n=4, section=""):
    """Один ряд различия: слева приглушённо («как все»), справа акцентом
    («как надо») — по одному различию на слайд."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if section:
        fl = e.font("SemiBold", 20)
        d.text((M, 58), section.upper(), font=fl, fill=LEFT)
    fst = e.font("SemiBold", 22)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=fst), 58), t, font=fst, fill=INK)

    colw = (W - 2 * M - 40) // 2
    fh = unbounded("Bold", 34)
    row = 46

    bad_lines = _wrap(d, bad.upper(), fh, colw)
    good_lines = _wrap(d, good.upper(), fh, colw)
    n_rows = max(len(bad_lines), len(good_lines))
    block_h = 40 + n_rows * row
    top = y0 = (H - block_h) // 2

    # левая колонка — «как все»
    d.text((M, y0 - 40), "КАК ВСЕ", font=e.font("SemiBold", 20), fill=LEFT)
    for k, line in enumerate(bad_lines):
        d.text((M, y0 + k * row), line, font=fh, fill=LEFT)

    # разделитель — по фактической высоте блока, не «на глаз»
    d.line([(W // 2, y0 - 60), (W // 2, y0 + block_h - 20)],
           fill=(210, 206, 198), width=2)

    x2 = M + colw + 40
    d.text((x2, y0 - 40), "КАК НАДО", font=e.font("SemiBold", 20), fill=acc)
    for k, line in enumerate(good_lines):
        d.text((x2, y0 + k * row), line, font=fh, fill=INK)
    d.rectangle([x2, y0 - 52, x2 + 50, y0 - 46], fill=acc)

    bottom = y0 + n_rows * row
    img.save(out)
    return {"text_bbox": (M, top - 60, W - M, bottom)}


def final(text, cta_text, acc, out, i=4, n=4, brand_line=""):
    """«Выбор очевиден» + CTA: правая (акцентная) половина побеждает —
    заливка-плашка акцентом на всю ширину, тёмный текст поверх."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    fst = e.font("SemiBold", 22)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=fst), 58), t, font=fst, fill=INK)

    bw = W - 2 * M
    fh, lines, lh = _fit(d, text, bw, hi=54, lo=36, max_lines=3)
    cta_h = 96 + 50 if cta_text else 0
    block_h = len(lines) * lh + cta_h
    top = y = (H - block_h) // 2
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh
    bottom = y

    if cta_text:
        block_h2 = 96
        by = y + 50
        d.rectangle([M, by, W - M, by + block_h2], fill=acc)
        fc = golos(30, 700)
        cw = d.textlength(cta_text, font=fc)
        d.text(((W - cw) // 2, by + 30), cta_text, font=fc, fill=(255, 255, 255))
        bottom = by + block_h2

    if brand_line:
        fb = e.font("SemiBold", 18)
        d.text((M, H - 60), brand_line.upper(), font=fb, fill=LEFT)

    img.save(out)
    return {"text_bbox": (M, top, W - M, bottom)}
