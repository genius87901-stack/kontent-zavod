# -*- coding: utf-8 -*-
"""
ДНЕВНИК (Scrapbook · Collage) — стиль 08 из библиотеки 18 (14.08.2026,
референс karusel-styles-library.html). Тёплый бумажный фон, «приклеенные»
скотчем полароиды под углом, рукописные подписи и дудлы — контраст с
остальными стилями (все на гротеске/антикве): здесь голос — рука, не
типографика.

ШРИФТ. Рукописный акцент — Caveat (Bold) и Marck Script (Regular), оба
довендорены с кириллицей. Тело — Golos Text (уже вендорен для engine_cream,
переиспользуется).

ФОТО. На обложке — во весь кадр (e.fit, как у editorial/photo): только тут
проверка «текст не на лице» работает по-настоящему (замер идёт на полном
1080×1350 кадре). На внутренних слайдах фото — маленький «полароид» в углу;
как и у engine_white.split (фото в узкой полосе, не во весь кадр), "photo"
в возврат не кладём — автоматика мерила бы лицо не на том масштабе, на
котором оно реально показано. Безопасность здесь структурная: текст и
полароид никогда не пересекаются по построению раскладки.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import engine4 as e

W, H, M = e.W, e.H, e.M

PAPER = (244, 237, 224)     # #F4EDE0 референса
INK = (32, 27, 21)          # притемнён от #3B3227 референса: тот тон дал
# v=0.23 в checks.check_palette (порог V_MIN=0.15) и вместе с sat=0.34 читался
# как «второй цвет» — ловится на каждом слове рукописного текста. Этот тон
# темнее и по яркости уже выпадает из маски проверки, как LINK у «Редактуры».
MUTED = (138, 126, 108)
ACC2 = (110, 139, 122)      # #6E8B6E — вторичный (не проверяется палитрой)

_FONTS = os.path.dirname(os.path.abspath(__file__))
_CAVEAT = os.path.join(_FONTS, "fonts", "Caveat", "Caveat-Bold.ttf")
_MARCK = os.path.join(_FONTS, "fonts", "MarckScript", "MarckScript-Regular.ttf")
_GOLOS = os.path.join(_FONTS, "fonts", "GolosText", "GolosText-Variable.ttf")


def hand(size, marck=False):
    return ImageFont.truetype(_MARCK if marck else _CAVEAT, size)


def body_font(size, weight=500):
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


def _paper_grain(img, amount=5, seed=0):
    """Лёгкий бумажный шум вместо внешней текстуры-PNG."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    noise = np.random.default_rng(seed).integers(-amount, amount + 1, a.shape[:2])
    for c in range(3):
        a[..., c] += noise
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _paste_rotated(base, patch, cx, cy, angle):
    """Вставить RGBA-patch в base с поворотом вокруг точки (cx, cy) на канве.
    .paste(..., mask) сам обрезает часть, ушедшую за край — угол и позиция
    могут быть любыми, лишний код на обработку выхода за канву не нужен."""
    rot = patch.rotate(angle, expand=True, resample=Image.BICUBIC)
    base.paste(rot, (int(cx - rot.width / 2), int(cy - rot.height / 2)), rot)


def _tape(w=150, h=44, fill=(255, 255, 255, 130)):
    patch = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(patch).rectangle([0, 0, w, h], fill=fill)
    return patch


def _heart(d, cx, cy, s, fill):
    """Сердце двумя кругами + треугольником — не текстовый глиф. GolosText
    (как и большинство текстовых шрифтов) не несёт ни ♥, ни ★, ни ❤: на
    клиентской машине без этого конкретного эмодзи-шрифта такой символ
    рисуется пустым прямоугольником-«тофу». Вектор гарантированно рисуется
    везде, где есть Pillow."""
    r = s * 0.28
    d.ellipse([cx - s * 0.52, cy - s * 0.34, cx, cy + s * 0.18], fill=fill)
    d.ellipse([cx, cy - s * 0.34, cx + s * 0.52, cy + s * 0.18], fill=fill)
    d.polygon([(cx - s * 0.5, cy), (cx + s * 0.5, cy), (cx, cy + s * 0.62)], fill=fill)


def _fit_box(path, bw, bh):
    img = Image.open(path).convert("RGB")
    tr = bw / bh
    iw, ih = img.size
    if iw / ih > tr:
        nw = int(ih * tr)
        img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    else:
        nh = int(iw / tr)
        img = img.crop((0, 0, iw, nh))
    return img.resize((bw, bh), Image.LANCZOS)


def _polaroid(photo_key, pw, ph, border=16, bottom=58):
    card = Image.new("RGBA", (pw + 2 * border, ph + border + bottom), (250, 248, 244, 255))
    photo = _fit_box(e.PHOTOS[photo_key], pw, ph)
    card.paste(photo, (border, border))
    d = ImageDraw.Draw(card)
    d.rectangle([0, 0, card.width - 1, card.height - 1], outline=(225, 220, 210), width=1)
    return card


