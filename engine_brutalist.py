# -*- coding: utf-8 -*-
"""
БЕТОН (Neo-Brutalist) — стиль 02 из библиотеки 18 (14.08.2026, референс
karusel-styles-library.html). Гигантская типографика, нулевые скругления,
жёсткий чёрно-белый контраст с одним ярким акцентом — «некрасиво нарочно»,
чтобы взгляд цеплялся на фоне вылизанных шаблонов. Никаких теней и
градиентов на плашках (только фото-обложка тонируется под контраст).

ШРИФТ. Референс просит «Archivo Black / Unbounded» — Archivo Black в
довендоренном релизе Google Fonts БЕЗ кириллицы (0/66 проверочных букв,
проверено fontTools при вендоринге), поэтому дисплейный шрифт стиля —
только Unbounded (700/900). Служебная типографика (номер, метка) —
JetBrains Mono, как и просит референс.

АКЦЕНТ — параметр, как у всех семейств завода (engine4.ACCENTS), а не
зашитый в референсе #FF3B12: один сигнальный цвет здесь даже больше, чем в
других стилях, роль всего смысла оформления, но заводить ради него отдельный
глобальный акцент до решения о раскатке (см. HANDOFF про editorial/_not_
shipped) преждевременно — сейчас подставляется любой акцент из общей
палитры, реальный выбор — на шаге регистрации семейства.

Разметка текста — общая с движком (engine4.tokens), через e.wrap_rich/
draw_rich напрямую (кернинг/трекинг стилю не нужен, в отличие от
«Редактуры» — можно переиспользовать движковые хелперы без своих копий):
  *слово*   → акцентный цвет
"""
import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

BG = (237, 237, 232)     # #EDEDE8 референса
INK = (10, 10, 10)       # #0A0A0A референса
WHITE = (245, 245, 240)

_UNB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fonts", "Unbounded")
_MONO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fonts", "JetBrainsMono")


def display(weight, size):
    name = "Unbounded-Black.ttf" if weight == "Black" else "Unbounded-Bold.ttf"
    return ImageFont.truetype(os.path.join(_UNB_DIR, name), size)


def mono(size):
    return ImageFont.truetype(os.path.join(_MONO_DIR, "JetBrainsMono-Regular.ttf"), size)


def _fit_head_rich(d, text, maxw, hi=150, lo=88, max_lines=3, weight="Black"):
    """Гигантский заголовок: крупнее — лучше, но не больше max_lines строк.
    Переиспользует e.wrap_rich/tokens — своей разметки стилю заводить не за
    что, кернинг тут не нужен (в отличие от «Редактуры»)."""
    for size in range(hi, lo - 1, -4):
        f = display(weight, size)
        lines = e.wrap_rich(d, text.upper(), f, maxw)
        if len(lines) <= max_lines:
            return f, lines, int(size * 1.05)
    f = display(weight, lo)
    return f, e.wrap_rich(d, text.upper(), f, maxw), int(lo * 1.05)


def _pageno(d, i, x, y, fill):
    f = mono(28)
    d.text((x, y), "%02d" % i, font=f, fill=fill)


def cover(hook, photo_key, acc, out, i=1, n=6, eyebrow_text="СВАЙПАЙ →"):
    """Обложка-удар: фото сведено в жёсткий ч/б контраст, заголовок гигантской
    Unbounded впритык к левому полю, *слово* — акцентом."""
    base = e.fit(e.PHOTOS[photo_key], H)
    g = ImageEnhance.Color(base).enhance(0.30)
    g = ImageEnhance.Contrast(g).enhance(1.20)
    g = ImageEnhance.Brightness(g).enhance(0.70)
    img = g.convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top_grad = int(H * 0.32)
    for yy in range(top_grad, H):
        a = int(196 * min(1, (yy - top_grad) / (H * 0.55)))
        od.line([(0, yy), (W, yy)], fill=(6, 6, 6, min(a, 196)))
    img = Image.alpha_composite(img, ov).convert("RGB")
    d = ImageDraw.Draw(img)

    fe = mono(24)
    d.text((M, 58), eyebrow_text.upper(), font=fe, fill=acc)
    _pageno(d, i, W - M - 40, 58, WHITE)

    bw = W - 2 * M
    fh, lines, lh = _fit_head_rich(d, hook, bw, hi=150, lo=88, max_lines=3)
    top = y = H - 130 - len(lines) * lh
    y = e.draw_rich(d, lines, fh, M, y, lh, acc, base=WHITE)

    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top, W - M, y)}


