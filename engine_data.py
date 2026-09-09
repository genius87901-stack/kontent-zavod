# -*- coding: utf-8 -*-
"""
ЦИФРА (Data Story) — стиль 07 из библиотеки 18 (15.08.2026, референс
karusel-styles-library.html). Тёмный фон, крупные числа, мини-чарт с
обрезанным у края столбцом — чтобы увидеть финальную точку, надо свайпнуть.

АКЦЕНТ. Референс различает «рост» (зелёный) и «падение» (красный) — второй
цвет вне правила «один акцент на карусель». Здесь везде один
зарегистрированный акцент: он держит и число, и растущие столбцы чарта;
приглушённый серый — служебный текст. Тот же принцип, что у «Развилки».

ШРИФТ. Числа — JetBrains Mono (уже вендорен под «Редактуру»). Подписи —
Manrope (вендорен).
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

BG = (14, 17, 22)        # #0E1116
INK = (231, 234, 240)    # #E7EAF0
MUTED = (120, 126, 138)

_MONO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fonts", "JetBrainsMono", "JetBrainsMono-Regular.ttf")
_MANROPE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fonts", "Manrope", "Manrope-Variable.ttf")


def mono(size):
    return ImageFont.truetype(_MONO, size)


def manrope(size, weight=500):
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


def _pageno(d, i, n):
    f = manrope(20, 700)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=f), 58), t, font=f, fill=MUTED)


def cover(title, photo_key, acc, out, i=1, n=5, chip=""):
    """Обложка-инсайт: тёмное фото, чип-метка сверху («1 метрика»),
    заголовок снизу на плотной вуали."""
    base = e.fit(e.PHOTOS[photo_key], H)
    img = Image.eval(base, lambda p: int(p * 0.55)).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top = int(H * 0.5)
    for yy in range(top, H):
        a = int(220 * min(1, (yy - top) / (H * 0.5)))
        od.line([(0, yy), (W, yy)], fill=(8, 9, 12, min(a, 220)))
    img = Image.alpha_composite(img, ov).convert("RGB")
    d = ImageDraw.Draw(img)
    _pageno(d, i, n)

    if chip:
        fc = manrope(20, 700)
        cw = d.textlength(chip, font=fc)
        d.rounded_rectangle([M, 52, M + cw + 32, 52 + 40], 20, fill=acc)
        d.text((M + 16, 62), chip, font=fc, fill=(10, 10, 14))

    bw = W - 2 * M
    fh = manrope(52, 800)
    lines = _wrap(d, title, fh, bw)[:4]
    lh = int(52 * 1.28)
    top_y = y = H - 90 - len(lines) * lh
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top_y, W - M, y)}


def metric(label, value, unit, acc, out, i=2, n=5, note=""):
    """Одна метрика на слайд: подпись мелко, гигантское число (моно),
    короткий вывод под ним."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _pageno(d, i, n)

    fl = manrope(26, 600)
    fn = mono(150)
    fu = manrope(44, 600)
    fnote = manrope(26, 400)

    top = y = H * 0.34
    d.text((M, y), label, font=fl, fill=MUTED)
    y += 56
    numy = y
    d.text((M, y), value, font=fn, fill=acc)
    nw = d.textlength(value, font=fn)
    if unit:
        d.text((M + nw + 12, y + 70), unit, font=fu, fill=acc)
    y += 176
    bottom = y
    if note:
        nl = _wrap(d, note, fnote, W - 2 * M)
        for line in nl:
            d.text((M, y), line, font=fnote, fill=INK)
            y += 38
        bottom = y

    img.save(out)
    return {"text_bbox": (M, top, W - M, bottom)}


def chart_metric(label, values, cur_index, value_text, acc, out, i=3, n=5):
    """Мини-чарт: столбцы растут слева направо, последний обрезан у края
    кадра — крючок «свайпни, чтобы увидеть точку»."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _pageno(d, i, n)

    fl = manrope(26, 600)
    top = y = H * 0.30
    d.text((M, y), label, font=fl, fill=MUTED)
    y += 56

    chart_h = 260
    bw = (W - 2 * M) / (len(values) - 0.4)
    gap = bw * 0.28
    base_y = y + chart_h
    mx = max(values) or 1
    for k, v in enumerate(values):
        bh = int(chart_h * (v / mx))
        x0 = M + k * bw
        x1 = x0 + bw - gap
        col = acc if k == cur_index else (44, 48, 58)
        d.rectangle([x0, base_y - bh, x1, base_y], fill=col)
    y = base_y + 40

    fn = mono(90)
    d.text((M, y), value_text, font=fn, fill=acc)
    bottom = y + 110

    img.save(out)
    return {"text_bbox": (M, top, W - M, bottom)}


def cta(title, prompt, acc, out, i=5, n=5, brand_line=""):
    """Вывод + CTA: число закрыло вопрос, дальше — простой призыв."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _pageno(d, i, n)

    bw = W - 2 * M
    fh = manrope(46, 700)
    lines = _wrap(d, title, fh, bw)
    lh = int(46 * 1.3)
    block_h = len(lines) * lh + (60 if prompt else 0)
    top = y = (H - block_h) // 2
    for line in lines:
        d.text((M, y), line, font=fh, fill=INK)
        y += lh
    bottom = y
    if prompt:
        fp = manrope(28, 600)
        y += 20
        d.text((M, y), prompt, font=fp, fill=acc)
        bottom = y + 36

    if brand_line:
        fb = manrope(18, 700)
        d.text((M, H - 60), brand_line.upper(), font=fb, fill=MUTED)

    img.save(out)
    return {"text_bbox": (M, top, W - M, bottom)}
