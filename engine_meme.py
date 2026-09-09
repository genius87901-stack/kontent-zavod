# -*- coding: utf-8 -*-
"""
РЕПЛИКА (Bold Meme-Native) — стиль 18 из библиотеки 18 (14.08.2026,
референс karusel-styles-library.html). Одна ударная фраза на контрастной
жёлтой плашке или в «скриншот»-рамке — вайб разговорного скрина, не
рекламного баннера. Минимум дизайна, максимум прямоты.

ШРИФТ. Golos Text — уже вендорен вариативным файлом (ось wght 400..900,
проверено fontTools), заголовки набираются на 800 без довендоринга новых
файлов. Метки — JetBrains Mono. У GolosText есть кириллица, стрелка «→» и
кавычки-лапки «» (проверено) — доп. фильтров-подмен, в отличие от
«Манифеста» (PT Serif) и «Стикера» (Nunito), тут не нужно.
"""
import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

BG = (17, 18, 20)          # #111214 референса
INK = (245, 245, 243)      # #F5F5F3
BAR_INK = (17, 18, 20)     # текст поверх жёлтой плашки — всегда тёмный

_GOLOS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "fonts", "GolosText", "GolosText-Variable.ttf")
_MONO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fonts", "JetBrainsMono", "JetBrainsMono-Regular.ttf")


def golos(size, weight=800):
    f = ImageFont.truetype(_GOLOS, size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def mono(size):
    return ImageFont.truetype(_MONO, size)


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


def _fit(d, text, maxw, hi=68, lo=42, max_lines=4, weight=800):
    for size in range(hi, lo - 1, -2):
        f = golos(size, weight)
        lines = _wrap(d, text.upper(), f, maxw)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.2)
    f = golos(lo, weight)
    return f, _wrap(d, text.upper(), f, maxw), int(lo * 1.2)


def _pageno(d, i, n, fill):
    f = mono(24)
    t = "%02d/%02d" % (i, n)
    d.text((W - M - d.textlength(t, font=f), 58), t, font=f, fill=fill)


def cover(bar_text, sub, photo_key, acc, out, i=1, n=4, brand_line=""):
    """Обложка-реплика: фото приглушено, жёлтая плашка с репликой в кавычках
    по центру, контекст-подпись мелко под ней."""
    base = e.fit(e.PHOTOS[photo_key], H)
    g = ImageEnhance.Color(base).enhance(0.55)
    g = ImageEnhance.Brightness(g).enhance(0.62)
    g = ImageEnhance.Contrast(g).enhance(1.08)
    img = g.convert("RGB")
    d = ImageDraw.Draw(img)
    _pageno(d, i, n, INK)

    bw = W - 2 * M - 64
    quoted = "«%s»" % bar_text.strip("«»")
    fh, lines, lh = _fit(d, quoted, bw, hi=64, lo=40, max_lines=4)
    pad = 32
    block_h = len(lines) * lh + 2 * pad
    fs = golos(26, 500)
    sub_lines = _wrap(d, "— " + sub, fs, bw) if sub else []
    # Плашка прижата к НИЖНЕЙ трети кадра, не к центру: первая версия ставила
    # её посередине канвы, а лицо на портретном кадре почти всегда стоит
    # именно там — реальная сборка (build_c64) на двух разных фото поймала
    # 61-62% перекрытия лица гейтом. Нижняя треть у портретов почти всегда
    # свободна (плечи/кадр), тот же приём уже держат обложки Редактуры,
    # Дневника и Стикера этой же библиотеки.
    bottom_pad = 90 + (len(sub_lines) * 34 + 24 if sub_lines else 0)
    top = by = H - bottom_pad - block_h
    d.rectangle([M, by, W - M, by + block_h], fill=acc)
    y = by + pad
    for line in lines:
        d.text((M + 32, y), line, font=fh, fill=BAR_INK)
        y += lh
    bottom = by + block_h

    if sub_lines:
        sy = bottom + 24
        for line in sub_lines:
            d.text((M, sy), line, font=fs, fill=(210, 208, 204))
            sy += 34
        bottom = sy

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, bottom)}


def screen_slide(text, acc, out, i=2, n=4, section=""):
    """«Скриншот»-рамка на тёмном фоне: тонкая рамка карточки, внутри —
    жирная реплика без плашки (контраст со слайдом-обложкой)."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if section:
        fl = mono(22)
        d.text((M, 58), section.upper(), font=fl, fill=(120, 120, 116))
    _pageno(d, i, n, INK)

    pad = 44
    bw = W - 2 * M - 2 * pad
    fh, lines, lh = _fit(d, text, bw, hi=54, lo=36, max_lines=6, weight=700)
    block_h = len(lines) * lh
    frame_h = block_h + 2 * pad
    fy = top = (H - frame_h) // 2
    d.rounded_rectangle([M, fy, W - M, fy + frame_h], 4, outline=acc, width=2)
    y = fy + pad
    for line in lines:
        d.text((M + pad, y), line, font=fh, fill=INK)
        y += lh

    img.save(out)
    return {"text_bbox": (M, top, W - M, fy + frame_h)}


def cta_bar(bar_text, cta_text, acc, out, i=4, n=4, brand_line=""):
    """Финал-реплика: та же жёлтая плашка, что на обложке (кольцует стиль),
    плюс призыв мелко под ней."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _pageno(d, i, n, INK)

    bw = W - 2 * M - 64
    fh, lines, lh = _fit(d, bar_text, bw, hi=58, lo=38, max_lines=4)
    pad = 30
    block_h = len(lines) * lh + 2 * pad
    top = by = 220
    d.rectangle([M, by, W - M, by + block_h], fill=acc)
    y = by + pad
    for line in lines:
        d.text((M + 32, y), line, font=fh, fill=BAR_INK)
        y += lh
    bottom = by + block_h

    if cta_text:
        fc = golos(28, 600)
        cw = d.textlength(cta_text, font=fc)
        cy = bottom + 30
        d.text(((W - cw) // 2, cy), cta_text, font=fc, fill=acc)
        bottom = cy + 40

    if brand_line:
        fb = mono(20)
        bt = brand_line.upper()
        bw2 = d.textlength(bt, font=fb)
        d.text(((W - bw2) // 2, H - 70), bt, font=fb, fill=(120, 120, 116))

    img.save(out)
    return {"text_bbox": (M, top, W - M, bottom)}