def statement(label_text, body, acc, out, i=2, n=6, section=""):
    """«Тезис плашками»: светлый фон, мелкая акцентная плашка-метка сверху,
    крупное утверждение снизу впритык к полю."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    fl = mono(26)
    lt = label_text.upper()
    lw = d.textlength(lt, font=fl)
    d.rectangle([M, 64, M + lw + 32, 64 + 46], fill=acc)
    d.text((M + 16, 76), lt, font=fl, fill=INK)
    _pageno(d, i, W - M - 40, 76, INK)

    bw = W - 2 * M
    fh, lines, lh = _fit_head_rich(d, body, bw, hi=120, lo=72, max_lines=4)
    top = y = H - 160 - len(lines) * lh
    y = e.draw_rich(d, lines, fh, M, y, lh, acc, base=INK)

    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}


def invert(word, sub, acc, out, i=3, n=6, section=""):
    """«Инверсия чёрное/цветное»: фон целиком INK, слово — акцентом во весь
    рост, подпись мелко белым. Резкая смена регистра внутри карусели — сама
    и есть приём привлечения внимания этого стиля."""
    img = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(img)

    fl = mono(26)
    d.text((M, 58), "%02d / %02d" % (i, n), font=fl, fill=WHITE)

    bw = W - 2 * M
    fh, lines, lh = _fit_head_rich(d, "*%s*" % word.upper(), bw, hi=160, lo=100,
                                    max_lines=2)
    y = top = (H - len(lines) * lh) // 2 - 30
    y = e.draw_rich(d, lines, fh, M, y, lh, acc, base=acc)
    if sub:
        fs = e.font("Regular", 30)
        sl = e.wrap(d, sub, fs, bw)
        y += 20
        for line in sl:
            d.text((M, y), line, font=fs, fill=WHITE)
            y += 44

    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}


def cta(title, word, acc, out, i=6, n=6, sub=""):
    """«Жирный CTA-блок»: акцентная заливка во всю нижнюю треть, тёмный текст
    поверх — последний экран карусели, самый плотный по контрасту."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    bw = W - 2 * M
    fh, lines, lh = _fit_head_rich(d, title, bw, hi=110, lo=64, max_lines=3,
                                    weight="Bold")
    top = y = 200
    y = e.draw_rich(d, lines, fh, M, y, lh, acc, base=INK)
    if sub:
        fs = e.font("Regular", 30)
        sl = e.wrap(d, sub, fs, bw)
        y += 24
        for line in sl:
            d.text((M, y), line, font=fs, fill=(70, 70, 66))
            y += 44

    block_h = 220
    by = H - block_h
    d.rectangle([0, by, W, H], fill=acc)
    fw = display("Black", 64)
    ww = d.textlength(word.upper(), font=fw)
    wy = by + 26
    d.text(((W - ww) // 2, wy), word.upper(), font=fw, fill=INK)
    fp = mono(24)
    pt = "СОХРАНИ ЭТОТ ПОСТ"
    pw = d.textlength(pt, font=fp)
    py = wy + 76
    d.text(((W - pw) // 2, py), pt, font=fp, fill=INK)
    # Низ блока — реальный низ последней строки (py + высота строки mono 24),
    # не оценка на глаз: полоса приливная к самому краю канвы намеренно
    # (декоративная плашка на всю ширину), но ТЕКСТ в ней обязан остаться в
    # безопасной зоне так же, как везде — иначе проверка просто не увидит,
    # что реально нарисовано в нижних 220px.
    button_bottom = py + int(24 * 1.3)

    img.save(out)
    return {"text_bbox": (M, top, W - M, max(y, button_bottom))}
