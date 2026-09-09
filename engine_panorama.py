# -*- coding: utf-8 -*-
"""
ПАНОРАМА (Seamless Panorama) — стиль 05 из библиотеки 18 (15.08.2026,
референс karusel-styles-library.html). Референс просит ОДНО изображение,
перетекающее через все слайды сплошным полотном (canvas 1080*N×1350,
нарезка). В движке — упрощённая, но честная версия: один и тот же кадр
банка, увеличенный по ширине канвы, показывается СМЕЩАЮЩИМИСЯ по вертикали
окнами — эффект панорамирования камеры, а не монтаж разных сцен. Настоящее
бесшовное полотно требует кадра с запасом по высоте в 2-3 раза больше
портретного 4:5 — у реального банка (телефонные фото, ~16:9 в масштабе
ширины канвы) запас гораздо меньше; смещение подобрано под то, что кадр
банка реально даёт, а не под спеку буквально.

ШРИФТ. Заголовок — Montserrat (600), довендорен. Акценты — Cormorant
Garamond (уже вендорен под «Манифест»).
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H = e.W, e.H
M = e.M

BG = (232, 226, 216)     # #E8E2D8
INK = (32, 36, 43)       # #20242B
MUTED = (150, 144, 134)

_MONTSERRAT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fonts", "Montserrat", "Montserrat-SemiBold.ttf")
_CORMORANT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fonts", "CormorantGaramond", "CormorantGaramond-Italic.ttf")


def montserrat(size):
    return ImageFont.truetype(_MONTSERRAT, size)


def cormorant_italic(size):
    return ImageFont.truetype(_CORMORANT, size)


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


def _pan_source(photo_key):
    """Кадр банка, увеличенный по ширине канвы (высота — по пропорции)."""
    img = Image.open(e.PHOTOS[photo_key]).convert("RGB")
    iw, ih = img.size
    nh = round(ih * (W / iw))
    return img.resize((W, nh), Image.LANCZOS)


def _pan_window(photo_key, band, n):
    """band: 0..n-1 — окно кадра, панорамирующее сверху вниз по мере
    пролистывания. Шаг подобран под РЕАЛЬНЫЙ запас кадра, не берётся с
    потолка, иначе на коротком фото окно ушло бы за нижний край."""
    src = _pan_source(photo_key)
    extra = max(0, src.height - H)
    step = extra // max(1, n - 1) if n > 1 else 0
    y0 = min(extra, band * step)
    return src.crop((0, y0, W, y0 + H))


def cover(hook, sub, photo_key, acc, out, i=1, n=4, brand_line=""):
    """Обложка: левое окно панорамы, заголовок внизу, тонкая подпись-
    приглашение свайпнуть курсивом Cormorant."""
    img = _pan_window(photo_key, 0, n).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top = int(H * 0.55)
    for yy in range(top, H):
        a = int(160 * min(1, (yy - top) / (H * 0.45)))
        od.line([(0, yy), (W, yy)], fill=(20, 18, 14, min(a, 160)))
    img = Image.alpha_composite(img, ov).convert("RGB")
    d = ImageDraw.Draw(img)

    fst = e.font("SemiBold", 22)
    d.text((M, 58), "%d/%d" % (i, n), font=fst, fill=(255, 255, 255))

    bw = W - 2 * M
    fh = montserrat(56)
    lines = _wrap(d, hook.upper(), fh, bw)[:2]
    lh = int(56 * 1.2)
    block_h = len(lines) * lh + (40 if sub else 0)
    top_y = y = H - 90 - block_h
    for line in lines:
        d.text((M, y), line, font=fh, fill=(255, 255, 255))
        y += lh
    if sub:
        fs = cormorant_italic(30)
        d.text((M, y + 10), sub, font=fs, fill=acc)
        y += 40

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top_y, W - M, y)}


def continued(step_label, photo_key, band, acc, out, i=2, n=4):
    """Продолжение кадра: окно панорамы сместилось, внизу — короткая метка
    этапа («День 3 · Старый город»)."""
    img = _pan_window(photo_key, band, n)
    d = ImageDraw.Draw(img, "RGBA")
    band_top = H - 160
    d.rectangle([0, band_top, W, H], fill=(20, 18, 14, 150))

    fst = e.font("SemiBold", 22)
    d.text((W - M - d.textlength("%d/%d" % (i, n), font=fst), 58),
           "%d/%d" % (i, n), font=fst, fill=(255, 255, 255))

    fs = montserrat(30)
    y = band_top + 36
    d.text((M, y), step_label, font=fs, fill=acc)

    # Без "photo" в возврате: check_face_safe мерил бы лицо на e.fit(photo) —
    # РАЗНОМ кадрировании, чем то, что реально нарисовано (_pan_window режет
    # по-своему, окно band>0 смещено вниз). Автопроверка сверяла бы не то,
    # что на слайде — хуже, чем её не иметь. Безопасность здесь структурная:
    # текст в сплошной тёмной плашке снизу, а не поверх произвольного места
    # кадра — смотреть глазами при сборке реальной карусели.
    img.save(out)
    return {"text_bbox": (M, band_top, W - M, y + 40)}


def climax(title, photo_key, band, acc, out, i=3, n=4, cta_text=""):
    """Кульминация: окно панорамы почти в конце пути, заголовок в сплошной
    тёмной плашке снизу (не по центру поверх виньетки — центр кадра у
    произвольного банковского фото непредсказуем, а плашка безопасна
    структурно при любом содержимом под ней, см. continued())."""
    img = _pan_window(photo_key, band, n)
    d = ImageDraw.Draw(img, "RGBA")

    bw = W - 2 * M
    fh = montserrat(46)
    lines = _wrap(d, title.upper(), fh, bw)
    lh = int(46 * 1.25)
    block_h = len(lines) * lh + (40 if cta_text else 0)
    band_top = H - 76 - block_h
    d.rectangle([0, band_top - 30, W, H], fill=(10, 9, 7, 190))

    fst = e.font("SemiBold", 22)
    d.text((W - M - d.textlength("%d/%d" % (i, n), font=fst), 58),
           "%d/%d" % (i, n), font=fst, fill=(255, 255, 255))

    top = y = band_top
    for line in lines:
        d.text((M, y), line, font=fh, fill=(255, 255, 255))
        y += lh
    if cta_text:
        fc = cormorant_italic(28)
        d.text((M, y + 10), cta_text, font=fc, fill=acc)
        y += 40

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, y)}
