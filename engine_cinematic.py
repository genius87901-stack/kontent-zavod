# -*- coding: utf-8 -*-
"""
КАДР (Cinematic) — стиль 04 из библиотеки 18 (15.08.2026, референс
karusel-styles-library.html). Фото во весь кадр, кинематографический
леттербокс (чёрные полосы сверху/снизу), лёгкое зерно, подпись как субтитр.
Тёмно, атмосферно.

ШРИФТ. Титры — Oswald (600, капс, разрядка). Подписи — PT Serif (уже
вендорен под «Манифест»). У Oswald нет глифа «→» (проверено fontTools) —
_osw_safe() подменяет его тире, как у PT Serif в engine_quote.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

INK = (243, 236, 224)      # #F3ECE0
MUTED = (150, 145, 138)

_OSWALD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fonts", "Oswald", "Oswald-SemiBold.ttf")
_PTSERIF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fonts", "PTSerif", "PTSerif-Regular.ttf")


def oswald(size):
    return ImageFont.truetype(_OSWALD, size)


def pt_serif(size):
    return ImageFont.truetype(_PTSERIF, size)


def _osw_safe(text):
    return text.replace("→", "—")


def _track_w(d, text, f, track):
    if not text:
        return 0
    return sum(d.textlength(ch, font=f) for ch in text) + track * (len(text) - 1)


def _track(d, text, f, x, y, fill, track):
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + track
    return x


def _wrap_tracked(d, text, f, maxw, track):
    words, lines, cur = text.split(), [], ""
    for w in words:
        probe = (cur + " " + w).strip()
        if _track_w(d, probe, f, track) <= maxw or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit_title(d, text, maxw, hi=64, lo=38, max_lines=3, track=3.0):
    t = _osw_safe(text.upper())
    for size in range(hi, lo - 1, -2):
        f = oswald(size)
        lines = _wrap_tracked(d, t, f, maxw, track)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.3)
    f = oswald(lo)
    return f, _wrap_tracked(d, t, f, maxw, track), int(lo * 1.3)


def _grain(img, amount=5, seed=1):
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    noise = np.random.default_rng(seed).integers(-amount, amount + 1, a.shape[:2])
    for c in range(3):
        a[..., c] += noise
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _letterbox(img, share=0.12):
    d = ImageDraw.Draw(img)
    bh = int(H * share)
    d.rectangle([0, 0, W, bh], fill=(0, 0, 0))
    d.rectangle([0, H - bh, W, H], fill=(0, 0, 0))
    return bh


def _scene_tag(d, text, acc):
    f = e.font("SemiBold", 22)
    _track(d, text.upper(), f, M, int(H * 0.12) + 26, acc, 3.0)


def cover(title, scene, photo_key, acc, out, i=1, n=5):
    """Тизер-обложка: летербокс, зерно, титр над нижней полосой, «СЦЕНА N»
    мелким моно в углу."""
    base = e.fit(e.PHOTOS[photo_key], H)
    img = _grain(base, amount=5, seed=hash(photo_key) % 1000)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top = int(H * 0.35)
    for yy in range(top, H):
        a = int(190 * min(1, (yy - top) / (H * 0.5)))
        od.line([(0, yy), (W, yy)], fill=(6, 5, 5, min(a, 190)))
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    bh = _letterbox(img)
    d = ImageDraw.Draw(img)
    _scene_tag(d, scene, acc)

    bw = W - 2 * M
    fh, lines, lh = _fit_title(d, title, bw, hi=62, lo=38, max_lines=3)
    bottom_pad = bh + 40
    top_y = y = H - bottom_pad - len(lines) * lh
    for line in lines:
        _track(d, line, fh, M, y, INK, 3.0)
        y += lh

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top_y, W - M, y)}


def scene_caption(quote, scene, acc, out, i=2, n=5, photo_key=None):
    """Сцена-цитата: летербокс на тёмном фоне (или фото), подпись PT Serif
    курсивным духом (без italic-начертания — размер и цвет держат акцент)."""
    if photo_key:
        img = _grain(e.fit(e.PHOTOS[photo_key], H), seed=hash(photo_key) % 1000)
        ov = Image.new("RGBA", (W, H), (8, 7, 6, 150))
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    else:
        img = Image.new("RGB", (W, H), (12, 13, 16))
    bh = _letterbox(img)
    d = ImageDraw.Draw(img)
    _scene_tag(d, scene, acc)

    bw = W - 2 * M
    fq = pt_serif(38)
    lines = []
    words, cur = quote.split(), ""
    for w in words:
        probe = (cur + " " + w).strip()
        if d.textlength(probe, font=fq) <= bw or not cur:
            cur = probe
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lh = int(38 * 1.5)
    top = y = (H - len(lines) * lh) // 2
    for line in lines:
        d.text((M, y), line, font=fq, fill=INK)
        y += lh

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, y)} if photo_key \
        else {"text_bbox": (M, top, W - M, y)}


def outro(title, cta_text, tag, acc, out, i=5, n=5, brand_line=""):
    """Финальный «титр»: маленький заголовок + призыв, летербокс и тег
    «ФИНАЛ» сохраняют ритм плёнки до последнего кадра."""
    img = Image.new("RGB", (W, H), (10, 9, 8))
    bh = _letterbox(img)
    d = ImageDraw.Draw(img)
    _scene_tag(d, tag, acc)

    bw = W - 2 * M
    fh, lines, lh = _fit_title(d, title, bw, hi=44, lo=30, max_lines=3)
    top = y = H // 2 - 60
    for line in lines:
        _track(d, line, fh, M, y, INK, 3.0)
        y += lh
    bottom = y

    if cta_text:
        fc = pt_serif(28)
        ct = _osw_safe(cta_text)
        y += 20
        d.text((M, y), ct, font=fc, fill=acc)
        bottom = y + 40

    if brand_line:
        fb = e.font("SemiBold", 20)
        bt = brand_line.upper()
        d.text((M, H - bh - 46), bt, font=fb, fill=MUTED)

    img.save(out)
    return {"text_bbox": (M, top, W - M, bottom)}
