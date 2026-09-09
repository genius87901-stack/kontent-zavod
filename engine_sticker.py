# -*- coding: utf-8 -*-
"""
СТИКЕР (Sticker · Native UGC) — стиль 15 из библиотеки 18 (14.08.2026,
референс karusel-styles-library.html). Имитирует интерфейс сторис: рамки-
стикеры со скруглением и обводкой, «опрос»-пилюли, бабблы-реплики, свайп-
подсказка. Нативно, не рекламно — снижает баннерную слепоту.

ШРИФТ. Nunito (800/900) — заголовки/плашки. Caveat — рукописные акценты
(не используется отдельным вызовом здесь, оставлен для будущих слайдов
семейства). Оба уже довендорены.

ЭМОДЗИ. Референс рисует ⏳/🤯/📌/❤ — ни один из вендоренных текстовых
шрифтов (Nunito проверен fontTools: нет ни стрелки, ни звезды, ни сердца,
ни часов) их не несёт, а цветного эмодзи-шрифта в проекте нет. Все
декоративные акценты здесь — векторные фигуры (_heart, _arrow), не текстовые
глифы: гарантированно рисуются на любой машине, где есть Pillow, а не только
там, где случайно установлен системный эмодзи-фонт.
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

BG = (255, 246, 249)     # #FFF6F9 референса
INK = (30, 27, 34)       # #1E1B22
MUTED = (140, 132, 140)

_NUNITO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Nunito")


def nunito(weight, size):
    name = "Nunito-Black.ttf" if weight == "Black" else "Nunito-Bold.ttf"
    return ImageFont.truetype(os.path.join(_NUNITO_DIR, name), size)


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


def _heart(d, cx, cy, s, fill):
    d.ellipse([cx - s * 0.52, cy - s * 0.34, cx, cy + s * 0.18], fill=fill)
    d.ellipse([cx, cy - s * 0.34, cx + s * 0.52, cy + s * 0.18], fill=fill)
    d.polygon([(cx - s * 0.5, cy), (cx + s * 0.5, cy), (cx, cy + s * 0.62)], fill=fill)


def _arrow_right(d, x, y, size, fill, width=5):
    d.line([(x, y - size), (x + size, y), (x, y + size)], fill=fill, width=width,
           joint="curve")


def _bubble(d, box, fill, outline, tail="bl", radius=28, owidth=3):
    """Скруглённая плашка-стикер с треугольным «хвостиком» бабла."""
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius, fill=fill, outline=outline, width=owidth)
    if tail == "bl":
        d.polygon([(x0 + 30, y1 - 2), (x0 + 30, y1 + 22), (x0 + 58, y1 - 2)], fill=fill)
    elif tail == "br":
        d.polygon([(x1 - 30, y1 - 2), (x1 - 30, y1 + 22), (x1 - 58, y1 - 2)], fill=fill)


def cover(hook, photo_key, acc, out, i=1, n=4, brand_line=""):
    """Обложка: фото под лёгкой вуалью, крупная стикер-плашка с хуком,
    свайп-подсказка внизу справа."""
    img = e.fit(e.PHOTOS[photo_key], H).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top = int(H * 0.36)
    for yy in range(top, H):
        a = int(150 * min(1, (yy - top) / (H * 0.6)))
        od.line([(0, yy), (W, yy)], fill=(20, 16, 20, min(a, 150)))
    img = Image.alpha_composite(img, ov)
    d = ImageDraw.Draw(img)

    bw = W - 2 * M - 64
    fh = nunito("Black", 56)
    lines = _wrap(d, hook, fh, bw)[:4]
    lh = int(56 * 1.22)
    pad = 34
    block_h = len(lines) * lh + 2 * pad
    top_y = H - 190 - block_h
    box = (M, top_y, W - M, top_y + block_h)
    _bubble(d, box, (255, 255, 255, 235), INK, tail="bl")
    y = top_y + pad
    for line in lines:
        d.text((M + pad, y), line, font=fh, fill=INK)
        y += lh

    fs = e.font("SemiBold", 24)
    st = "свайп"
    sw = d.textlength(st, font=fs)
    d.text((W - M - sw - 40, H - 84), st, font=fs, fill=(255, 255, 255))
    _arrow_right(d, W - M - 18, H - 72, 12, (255, 255, 255))

    fst = e.font("SemiBold", 22)
    d.text((M, 58), "%d/%d" % (i, n), font=fst, fill=(255, 255, 255))

    img = img.convert("RGB")
    img.save(out)
    return {"photo": photo_key, "text_bbox": box}


def poll_slide(remark, opt_a, opt_b, acc, out, i=2, n=4, picked="b", section=""):
    """«Опрос» + баббл-реплика: имитация стикера-голосования сторис. `picked`
    определяет, какая пилюля залита акцентом (та, что «выбрана»)."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if section:
        fl = e.font("SemiBold", 22)
        d.text((M, 58), section.upper(), font=fl, fill=MUTED)
    fst = e.font("SemiBold", 22)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=fst), 58), t, font=fst, fill=MUTED)

    fp = nunito("Bold", 30)
    pills = [("a", opt_a), ("b", opt_b)]
    py = 240
    top = py
    for key, label in pills:
        pw_ = d.textlength(label, font=fp) + 64
        filled = key == picked
        d.rounded_rectangle([M, py, M + pw_, py + 76], 38,
                            fill=acc if filled else (255, 255, 255),
                            outline=INK, width=3)
        d.text((M + 32, py + 20), label, font=fp,
               fill=INK if not filled else (255, 255, 255))
        py += 96

    bw = W - 2 * M - 64
    fh = nunito("Black", 40)
    lines = _wrap(d, remark, fh, bw)
    lh = int(40 * 1.3)
    pad = 30
    block_h = len(lines) * lh + 2 * pad
    by0 = py + 40
    box = (M, by0, W - M, by0 + block_h)
    _bubble(d, box, (255, 255, 255), INK, tail="bl")
    y = by0 + pad
    for line in lines:
        d.text((M + pad, y), line, font=fh, fill=INK)
        y += lh

    img.save(out)
    return {"text_bbox": (M, top, W - M, by0 + block_h)}


def outro(cta_text, likes, acc, out, i=4, n=4, brand_line=""):
    """CTA-стикер: крупная реплика-баббл + фейковый лайк-каунтер (сердце
    вектором + число) — социальное доказательство в языке платформы."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    bw = W - 2 * M - 64
    fh = nunito("Black", 52)
    lines = _wrap(d, cta_text, fh, bw)
    lh = int(52 * 1.25)
    pad = 34
    block_h = len(lines) * lh + 2 * pad
    top_y = (H - block_h) // 2 - 60
    box = (M, top_y, W - M, top_y + block_h)
    top = top_y
    _bubble(d, box, (255, 255, 255), INK, tail="br", radius=32)
    y = top_y + pad
    for line in lines:
        d.text((M + pad, y), line, font=fh, fill=INK)
        y += lh

    hy = top_y + block_h + 46
    _heart(d, W // 2 - 60, hy, 44, acc)
    fl = nunito("Bold", 30)
    lt = likes
    d.text((W // 2 - 20, hy - 20), lt, font=fl, fill=INK)

    if brand_line:
        fb = e.font("SemiBold", 20)
        bt = brand_line.upper()
        bw2 = d.textlength(bt, font=fb)
        d.text(((W - bw2) // 2, H - 70), bt, font=fb, fill=MUTED)

    img.save(out)
    return {"text_bbox": (M, top, W - M, hy + 30)}
