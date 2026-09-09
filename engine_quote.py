# -*- coding: utf-8 -*-
"""
МАНИФЕСТ (Quote · Manifesto) — стиль 14 из библиотеки 18 (14.08.2026,
референс karusel-styles-library.html). Одна сильная мысль на слайд
гигантским серифом на тёмном фоне, декоративная кавычка-глиф, автор мелко
внизу. Медитативный ритм: пустота и пауза несут вес не меньше, чем текст.

ШРИФТ. Цитата — Prata (единственное начертание Regular, довендорено с
кириллицей). Подпись/автор — PT Serif.

АКЦЕНТ — параметр, как у всех семейств (engine4.ACCENTS), не зашитый в
референсе тёплый золотой: реальный выбор акцента — на шаге регистрации
семейства, до этого подставляется любой цвет палитры.
"""
import os

from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

BG = (22, 21, 18)          # #161512 референса
INK = (244, 239, 230)      # #F4EFE6
MUTED = (138, 133, 120)    # #8A8578

_PRATA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "fonts", "Prata", "Prata-Regular.ttf")
_PTSERIF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fonts", "PTSerif", "PTSerif-Regular.ttf")


def prata(size):
    return ImageFont.truetype(_PRATA, size)


def pt_serif(size):
    return ImageFont.truetype(_PTSERIF, size)


def _pt_safe(text):
    """PT Serif не несёт «→» (U+2192, проверено fontTools) — без замены
    вместо стрелки рисовался пустой тофу-квадрат. «→» в подписях-призывах —
    привычная по всему заводу конструкция («полный разбор → в профиле»),
    поэтому подменяем здесь, а не полагаемся на то, что каждый вызывающий
    скрипт про это вспомнит."""
    return text.replace("→", "—")


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


def _fit_quote(d, text, maxw, hi=88, lo=52, max_lines=5):
    for size in range(hi, lo - 1, -2):
        f = prata(size)
        lines = _wrap(d, text, f, maxw)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.34)
    f = prata(lo)
    return f, _wrap(d, text, f, maxw), int(lo * 1.34)


def _mark(img, cx, top_y, size, acc):
    """Гигантская кавычка «„20% прозрачности“» позади текста — фоновый
    декор, не читаемый как буква, только как акцент-пятно."""
    m = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    md = ImageDraw.Draw(m)
    f = prata(size)
    md.text((0, 0), "„", font=f, fill=acc + (56,))  # „ — открывающая лапка
    img.alpha_composite(m, (int(cx - size * 0.3), int(top_y)))


def cover(quote, author, photo_key, acc, out, i=1, n=5, brand_line=""):
    """Обложка-тезис: фото под ПОЛНОЙ тёмной вуалью (referral sc-full — не
    градиент, а ровный тёмный слой на весь кадр), цитата и автор по центру."""
    base = e.fit(e.PHOTOS[photo_key], H).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (10, 9, 8, 205))
    img = Image.alpha_composite(base, ov)
    d = ImageDraw.Draw(img)

    bw = W - 2 * M
    f, lines, lh = _fit_quote(d, quote, bw, hi=80, lo=48, max_lines=5)
    block_h = len(lines) * lh + (44 if author else 0)
    top = y = (H - block_h) // 2
    for line in lines:
        lw = d.textlength(line, font=f)
        d.text(((W - lw) // 2, y), line, font=f, fill=INK)
        y += lh
    if author:
        fa = pt_serif(26)
        at = _pt_safe("— " + author)
        aw = d.textlength(at, font=fa)
        d.text(((W - aw) // 2, y + 16), at, font=fa, fill=MUTED)
        y += 44

    fst = e.font("SemiBold", 22)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=fst), 58), t, font=fst, fill=MUTED)
    if brand_line:
        fb = e.font("SemiBold", 20)
        bt = brand_line.upper()
        d.text((M, H - 70), bt, font=fb, fill=MUTED)

    img = img.convert("RGB")
    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, y)}


def quote_slide(quote, acc, out, i=2, n=5, mark=True):
    """Развитие мысли: плоский тёмный фон, декоративная кавычка позади,
    цитата по центру крупным Prata. Вызывается несколько раз подряд —
    «3–4 цитаты-развития» референса это повтор этой же функции с новым
    текстом, не отдельные раскладки."""
    img = Image.new("RGBA", (W, H), BG)

    bw = W - 2 * M - 40
    d0 = ImageDraw.Draw(img)
    f, lines, lh = _fit_quote(d0, quote, bw, hi=72, lo=44, max_lines=4)
    block_h = len(lines) * lh
    top = y = (H - block_h) // 2

    if mark:
        _mark(img, M + 10, top - 90, 220, acc)

    d = ImageDraw.Draw(img)
    for line in lines:
        lw = d.textlength(line, font=f)
        d.text(((W - lw) // 2, y), line, font=f, fill=INK)
        y += lh

    fst = e.font("SemiBold", 22)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=fst), 58), t, font=fst, fill=MUTED)

    img = img.convert("RGB")
    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}


def cta_slide(quote, prompt, acc, out, i=5, n=5, brand_line=""):
    """Финал: последняя мысль + мягкий призыв мелким PT Serif — «подпись/CTA»
    референса. Без плашек и кнопок: ритм стиля держится тишиной до конца."""
    img = Image.new("RGBA", (W, H), BG)
    d0 = ImageDraw.Draw(img)

    bw = W - 2 * M
    f, lines, lh = _fit_quote(d0, quote, bw, hi=72, lo=44, max_lines=4)
    block_h = len(lines) * lh + (46 if prompt else 0)
    top = y = (H - block_h) // 2
    d = ImageDraw.Draw(img)
    for line in lines:
        lw = d.textlength(line, font=f)
        d.text(((W - lw) // 2, y), line, font=f, fill=INK)
        y += lh
    if prompt:
        fp = pt_serif(28)
        prompt = _pt_safe(prompt)
        pw = d.textlength(prompt, font=fp)
        d.text(((W - pw) // 2, y + 20), prompt, font=fp, fill=acc)
        y += 46

    if brand_line:
        fb = e.font("SemiBold", 20)
        bt = brand_line.upper()
        bw2 = d.textlength(bt, font=fb)
        d.text(((W - bw2) // 2, H - 70), bt, font=fb, fill=MUTED)

    img = img.convert("RGB")
    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}
