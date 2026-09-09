# -*- coding: utf-8 -*-
"""
ТИШИНА (Swiss Minimal) — стиль 03 из библиотеки 18 (15.08.2026, референс
karusel-styles-library.html). Одна мысль на слайд, огромные поля воздуха,
единственный акцент — цветная точка/подчёркивание. Микро-обучение: 15-20
слов на слайд, пустота — сам приём.

ШРИФТ. Manrope — уже вендорен. Поля шире, чем у остальных семейств
(marginX 120 по спеке референса, не общий engine4.M=72) — это ЧАСТЬ образа
стиля («мне нечего доказывать»), не косметика.
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H = e.W, e.H
M = 120

BG = (255, 255, 255)
INK = (27, 27, 31)
MUTED = (140, 138, 144)

_MANROPE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fonts", "Manrope", "Manrope-Variable.ttf")


def manrope(size, weight=400):
    f = ImageFont.truetype(_MANROPE, size)
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


def _fit(d, text, maxw, hi=52, lo=32, max_lines=4, weight=700):
    for size in range(hi, lo - 1, -2):
        f = manrope(size, weight)
        lines = _wrap(d, text, f, maxw)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.32)
    f = manrope(lo, weight)
    return f, _wrap(d, text, f, maxw), int(lo * 1.32)


def _dot(d, x, y, acc, r=7):
    d.ellipse([x - r, y - r, x + r, y + r], fill=acc)


def _pageno(d, i, n, fill):
    f = manrope(20, 600)
    t = "%d / %d" % (i, n)
    d.text((W - M - d.textlength(t, font=f), 58), t, font=f, fill=fill)


def cover(question, sub, photo_key, acc, out, i=1, n=5, brand_line=""):
    """Обложка-вопрос: фото под лёгкой вуалью, надстрочка «ВОПРОС» акцентом,
    заголовок на оптической трети, точка-акцент."""
    # Текст прижат к НИЖНЕЙ трети, не к верхней (первая версия ставила его на
    # «оптическую треть» сверху — прямо на лицо у портретных кадров, гейт
    # поймал 100% перекрытия). Нижняя треть портрета почти всегда свободна —
    # тот же приём, что уже держат обложки других стилей библиотеки.
    base = e.fit(e.PHOTOS[photo_key], H).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top_grad = int(H * 0.55)
    for yy in range(top_grad, H):
        a = int(150 * min(1, (yy - top_grad) / (H * 0.45)))
        od.line([(0, yy), (W, yy)], fill=(255, 255, 255, min(a, 150)))
    img = Image.alpha_composite(base, ov).convert("RGB")
    d = ImageDraw.Draw(img)
    _pageno(d, i, n, (255, 255, 255))

    bw = W - 2 * M
    fh, lines, lh = _fit(d, question, bw, hi=56, lo=36, max_lines=4)
    block_h = 46 + len(lines) * lh + (40 if sub else 0)
    top = y0 = H - 90 - block_h
    d.text((M, y0), "ВОПРОС", font=manrope(22, 700), fill=acc)
    y = y0 + 46
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh
    _dot(d, M + 9, y + 20, acc)
    if sub:
        fs = manrope(24, 400)
        d.text((M + 30, y + 6), sub, font=fs, fill=MUTED)
        y += 40

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, int(y))}


def statement(text, acc, out, i=2, n=5, section=""):
    """По одной идее на слайд: короткая строка на белом, точка-акцент,
    воздух несёт вес не меньше текста."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if section:
        fl = manrope(18, 700)
        d.text((M, 58), section.upper(), font=fl, fill=MUTED)
    _pageno(d, i, n, INK)

    bw = W - 2 * M
    fh, lines, lh = _fit(d, text, bw, hi=52, lo=34, max_lines=4)
    top = y = (H - len(lines) * lh) // 2
    _dot(d, M + 7, y - 14, acc)
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh

    img.save(out)
    return {"text_bbox": (M, top - 24, W - M, y)}


def bignum(num, text, acc, out, i=3, n=5, section=""):
    """Числовой акцент: крупная цифра акцентом + короткий вывод."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if section:
        fl = manrope(18, 700)
        d.text((M, 58), section.upper(), font=fl, fill=MUTED)
    _pageno(d, i, n, INK)

    fnum = manrope(180, 800)
    bw = W - 2 * M
    fh, lines, lh = _fit(d, text, bw, hi=44, lo=30, max_lines=3, weight=600)
    block_h = 200 + len(lines) * lh
    top = y = (H - block_h) // 2
    d.text((M, y), str(num), font=fnum, fill=acc)
    y += 200
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh

    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}


def cta(text, prompt, acc, out, i=5, n=5, brand_line=""):
    """Сухой CTA: без плашек и кнопок — тот же минимализм до конца."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _pageno(d, i, n, INK)

    bw = W - 2 * M
    fh, lines, lh = _fit(d, text, bw, hi=48, lo=32, max_lines=4)
    top = y = (H - len(lines) * lh) // 2 - 20
    _dot(d, M + 7, y - 14, acc)
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh
    bottom = y
    if prompt:
        fp = manrope(24, 600)
        y += 20
        d.text((M, y), prompt, font=fp, fill=acc)
        bottom = y + 34

    if brand_line:
        fb = manrope(18, 700)
        bt = brand_line.upper()
        d.text((M, H - 70), bt, font=fb, fill=MUTED)

    img.save(out)
    return {"text_bbox": (M, top - 24, W - M, bottom)}