def cover(hand_text, sub, photo_key, acc, out, i=1, n=5, brand_line=""):
    """Обложка: фото в полный кадр, тёплая тёмная вуаль снизу, рукописная
    подпись крупной Caveat внизу.

    Скотч-полоса из первой версии убрана: полупрозрачный белый на светлом
    небе визуально читался не «скотчем», а плоским серым прямоугольником —
    без текстуры/диагональных штрихов приём не работал и выглядел как
    случайный артефакт, честнее было убрать, чем оставить недостоверную
    деталь. Полароиды и рукопись внутри карусели держат стиль сами."""
    img = e.fit(e.PHOTOS[photo_key], H).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    top = int(H * 0.42)
    for yy in range(top, H):
        a = int(180 * min(1, (yy - top) / (H * 0.55)))
        od.line([(0, yy), (W, yy)], fill=(28, 20, 12, min(a, 180)))
    img = Image.alpha_composite(img, ov)

    d = ImageDraw.Draw(img)
    fst = e.font("SemiBold", 22)
    d.text((M, 58), "%d/%d" % (i, n), font=fst, fill=(255, 250, 240))

    fh = hand(96)
    bw = W - 2 * M
    lines = _wrap(d, hand_text, fh, bw)[:2]
    lh = int(96 * 0.9)
    top_y = y = H - 76 - len(lines) * lh - (36 if sub else 0)
    for line in lines:
        lw = d.textlength(line, font=fh)
        d.text(((W - lw) // 2, y), line, font=fh, fill=(255, 250, 240))
        y += lh
    if sub:
        fs = body_font(28)
        sw = d.textlength(sub, font=fs)
        d.text(((W - sw) // 2, y + 8), sub, font=fs, fill=(228, 220, 206))
        y += 36

    img = img.convert("RGB")
    img.save(out)
    return {"photo": photo_key, "text_bbox": (M, top_y, W - M, y)}


def polaroid_note(note_hand, photo_key, acc, out, i=2, n=5, side="right",
                  angle=5, section=""):
    """Полароид под углом + рукописная заметка рядом со стрелкой-дудлом."""
    img = _paper_grain(Image.new("RGB", (W, H), PAPER))
    d = ImageDraw.Draw(img, "RGBA")
    if section:
        fl = e.font("SemiBold", 22)
        d.text((M, 58), section.upper(), font=fl, fill=MUTED)
    fst = e.font("SemiBold", 22)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=fst), 58), t, font=fst, fill=MUTED)

    img = img.convert("RGBA")
    card = _polaroid(photo_key, 460, 560)
    cx = W - M - card.width // 2 - 20 if side == "right" else M + card.width // 2 + 20
    _paste_rotated(img, card, cx, H // 2 - 40, angle)
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)

    tx = M if side == "right" else int(cx + card.width * 0.7)
    tw = (cx - card.width // 2 - 40 - M) if side == "right" else (W - M - tx)
    fh = hand(52, marck=True)
    lines = _wrap(d, note_hand, fh, max(160, tw))
    lh = int(52 * 1.1)
    top = ny = H // 2 - (len(lines) * lh) // 2
    for line in lines:
        d.text((tx, ny), line, font=fh, fill=INK)
        ny += lh

    img.save(out)
    return {"text_bbox": (tx, top, tx + max(160, tw), ny)}


def moodboard(caption, photo_a, photo_b, acc, out, i=3, n=5, section=""):
    """Мудборд-коллаж: два полароида вразнобой + короткая подпись между ними."""
    img = _paper_grain(Image.new("RGB", (W, H), PAPER))
    d = ImageDraw.Draw(img, "RGBA")
    if section:
        fl = e.font("SemiBold", 22)
        d.text((M, 58), section.upper(), font=fl, fill=MUTED)
    fst = e.font("SemiBold", 22)
    t = "%d/%d" % (i, n)
    d.text((W - M - d.textlength(t, font=fst), 58), t, font=fst, fill=MUTED)

    img = img.convert("RGBA")
    ca = _polaroid(photo_a, 380, 460)
    cb = _polaroid(photo_b, 380, 460)
    _paste_rotated(img, ca, M + ca.width // 2 + 10, 420, -6)
    _paste_rotated(img, cb, W - M - cb.width // 2 - 10, 640, 5)
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)

    fh = hand(56)
    bw = W - 2 * M
    lines = _wrap(d, caption, fh, bw)
    lh = int(56 * 1.05)
    top = y = 980
    for line in lines:
        lw = d.textlength(line, font=fh)
        d.text(((W - lw) // 2, y), line, font=fh, fill=INK)
        y += lh

    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}


def outro(hand_text, acc, out, i=5, n=5, sticker="heart", brand_line=""):
    """Финал: крупный рукописный призыв + «стикер» + подпись «листай ещё →»
    — мягкий CTA без давления, в духе личного дневника. `sticker` — имя
    вектора ("heart" пока единственный), не текстовый символ."""
    img = _paper_grain(Image.new("RGB", (W, H), PAPER))
    d = ImageDraw.Draw(img)

    bw = W - 2 * M
    fh = hand(76)
    lines = _wrap(d, hand_text, fh, bw)
    lh = int(76 * 0.95)
    top = y = (H - len(lines) * lh) // 2 - 40
    for line in lines:
        lw = d.textlength(line, font=fh)
        d.text(((W - lw) // 2, y), line, font=fh, fill=INK)
        y += lh

    if sticker == "heart":
        # Заливка — ТОЛЬКО acc: любой другой сплошной цвет тут падает
        # check_palette (единственный акцент карусели, без исключений для
        # декора) — тот же принцип, что у акцентной плашки в polaroid_note.
        _heart(d, W // 2, y + 40, 56, acc)
    y += 20 + 66

    fs = body_font(26)
    ct = "листай ещё →" if n > i else "сохрани, чтобы не потерять"
    cw = d.textlength(ct, font=fs)
    d.text(((W - cw) // 2, y), ct, font=fs, fill=MUTED)
    y += 40

    if brand_line:
        fb = e.font("SemiBold", 20)
        bt = brand_line.upper()
        bw2 = d.textlength(bt, font=fb)
        d.text(((W - bw2) // 2, H - 70), bt, font=fb, fill=MUTED)

    img.save(out)
    return {"text_bbox": (M, top, W - M, y)}
